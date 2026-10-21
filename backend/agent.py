"""ColdTrust backend: ingest endpoints, agent pipeline, and the dashboard API.

    uvicorn backend.agent:app --port 8000
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import approvals, auth as auth_mod, context, epcis, maintenance, report, trust
from .config import DEFAULT_SECRET, load
from .engine import Engine, STATUS_LABEL
from .llm import Explainer, agent_for
from .notify import Notifier
from .schemas import (ActorIn, ApprovalDecisionIn, CameraObservationIn, DemoMockIn, EvidenceIn, IngestIn, LoginIn, MaintenanceIn, OverrideIn, ReassignIn,
                      VehicleActionIn, VehicleIn)
from .seed import BACKUP_CUSTODIAN, GEOFENCES, ROUTE, VEHICLE, seed
from .store import Store

log = logging.getLogger("coldtrust")
cfg = load()

# CISA Secure by Design / CPG "change default passwords": never accept real device data with a shared default key.
if cfg.device_secret == DEFAULT_SECRET or len(cfg.device_secret) < 16:
    if cfg.strict_time:
        raise RuntimeError(
            "Refusing to start: COLDTRUST_SECRET is the default or shorter than 16 characters. Generate one with "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"`, put it in cold-chain/.env and in the "
            "firmware. (Simulator-only runs with --timelapse are allowed to use the default.)")
    log.warning("Using the default device secret. Fine for simulation; never for real hardware.")

auth = auth_mod.Auth.from_config(cfg)
if not auth.enabled:
    log.warning("OPEN ACCESS: no COLDTRUST_ADMIN_CODE is set, so anyone who can reach this server can use every control. "
                "Fine for a demo on your own PC; set the passwords in .env before sharing the dashboard.")

store = Store(cfg.db_path)
seed(store)
engine = Engine(store, cfg)
explainer = Explainer(cfg)
notifier = Notifier(cfg)
approval_queue = approvals.Queue(store)

WATCHDOG_EVERY_S = 15


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_watchdog())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="ColdTrust agent", version="0.1", lifespan=lifespan)
app.add_middleware(auth_mod.AuthMiddleware, auth=auth)


@app.exception_handler(RequestValidationError)
async def _log_rejected_request(request: Request, exc: RequestValidationError):
    """Say WHY a device or the dashboard was refused (a bare 422 in the access log hides the field)."""
    log.warning("422 %s %s: %s", request.method, request.url.path,
                "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']} (got {str(e.get('input'))[:40]})"
                          for e in exc.errors())[:400])
    return await request_validation_exception_handler(request, exc)
_sockets: set[WebSocket] = set()
_tasks: set[asyncio.Task] = set()  # keep references so background tasks are not garbage-collected


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def broadcast(message: dict[str, Any]) -> None:
    for ws in list(_sockets):
        try:
            await ws.send_json(message)
        except Exception:
            _sockets.discard(ws)


# ------------------------------------------------------------------ pipeline helpers

PHOTO_ALERTS = {"SUSPICIOUS_PERSON", "THEFT_SUSPECTED", "DOOR_AJAR"}


def _mock_tag() -> str:
    """Alerts raised while the node is sending mock readings must say so, or a demo reads like a real incident."""
    demo = engine.demo_state()
    mocked = [name for key, name in (("simulated_temp", "temperature"), ("simulated_light", "light")) if demo[key]]
    return f"[MOCK DATA: {' + '.join(mocked)}] " if mocked else ""


def _ai_message(entry: dict[str, Any], ai: dict[str, Any], tag: str, prefix: str) -> str:
    """The single Telegram message when the AI analysis is ready: what the rules decided, then the agent's explanation."""
    lines = [f"{tag}{prefix}[{entry['severity'].upper()}] {entry['kind']} ({ai['explained_by']})", ai["explanation"]]
    if ai.get("product_impact"):
        lines.append(f"Impact: {ai['product_impact']}")
    if ai.get("recommended_actions"):
        lines.append("Next: " + "; ".join(ai["recommended_actions"][:3]))
    status = f"Status: {entry['verdict_label']}"
    if entry.get("affected"):
        status += f" ({', '.join(entry['affected'][:6])}{' ...' if len(entry['affected']) > 6 else ''})"
    lines.append(status)
    lines += trust.data_check_line(entry.get("seal"))
    return "\n".join(lines)


async def _deliver(entry: dict[str, Any], ai: dict[str, Any] | None = None) -> None:
    """Out-of-band message to driver and admin (Telegram), ONE per alert: the AI analysis when `ai` is given, otherwise the
    rule-based text. Voice is played by the browser from entry['voice']. Person and door alerts carry the latest annotated
    camera frame when the camera is live."""
    prefix = {"reminder": "REMINDER, still unresolved. ", "escalated": "ESCALATED. ",
              "status_change": "STATUS CHANGE. "}.get(entry.get("notify_reason") or "", "")
    tag = _mock_tag()
    if ai:
        text = _ai_message(entry, ai, tag, prefix)
    else:
        text = f"{tag}{prefix}[{entry['severity'].upper()}] {entry['kind']}: {entry['reasoning']}"
        if entry.get("actions"):
            text += "\n" + "\n".join(entry["actions"][:4]) + (" ..." if len(entry["actions"]) > 4 else "")
        text += "".join("\n" + line for line in trust.data_check_line(entry.get("seal")))
    waiting = approval_queue.for_audit(entry["id"], engine.now()) if "id" in entry else []
    if waiting:
        text += "\nAwaiting admin approval: " + "; ".join(i["label"] for i in waiting)
    frame = _frame.get("jpeg") if time.time() - _frame.get("ts", 0) <= 15 else None
    if entry["kind"] in PHOTO_ALERTS and frame:
        await asyncio.to_thread(notifier.send_photo, frame, text)
    else:
        await asyncio.to_thread(notifier.send, text)


async def _notify_with_ai(entry: dict[str, Any]) -> None:
    """Wait a few seconds for the AI analysis and send it as THE alert message. If it is slow, off, paused or rejected by
    the number check, the rule-based text goes out instead. The dashboard log gets the AI text whenever it arrives."""
    refine = asyncio.ensure_future(_refine(entry))
    _tasks.add(refine)
    refine.add_done_callback(_tasks.discard)
    try:
        res = await asyncio.wait_for(asyncio.shield(refine), timeout=cfg.telegram_ai_wait_s)
    except asyncio.TimeoutError:
        res = None
    except Exception as exc:  # the AI path must never stop the alert from going out
        log.warning("AI explanation failed: %s", exc)
        res = None
    await _deliver(entry, res)


AI_MIN_GAP_S = 60
_ai_last: dict[str, float] = {}


def _ai_due(entry: dict[str, Any], now: float | None = None) -> bool:
    """New and escalated problems are always explained. Repeats of the same situation (status changes, reminders)
    within a minute keep their rule-based text, so a burst of events cannot burn the free-tier allowance."""
    now = time.time() if now is None else now
    key = entry.get("scenario") or entry.get("kind")
    if entry.get("notify_reason") in ("status_change", "reminder") and now - _ai_last.get(key, 0) < AI_MIN_GAP_S:
        return False
    _ai_last[key] = now
    return True


async def _refine(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The AI analysis of an alert, then the follow-up proposals for an admin to approve (AI-chosen when the Incident agent
    answered, otherwise the rules' defaults). Returns the AI answer, or None when there is none."""
    res = None
    try:
        res = await _explain(entry)
    finally:                      # the proposals must not depend on the AI being up
        try:
            await _propose(entry, res)
        except Exception:
            log.exception("could not queue proposals for #%s", entry.get("id"))
    return res


def _report_gate(entry: dict[str, Any]) -> tuple[bool, str]:
    """Temperature events only become incident reports when a maintenance review supports it; security events always may."""
    if entry.get("scenario") not in approvals.THERMAL:
        return True, ""
    return maintenance.report_gate(store.kv_get("maintenance_review"), entry["ts"])


AUTO_REPORT_KINDS = {"SUSPICIOUS_PERSON", "THEFT_SUSPECTED"}   # a person in the hold: the report is prepared at once
AUTO_REPORT_DELAY_S = 15       # lets the camera agent upload its evidence pictures first, so the report can embed them
AUTO_REPORT_GAP_S = 1800       # one automatic report per kind of alert per 30 minutes, not one per reminder
SYSTEM_REPORTER = {"name": "ColdTrust system (automatic draft)", "id": None, "role": "System", "auto": True}


_auto_inflight: set[str] = set()


def _wants_auto_report(entry: dict[str, Any]) -> bool:
    return entry.get("scenario") in AUTO_REPORT_KINDS and entry.get("notify_reason") in ("new", "escalated")


async def _auto_report(entry: dict[str, Any]) -> None:
    """Prepare the incident report for a suspicious person or suspected theft without waiting for anyone to press a button.
    The admin finds it ready to download; pressing "Prepare incident report" later makes a new version in their own name."""
    kind = entry["scenario"]
    if kind in _auto_inflight:
        return
    _auto_inflight.add(kind)
    try:
        await asyncio.sleep(AUTO_REPORT_DELAY_S)
        now = engine.now()
        done = store.kv_get("auto_reports", {})
        if now - done.get(kind, -AUTO_REPORT_GAP_S) < AUTO_REPORT_GAP_S:
            return
        await _make_report(entry["id"], SYSTEM_REPORTER)
        store.kv_set("auto_reports", {**store.kv_get("auto_reports", {}), kind: now})   # only after it worked, so a failure is retried
    except Exception:
        log.exception("automatic incident report failed for alert #%s", entry.get("id"))
    finally:
        _auto_inflight.discard(kind)


def _dispatch(entry: dict[str, Any]) -> None:
    """Send an alert on its way: ONE Telegram message (the AI analysis, or the rule text if it is late) and the dashboard log."""
    if _wants_auto_report(entry):
        _spawn(_auto_report(entry))
    if cfg.telegram_ai and notifier.enabled and explainer.enabled:
        _spawn(_notify_with_ai(entry))
    else:
        _spawn(_deliver(entry))
        _spawn(_refine(entry))              # the dashboard log still gets the AI text and the proposals


async def _propose(entry: dict[str, Any], res: dict[str, Any] | None) -> None:
    if entry.get("scenario") not in approvals.PROPOSAL_SCENARIOS:
        return
    menu = approvals.allowed(entry, store.list_packages(), store.kv_get("vehicle", {}), _report_gate(entry)[0])
    chosen = approvals.clean((res or {}).get("proposals"), menu)
    source = res["explained_by"] if chosen and res else "rules"
    if not chosen:
        chosen = approvals.rule_proposals(entry, menu)
    now = engine.now()
    added = approval_queue.add(entry, chosen, source, now)
    for item in added:
        logged = _human_entry("APPROVAL_PROPOSED", source, f"Proposed for admin approval: {item['label']}. {item['reason']}",
                              {"approval": item["id"], "action": item["action"], "alert": entry["id"]}, [])
        await broadcast({"type": "audit", "entry": logged})
    if added:
        await broadcast({"type": "approvals", "items": approval_queue.list(now)[-15:]})


async def _explain(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Replace the template explanation with the AI agent's once it arrives (status is never changed here)."""
    if not explainer.enabled or not _ai_due(entry):
        return None
    ctx = context.build(entry, store.list_packages(), store.kv_get("vehicle", {}), _report_gate(entry)[0])
    res, invented = await asyncio.to_thread(explainer.explain_checked, ctx, entry["kind"])
    if not res:
        if invented:  # say so in the log: the AI answered, but quoted figures that are not in the data
            doc = store.update_audit(entry["id"], {"ai_rejected": f"AI text rejected: {', '.join(invented)} not in the data; "
                                                                  "the rule-based text is kept"})
            if doc:
                await broadcast({"type": "audit_update", "entry": {"id": entry["id"], "ts": entry["ts"], **doc}})
        return None
    doc = store.update_audit(entry["id"], {
        "consulted": f"Seal agent: {entry['seal']['label']}" if entry.get("seal") else None,
        "reasoning": res["explanation"], "product_impact": res["product_impact"],
        "recommended_actions": res["recommended_actions"], "explanation_source": "llm", "explained_by": res["explained_by"]})
    if doc:
        await broadcast({"type": "audit_update", "entry": {"id": entry["id"], "ts": entry["ts"], **doc}})
    return res


async def _watchdog() -> None:
    """Raise an alert when the sensor node or GPS goes quiet (and again when it comes back)."""
    while True:
        await asyncio.sleep(WATCHDOG_EVERY_S)
        try:
            for result in engine.check_health(engine.now()):
                await broadcast({"type": "audit", "entry": result["entry"]})
                if result["notify"]:
                    _spawn(_deliver(result["entry"]))
        except Exception:  # the watchdog must never die quietly
            log.exception("watchdog check failed")


# ------------------------------------------------------------------ device ingest

async def _ingest(payload: IngestIn) -> dict[str, Any]:
    result = engine.ingest(payload)
    entry = result["entry"]
    await broadcast({"type": "audit", "entry": entry})
    if result["notify"]:
        if result["accepted"]:
            _dispatch(entry)
        else:
            _spawn(_deliver(entry))                 # a rejected payload is explained by rules only
    return {"accepted": result["accepted"], "reason": result.get("reason"), "entry": entry}


@app.post("/api/telemetry")
async def telemetry(payload: IngestIn) -> dict[str, Any]:
    """Routine batches and emergency pings share one pipeline; `ping_type` says which."""
    return await _ingest(payload)


@app.post("/api/event")
async def event(payload: IngestIn) -> dict[str, Any]:
    return await _ingest(payload)


# ------------------------------------------------------------------ context + camera

@app.post("/api/vehicle")
async def vehicle(v: VehicleIn) -> dict[str, Any]:
    data = v.model_dump(exclude_none=True)
    # A vehicle with any speed is driving, so its route is active: same as pressing "Activate Route". Only an idle
    # vehicle is switched on; Offline and Maintenance are deliberate choices and are left alone.
    speed = data.get("speed_kmh") or 0
    activate = speed > 0 and "route_status" not in data and (store.kv_get("vehicle", {}).get("route_status") or "Idle") == "Idle"
    if activate:
        data["route_status"] = "En route"
    cur = engine.set_vehicle(data)
    if activate and "lat" not in data:
        cur.update(authorised_stop=False, zone=None)  # it has left the depot, as with "Activate Route"
        store.kv_set("vehicle", cur)
    await broadcast({"type": "vehicle", "vehicle": cur})
    if activate:
        entry = _human_entry("VEHICLE_ACTION", "system", f"Route activated: the vehicle started moving ({speed:g} km/h).",
                             {"action": "activate", "speed_kmh": speed}, [])
        await broadcast({"type": "audit", "entry": entry})
    return cur


def _camera_auth(key: str | None) -> None:
    """Camera uploads can trigger alerts, so on real hardware they must carry the device key."""
    if cfg.strict_time and not hmac.compare_digest((key or "").encode(), cfg.device_secret.encode()):
        raise HTTPException(401, "camera key missing or wrong (send header X-ColdTrust-Key)")


@app.post("/api/camera/observation")
async def camera_observation(o: CameraObservationIn,
                             x_coldtrust_key: str | None = Header(default=None)) -> dict[str, Any]:
    _camera_auth(x_coldtrust_key)
    result = engine.person_observed(o.model_dump())
    if result:
        await broadcast({"type": "audit", "entry": result["entry"]})
        if result["notify"]:
            _dispatch(result["entry"])              # person alerts get the Incident agent and its proposals too
    return {"ok": True, "entry": result["entry"] if result else None}


# Live annotated frames from the camera agent (kept in memory only; evidence goes through /api/evidence).
_frame: dict[str, Any] = {}
FRAME_LIVE_S = 5


@app.post("/api/camera/frame")
async def camera_frame(request: Request, persons: int = 0, labels: str = "",
                       x_coldtrust_key: str | None = Header(default=None)) -> dict[str, bool]:
    _camera_auth(x_coldtrust_key)
    body = await request.body()
    if not body.startswith(b"\xff\xd8\xff") or len(body) > 2_000_000:
        raise HTTPException(400, "send one JPEG frame (max 2 MB) as the request body")
    _frame.update(jpeg=body, ts=time.time(), persons=max(0, min(persons, 20)),
                  labels=[x.strip()[:24] for x in labels.split(",") if x.strip()][:6])
    return {"ok": True}


def _feed_status() -> dict[str, Any]:
    ts = _frame.get("ts", 0)
    return {"live": bool(ts) and time.time() - ts <= FRAME_LIVE_S, "ts": ts or None,
            "persons": _frame.get("persons", 0), "labels": _frame.get("labels", [])}


@app.get("/api/camera/status")
async def camera_status() -> dict[str, Any]:
    return _feed_status()


@app.get("/api/camera/latest.jpg")
async def camera_latest():
    if not _frame.get("jpeg"):
        raise HTTPException(404, "no camera frame yet")
    return Response(_frame["jpeg"], media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/camera/stream")
async def camera_stream() -> StreamingResponse:
    """MJPEG stream of the newest annotated frames; works in a plain <img> tag."""
    async def frames():
        last = 0.0
        while True:
            ts = _frame.get("ts", 0)
            if ts > last and _frame.get("jpeg"):
                last = ts
                jpg = _frame["jpeg"]
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode()
                       + b"\r\n\r\n" + jpg + b"\r\n")
            await asyncio.sleep(0.08)
    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


_EVIDENCE_NAME = re.compile(r"^[0-9]{10,}_[0-9a-f]{12}\.jpg$")


@app.post("/api/evidence")
async def evidence(e: EvidenceIn, x_coldtrust_key: str | None = Header(default=None)) -> dict[str, Any]:
    _camera_auth(x_coldtrust_key)
    try:
        raw = base64.b64decode(e.jpeg_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "jpeg_base64 is not valid base64")
    if not raw.startswith(b"\xff\xd8\xff"):
        raise HTTPException(400, "not a JPEG")
    digest = hashlib.sha256(raw).hexdigest()
    now = engine.now()
    name = f"{int(now)}_{digest[:12]}.jpg"
    Path(cfg.evidence_dir).mkdir(parents=True, exist_ok=True)
    (Path(cfg.evidence_dir) / name).write_bytes(raw)

    where = store.kv_get("vehicle", {})
    entry = {
        "kind": "EVIDENCE_CAPTURED", "scenario": "EVIDENCE", "ping_type": "CAMERA", "trigger": e.trigger,
        "severity": "info", "device_id": e.device_id,
        "params": {"file": name, "sha256": digest, "bytes": len(raw), "detected": e.trigger,
                   "zone": where.get("zone"), "lat": where.get("lat"), "lon": where.get("lon"),
                   "speed_kmh": where.get("speed_kmh")},
        "findings": [], "verdict": "NORMAL", "verdict_label": "-", "risk_score": 0, "affected": [], "actions":
        [f"Frame stored, SHA-256 {digest[:16]}..."], "reasoning": "Evidence frame captured.",
        "recommended_actions": [], "explanation_source": "rules", "notified": False,
    }
    entry["id"] = store.add_audit(now, entry)
    entry["ts"] = now
    await broadcast({"type": "audit", "entry": entry})

    async def analyse() -> None:
        res = await asyncio.to_thread(explainer.describe_frame, e.jpeg_base64)
        if not res:
            return
        engine.camera_observation({"person_present": res["person_present"], "description": res["description"],
                                   "source": "vision"}, now)
        doc = store.update_audit(entry["id"], {
            "reasoning": res["description"], "explanation_source": "llm", "explained_by": res["explained_by"],
            "params": {**entry["params"], "person_present": res["person_present"],
                       "cargo_door_open": res["cargo_door_open"], "confidence": res["confidence"]}})
        if doc:
            await broadcast({"type": "audit_update", "entry": {"id": entry["id"], "ts": now, **doc}})

    if explainer.vision:
        _spawn(analyse())
    return {"stored": name, "sha256": digest, "entry": entry}


@app.get("/api/evidence/{name}")
async def evidence_file(name: str):
    if not _EVIDENCE_NAME.match(name):
        raise HTTPException(404)
    path = Path(cfg.evidence_dir) / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


# ------------------------------------------------------------------ dashboard API

@app.get("/api/state")
async def state() -> dict[str, Any]:
    return {**engine.state(), "fleet": VEHICLE, "geofences": GEOFENCES, "route": ROUTE, "camera_feed": _feed_status(),
            "llm": explainer.enabled, "llm_provider": explainer.provider_label, "llm_model": explainer.model,
            "vision": explainer.vision, "llm_usage": explainer.usage(), "telegram": notifier.enabled, "server_time": time.time(),
            "approvals": approval_queue.list(engine.now())[-15:], "report_gates": _report_gates()}


def _report_gates() -> dict[int, dict[str, Any]]:
    """For recent temperature alerts: may a report be made, and if not, why. Security alerts are not listed (always allowed)."""
    out = {}
    for e in store.audit_tail(150):
        if e.get("notified") and e.get("scenario") in approvals.THERMAL:
            ok, why = _report_gate(e)
            out[e["id"]] = {"ok": ok, "reason": why}
    return out


@app.get("/api/audit")
async def audit(limit: int = 100) -> list[dict[str, Any]]:
    return store.audit_tail(max(1, min(limit, 500)))


@app.get("/api/readings")
async def readings(minutes: int = 60) -> list[dict[str, float]]:
    until = store.last_sample_t(VEHICLE["device_id"]) or time.time()
    rows = store.recent_readings(VEHICLE["device_id"], max(1, min(minutes, 360)) * 60, until)
    return [{"t": r.t, "temp_c": r.temp_c, "hum": r.hum, "light_pct": round(r.light_pct, 1)} for r in rows]


def _maint_report(hours: int) -> dict[str, Any]:
    device = VEHICLE["device_id"]
    until = store.last_sample_t(device) or time.time()
    samples = store.recent_readings(device, max(1, min(hours, 168)) * 3600, until)
    return maintenance.report(samples, hours=hours, hold_limits=VEHICLE["hold_limits_c"], bright_pct=cfg.light_bright_pct,
                              tz_offset=cfg.tz_offset)


@app.get("/api/maintenance")
async def maintenance_report(hours: int = 24) -> dict[str, Any]:
    """Early-warning indicators for the last 6 h, 24 h or 7 days, overall and per day. Plain arithmetic, no AI."""
    return _maint_report(max(1, min(hours, 168)))


async def _offer_thermal_reports() -> list[dict[str, Any]]:
    """A maintenance review that finds the cooling unit may be failing is what justifies an incident report for the temperature
    event it covers. Queue "Prepare the incident report" for the latest such event of each kind that has no report yet, for an
    admin to approve. Returns the proposals now waiting (also shown under the review)."""
    review = store.kv_get("maintenance_review")
    now = engine.now()
    audit = store.audit_tail(300)
    reported = {e["params"].get("incident") for e in audit if e["kind"] == "REPORT_GENERATED"}
    latest: dict[str, dict[str, Any]] = {}
    for e in audit:
        if e.get("notified") and e.get("scenario") in approvals.THERMAL:
            latest[e["scenario"]] = e
    offers = []
    for e in latest.values():
        if e["id"] in reported or not maintenance.report_gate(review, e["ts"])[0]:
            continue
        reason = (f"The maintenance review ({review['by']}) found the cooling unit may be failing, so this temperature event "
                  "can be reported.")
        for item in approval_queue.add(e, [{"action": approvals.PREPARE_REPORT, "reason": reason}], "Maintenance review", now):
            logged = _human_entry("APPROVAL_PROPOSED", "Maintenance review", f"Proposed for admin approval: {item['label']}. {item['reason']}",
                                  {"approval": item["id"], "action": item["action"], "alert": e["id"]}, [])
            await broadcast({"type": "audit", "entry": logged})
        offers += [i for i in approval_queue.for_audit(e["id"], now) if i["action"] == approvals.PREPARE_REPORT]
    await broadcast({"type": "approvals", "items": approval_queue.list(now)[-15:]})
    return offers


MAINT_CACHE_S = 600
_maint_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@app.post("/api/maintenance/summary")
async def maintenance_summary(body: MaintenanceIn) -> dict[str, Any]:
    """The Thermal agent's written maintenance review. One AI call per press; the same numbers are answered from a
    10-minute cache, and with the AI off, paused or out of free calls the rule-based summary is returned instead."""
    rep = _maint_report(body.hours)
    key = hashlib.sha256(json.dumps(rep, sort_keys=True, default=str).encode()).hexdigest()
    hit = _maint_cache.get(key)
    if hit and time.time() - hit[0] < MAINT_CACHE_S:
        return {**hit[1], "cached": True, "usage": explainer.usage()}
    res, note = None, None
    if not explainer.enabled:
        note = "The AI agents are off (no API key), so this is the rule-based summary."
    elif not rep["enough_data"]:
        note = "Not enough history to ask the AI, so this is the rule-based summary."
    else:
        res, invented = await asyncio.to_thread(explainer.maintenance_checked, rep)
        if res is None:
            reason = explainer.guard.blocked_reason
            note = (f"The AI text was rejected ({', '.join(invented)} not in the data), so this is the rule-based summary."
                    if invented else
                    f"The AI is paused ({reason}), so this is the rule-based summary." if reason
                    else "The AI did not give a usable answer, so this is the rule-based summary.")
    summary = {k: res[k] for k in ("assessment", "cooling_unit", "urgency", "evidence", "recommended_actions")} if res \
        else maintenance.rule_summary(rep)
    out = {"source": "ai" if res else "rules", "agent": res["explained_by"] if res else None, "summary": summary,
           "note": note, "hours": body.hours, "cached": False, "usage": explainer.usage()}
    if res:
        _maint_cache[key] = (time.time(), {k: v for k, v in out.items() if k not in ("usage", "cached")})
    # Remember the latest review: it decides whether a temperature event may be turned into an incident report (maintenance.report_gate)
    store.kv_set("maintenance_review", {
        "ts": time.time(), "source": out["source"], "by": out["agent"], "hours": body.hours, "from": rep["from"], "to": rep["to"],
        "enough_data": rep["enough_data"], "cooling_unit": summary.get("cooling_unit"), "urgency": summary.get("urgency"),
        "assessment": summary.get("assessment")})
    out["report_offers"] = await _offer_thermal_reports()
    return out


# ------------------------------------------------------------------ login, roles, identity

def _session(request: Request) -> dict[str, Any] | None:
    return request.scope.get("state", {}).get("user")


def _actor(request: Request, claimed: str) -> str:
    """Who did it. With login on, the name comes from the signed session; whatever the request says is ignored."""
    user = _session(request)
    return f"{user['n']} ({user['u']}, {user['r']})" if user else claimed


def _headers(request: Request) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in request.scope.get("headers", [])}


def _secure_cookie(request: Request) -> bool:
    peer = request.client.host if request.client else None
    forwarded = _headers(request).get("x-forwarded-proto", "") if peer in ("127.0.0.1", "::1") else ""
    return request.url.scheme == "https" or forwarded == "https"


@app.get("/api/me")
async def me(request: Request) -> dict[str, Any]:
    if not auth.enabled:
        return {"auth": False, "role": "admin", "name": None}
    user = _session(request)
    return {"auth": True, "role": user["r"] if user else None, "name": user["n"] if user else None,
            "id": user["u"] if user else None, "expires": user["e"] if user else None}


@app.post("/api/login")
async def login(body: LoginIn, request: Request) -> JSONResponse:
    if not auth.enabled:
        raise HTTPException(400, "login is not set up (open access): set COLDTRUST_ADMIN_CODE in .env")
    ip = auth_mod.client_ip(request.client.host if request.client else None, _headers(request))
    wait = auth.limiter.retry_after(ip)
    if wait:
        raise HTTPException(429, f"too many wrong passwords; try again in {wait} s", headers={"Retry-After": str(wait)})
    cid = auth_mod.clean_id(body.company_id)
    if len(cid) < 3:
        raise HTTPException(422, "enter your company ID")
    user = auth.authenticate(cid, body.code)
    if user is None:
        locked = auth.limiter.fail(ip)
        entry = _human_entry("LOGIN_FAILED", "unknown", f"Failed login for company ID '{cid}' from {ip}"
                             + (": locked out for 5 minutes after 5 wrong passwords." if locked else "."),
                             {"ip": ip, "company_id": cid, "locked": locked}, [], severity="warning")
        await broadcast({"type": "audit", "entry": entry})
        if locked and notifier.enabled:
            _spawn(asyncio.to_thread(notifier.send, f"SECURITY: 5 wrong passwords from {ip}. Login is locked for that address "
                                                    "for 5 minutes."))
        await asyncio.sleep(0.3)  # slows down guessing
        raise HTTPException(401, "wrong company ID or password")
    auth.limiter.ok(ip)
    token, data = auth.issue(user)
    who = f"{user.name} ({user.id}, {user.role})"
    entry = _human_entry("LOGIN_OK", who, f"{user.name} ({user.id}) signed in as {user.role}.", {"ip": ip, "role": user.role}, [])
    await broadcast({"type": "audit", "entry": entry})
    resp = JSONResponse({"id": user.id, "name": user.name, "role": user.role, "expires": data["e"]})
    resp.headers["set-cookie"] = auth_mod.cookie_header(token, int(auth.ttl), _secure_cookie(request))
    return resp


@app.post("/api/logout")
async def logout(request: Request) -> JSONResponse:
    user = _session(request)
    if user:
        auth.revoke(user)
        entry = _human_entry("LOGOUT", f"{user['n']} ({user['u']}, {user['r']})", f"{user['n']} ({user['u']}) signed out.", {}, [])
        await broadcast({"type": "audit", "entry": entry})
    resp = JSONResponse({"ok": True})
    resp.headers["set-cookie"] = auth_mod.clear_cookie_header(_secure_cookie(request))
    return resp


@app.post("/api/package/{pkg_id}/override")
async def override(pkg_id: str, body: OverrideIn, request: Request) -> dict[str, Any]:
    entry = engine.override(pkg_id, body.status, body.reason, _actor(request, body.supervisor), engine.now())
    if not entry:
        raise HTTPException(404, "unknown package")
    await broadcast({"type": "audit", "entry": entry})
    return {"entry": entry, "package": store.get_package(pkg_id)}


def _human_entry(kind: str, by: str, reasoning: str, params: dict[str, Any], actions: list[str],
                 affected: list[str] | None = None, severity: str = "info") -> dict[str, Any]:
    now = engine.now()
    entry = {"kind": kind, "scenario": kind, "ping_type": "MANUAL", "trigger": None, "severity": severity,
             "device_id": None, "params": {"by": by, **params}, "findings": [], "verdict": "NONE",
             "verdict_label": "NO CHANGE", "risk_score": 0, "affected": affected or [], "actions": actions,
             "reasoning": reasoning, "recommended_actions": [], "explanation_source": "human", "notified": False}
    entry["id"] = store.add_audit(now, entry)
    entry["ts"] = now
    return entry


@app.post("/api/audit/{audit_id}/ack")
async def acknowledge(audit_id: int, body: ActorIn, request: Request) -> dict[str, Any]:
    """Someone has seen the alert and is acting on it (stops reminders being needed)."""
    original = store.audit_get(audit_id)
    if not original:
        raise HTTPException(404, "unknown alert")
    who = _actor(request, body.by)
    entry = _human_entry("ALERT_ACKNOWLEDGED", who, f"{who} acknowledged {original['kind']} (#{audit_id}).",
                         {"alert": audit_id}, [])
    doc = store.update_audit(audit_id, {"acked_by": who, "acked_at": entry["ts"]})
    await broadcast({"type": "audit", "entry": entry})
    if doc:
        await broadcast({"type": "audit_update", "entry": {"id": audit_id, "ts": original["ts"], **doc}})
    return {"entry": entry}


@app.post("/api/package/{pkg_id}/reassign")
async def reassign(pkg_id: str, body: ReassignIn, request: Request) -> dict[str, Any]:
    pk = store.get_package(pkg_id)
    if not pk:
        raise HTTPException(404, "unknown package")
    pk["reassign_to"] = body.truck
    store.upsert_package(pk)
    who = _actor(request, body.by)
    entry = _human_entry("REASSIGN_REQUESTED", who,
                         f"{who} asked to move {pkg_id} to {body.truck}: {body.reason}",
                         {"package": pkg_id, "truck": body.truck},
                         [f"{pkg_id}: transfer to {body.truck} requested; check its capacity and temperature limits"],
                         [pkg_id])
    await broadcast({"type": "audit", "entry": entry})
    return {"entry": entry, "package": pk}


@app.post("/api/vehicle/action")
async def vehicle_action(body: VehicleActionIn, request: Request) -> dict[str, Any]:
    user = _session(request)
    if auth.enabled and body.action in auth_mod.ADMIN_ONLY_VEHICLE_ACTIONS and (not user or user["r"] != "admin"):
        raise HTTPException(403, "admin only")          # a driver may activate the route, not take the vehicle offline
    who = _actor(request, body.by)
    label = {"maintenance": "Maintenance requested", "offline": "Offline", "activate": "En route"}[body.action]
    cur = engine.set_vehicle({"route_status": label})
    if body.action == "activate":
        # The truck has left the depot: it is no longer at an authorised stop until GPS or a dock says so.
        cur.update(authorised_stop=False, zone=None)
        store.kv_set("vehicle", cur)
    entry = _human_entry("VEHICLE_ACTION", who, f"{who}: {label}.", {"action": body.action}, [])
    await broadcast({"type": "vehicle", "vehicle": cur})
    await broadcast({"type": "audit", "entry": entry})
    return {"vehicle": cur, "entry": entry}


@app.get("/api/approvals")
async def list_approvals() -> list[dict[str, Any]]:
    return approval_queue.list(engine.now())[-30:]


def _reporter(request: Request, claimed: str) -> dict[str, Any]:
    """Whoever pressed the button, from the signed session (open mode: the name the page sent)."""
    user = _session(request)
    return {"name": user["n"], "id": user["u"], "role": user["r"]} if user else {"name": claimed, "id": None, "role": "admin"}


async def _make_report(audit_id: int, reporter: dict[str, Any]) -> dict[str, Any]:
    """Fill the template for one alert, save it, and put the file's SHA-256 in the audit log."""
    wording = None
    entry = store.audit_get(audit_id)
    if entry is None:
        raise KeyError(audit_id)
    allowed, why = _report_gate(entry)
    if not allowed:                     # before any AI call is spent
        raise report.ReportNotAllowed(why)
    if explainer.enabled:               # one small AI call, only now; any failure leaves the report on its rule-based sentences
        ctx = context.build(entry, store.list_packages(), store.kv_get("vehicle", {}))
        wording = await asyncio.to_thread(explainer.report_text, ctx)
    review = store.kv_get("maintenance_review")
    info = await asyncio.to_thread(report.generate, cfg, store, audit_id, reporter, wording, review)
    who = f"{reporter['name']} ({reporter['id']}, {reporter['role']})" if reporter.get("id") else reporter["name"]
    entry = _human_entry(
        "REPORT_GENERATED", who, f"{who} {'prepared automatically' if reporter.get('auto') else 'prepared'} incident report "
        f"{info['report_number']} for alert #{audit_id} ({info['pictures']} picture(s) embedded).",
        {"file": info["file"], "sha256": info["sha256"], "incident": audit_id, "report": info["report_number"],
         "pictures": info["pictures"], "automatic": bool(reporter.get("auto"))}, [f"Saved as {info['file']}, SHA-256 {info['sha256'][:16]}…"])
    await broadcast({"type": "audit", "entry": entry})
    return {**info, "entry": entry, "url": f"/api/reports/{info['file']}"}


def _report_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "unknown alert")
    if isinstance(exc, report.ReportNotAllowed):
        return HTTPException(409, str(exc))
    if isinstance(exc, report.NotAnIncident):
        return HTTPException(422, str(exc))
    return HTTPException(500, f"the report template could not be used: {exc}")


@app.post("/api/incidents/{audit_id}/report")
async def incident_report(audit_id: int, body: ActorIn, request: Request) -> dict[str, Any]:
    """Admin only. The person who presses the button is the reporter named in the report."""
    try:
        return await _make_report(audit_id, _reporter(request, body.by))
    except (KeyError, report.NotAnIncident, OSError, ValueError) as exc:
        raise _report_errors(exc)


@app.get("/api/reports/{name}")
async def report_file(name: str):
    path = Path(cfg.reports_dir) / name
    if not report.REPORT_NAME.match(name) or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


async def _execute(item: dict[str, Any], who: str, reporter: dict[str, Any]) -> list[str]:
    """Carry out an approved proposal with the same code paths as the manual controls. Only the four menu actions exist."""
    now = engine.now()
    action = item["action"]
    if action == approvals.QUARANTINE:
        done = []
        for pk in store.list_packages():
            if pk["status"] != "QUARANTINED":
                entry = engine.override(pk["id"], "QUARANTINED", f"approved by {who}: {item['label']} (proposal #{item['id']})",
                                        who, now)
                if entry:
                    await broadcast({"type": "audit", "entry": entry})
                    done.append(pk["id"])
        return [f"{len(done)} package(s) quarantined; custody tokens revoked"] if done else ["nothing left to quarantine"]
    if action == approvals.OFFLINE:
        cur = engine.set_vehicle({"route_status": "Offline"})
        await broadcast({"type": "vehicle", "vehicle": cur})
        return ["vehicle marked offline"]
    if action == approvals.DIVERT:
        text = (f"{_mock_tag()}DIVERT: go to the backup cold store, {BACKUP_CUSTODIAN}. Approved by {who} for alert "
                f"#{item['audit_id']} ({item['kind']}).")
        if notifier.enabled:
            _spawn(asyncio.to_thread(notifier.send, text))
        return [f"driver told to divert to {BACKUP_CUSTODIAN}" + ("" if notifier.enabled else " (Telegram off: dashboard only)")]
    try:                                           # PREPARE_REPORT: the approving admin is the reporter
        info = await _make_report(item["audit_id"], reporter)
        return [f"report {info['report_number']} saved as {info['file']}"]
    except (KeyError, report.NotAnIncident, OSError, ValueError) as exc:
        return [f"report not prepared: {_report_errors(exc).detail}"]


@app.post("/api/approvals/{approval_id}/decide")
async def decide_approval(approval_id: int, body: ApprovalDecisionIn, request: Request) -> dict[str, Any]:
    """Admin only (see auth.POLICY). Approving carries the proposal out; rejecting just closes it. Both are logged."""
    who = _actor(request, body.by)
    item = approval_queue.decide(approval_id, body.approve, who, engine.now(), body.note)
    if not item:
        raise HTTPException(409, "this proposal does not exist or is no longer waiting for a decision")
    actions = await _execute(item, who, _reporter(request, body.by)) if body.approve else []
    verb = "approved" if body.approve else "rejected"
    entry = _human_entry("APPROVAL_DECIDED", who, f"{who} {verb}: {item['label']} (proposal #{item['id']}, alert #{item['audit_id']})."
                         + (f" Note: {body.note}" if body.note else ""),
                         {"approval": item["id"], "action": item["action"], "decision": verb, "alert": item["audit_id"]},
                         actions, severity="warning" if body.approve else "info")
    await broadcast({"type": "audit", "entry": entry})
    await broadcast({"type": "approvals", "items": approval_queue.list(engine.now())[-15:]})
    return {"approval": item, "entry": entry}


@app.get("/api/custody/{token}")
async def custody(token: str) -> dict[str, Any]:
    """What a receiving scanner calls before accepting a package."""
    pk = store.package_by_token(token)
    if not pk:
        return {"valid": False, "reason": "unknown or superseded token"}
    if not pk["token_valid"]:
        return {"valid": False, "package": pk["id"], "reason": STATUS_LABEL[pk["status"]]}
    return {"valid": True, "package": pk["id"], "status": pk["status"]}


# ------------------------------------------------------------------ GS1 EPCIS 2.0

def _created() -> str:
    return epcis.utc_millis(time.time())


@app.get("/api/epcis")
async def epcis_document() -> JSONResponse:
    """The whole trip as an EPCIS 2.0 JSON-LD document any EPCIS repository can ingest."""
    return JSONResponse(epcis.document(store.list_epcis(), _created()), media_type="application/ld+json")


@app.get("/api/epcis/events")
async def epcis_events(EQ_bizStep: str | None = None, EQ_disposition: str | None = None, MATCH_epc: str | None = None,
                       GE_eventTime: str | None = None, LT_eventTime: str | None = None,
                       eventID: str | None = None) -> JSONResponse:
    """EPCIS 2.0 query subset, e.g. ?EQ_disposition=non_conformant or ?MATCH_epc=<SSCC Digital Link>."""
    q = {"EQ_bizStep": EQ_bizStep, "EQ_disposition": EQ_disposition, "MATCH_epc": MATCH_epc,
         "GE_eventTime": GE_eventTime, "LT_eventTime": LT_eventTime, "eventID": eventID}
    events = epcis.query_events(store.list_epcis(), q)
    return JSONResponse(epcis.query_document(events, _created()), media_type="application/ld+json")


@app.get("/api/epcis/verify")
async def epcis_verify() -> dict[str, Any]:
    """Recompute every Event Hash ID; any record edited after capture shows up as invalid."""
    results = epcis.verify_events(store.list_epcis())
    bad = [r["eventID"] for r in results if not r["valid"]]
    return {"events": len(results), "all_valid": not bad, "invalid": bad}


# ------------------------------------------------------------------ demo: mock temperature

@app.get("/api/demo")
async def demo_get() -> dict[str, Any]:
    """The node polls this: if `mock` is true it reports `temp_c` instead of reading its sensor."""
    return engine.demo_state()


@app.post("/api/demo")
async def demo_set(body: DemoMockIn, request: Request) -> dict[str, Any]:
    if not cfg.demo_enabled:
        raise HTTPException(403, "mock temperature is off; start the backend with --demo")
    before = engine.demo_state()
    state = engine.set_demo_mock(body.mock, body.temp_c, body.light_mock, body.light_pct)
    if body.log:
        bits = []
        if body.mock is not None:
            bits.append(f"set the MOCK temperature to {state['temp_c']:.0f} °C" if body.mock and before["mock"] else
                        f"turned MOCK temperature on, set to {state['temp_c']:.0f} °C" if body.mock else
                        "turned mock temperature off; the node reports its real sensor again")
        if body.light_mock is not None:
            bits.append(f"set the MOCK light to {state['light_pct']:.0f} %" if body.light_mock and before["light_mock"] else
                        f"turned MOCK light on, set to {state['light_pct']:.0f} %" if body.light_mock else
                        "turned mock light off; the node reports its real light sensor again")
        who = _actor(request, body.by)
        entry = _human_entry("DEMO_MODE", who, f"{who} {' and '.join(bits)}. Mock readings are labelled as mock.",
                             {"mock": body.mock, "temp_c": state["temp_c"], "light_mock": body.light_mock,
                              "light_pct": state["light_pct"]}, [])
        await broadcast({"type": "audit", "entry": entry})
    await broadcast({"type": "demo", "demo": state})
    return state


@app.get("/api/config")
async def config() -> dict[str, Any]:
    """Thresholds the firmware mirrors; handy for keeping the two in sync."""
    return {"rate_c_per_min": cfg.rate_limit_c_per_min, "step_c": cfg.step_change_c,
            "step_window_s": cfg.step_window_s, "light_bright_pct": cfg.light_bright_pct,
            "hold_limits_c": VEHICLE["hold_limits_c"], "log_interval_s": 30, "routine_ping_s": 900}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "llm": explainer.enabled, "telegram": notifier.enabled, "auth": auth.enabled}


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    _sockets.add(socket)
    try:
        while True:
            await socket.receive_text()  # keep-alive; clients send nothing meaningful
    except WebSocketDisconnect:
        pass
    finally:
        _sockets.discard(socket)


class _RevalidatingStatic(StaticFiles):
    """Dashboard files: browsers must re-check them, so an update is never hidden behind a stale cache."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


_dash = Path(__file__).resolve().parent.parent / "dashboard"
if (_dash / "index.html").is_file():
    app.mount("/", _RevalidatingStatic(directory=_dash, html=True), name="dashboard")
