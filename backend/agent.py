"""ColdTrust backend: ingest endpoints, agent pipeline, and the dashboard API.

    uvicorn backend.agent:app --port 8000
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
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

from . import epcis
from .config import DEFAULT_SECRET, load
from .engine import Engine, STATUS_LABEL
from .llm import Explainer
from .notify import Notifier
from .schemas import (ActorIn, CameraObservationIn, DemoMockIn, EvidenceIn, IngestIn, OverrideIn, ReassignIn,
                      VehicleActionIn, VehicleIn)
from .seed import GEOFENCES, ROUTE, VEHICLE, seed
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

store = Store(cfg.db_path)
seed(store)
engine = Engine(store, cfg)
explainer = Explainer(cfg)
notifier = Notifier(cfg)

WATCHDOG_EVERY_S = 15


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_watchdog())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="ColdTrust agent", version="0.1", lifespan=lifespan)


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


async def _deliver(entry: dict[str, Any]) -> None:
    """Out-of-band message to driver and admin (Telegram). Voice is played by the browser from entry['voice'].
    Person and door alerts carry the latest annotated camera frame when the camera is live."""
    prefix = {"reminder": "REMINDER, still unresolved. ", "escalated": "ESCALATED. ",
              "status_change": "STATUS CHANGE. "}.get(entry.get("notify_reason") or "", "")
    # Alerts raised while the node is sending mock readings must say so, or a demo reads like a real incident.
    demo = engine.demo_state()
    mocked = [name for key, name in (("simulated_temp", "temperature"), ("simulated_light", "light")) if demo[key]]
    tag = f"[MOCK DATA: {' + '.join(mocked)}] " if mocked else ""
    text = f"{tag}{prefix}[{entry['severity'].upper()}] {entry['kind']}: {entry['reasoning']}"
    if entry.get("actions"):
        text += "\n" + "\n".join(entry["actions"][:4]) + (" ..." if len(entry["actions"]) > 4 else "")
    frame = _frame.get("jpeg") if time.time() - _frame.get("ts", 0) <= 15 else None
    if entry["kind"] in PHOTO_ALERTS and frame:
        await asyncio.to_thread(notifier.send_photo, frame, text)
    else:
        await asyncio.to_thread(notifier.send, text)


async def _refine(entry: dict[str, Any]) -> None:
    """Replace the template explanation with the AI agent's once it arrives (status is never changed here)."""
    if not explainer.enabled:
        return
    packages = {p["id"]: p for p in store.list_packages()}
    ctx = {
        "scenario": entry["scenario"], "severity": entry["severity"], "trigger": entry.get("trigger"),
        "findings": entry["findings"], "readings": entry["params"],
        "vehicle": {k: v for k, v in store.kv_get("vehicle", {}).items() if k in ("speed_mph", "zone", "authorised_stop")},
        "status_decided_by_rules": entry["verdict_label"], "risk_score": entry["risk_score"],
        "products": [
            {"id": pid, "description": pk["description"], "limits_c": [pk["temp_min_c"], pk["temp_max_c"]],
             "excursion_budget_min": pk["excursion_budget_min"], "minutes_outside_limits": pk["excursion_min"],
             "status": pk["status"], "risk_score": pk["risk_score"]}
            for pid, pk in packages.items() if pid in entry["affected"] or pk["status"] != "NORMAL"][:6],
    }
    res = await asyncio.to_thread(explainer.explain, ctx, entry["kind"])
    if not res:
        return
    doc = store.update_audit(entry["id"], {
        "reasoning": res["explanation"], "product_impact": res["product_impact"],
        "recommended_actions": res["recommended_actions"], "explanation_source": "llm", "explained_by": res["explained_by"]})
    if doc:
        await broadcast({"type": "audit_update", "entry": {"id": entry["id"], "ts": entry["ts"], **doc}})


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
        _spawn(_deliver(entry))
    if result["accepted"] and result["notify"]:
        _spawn(_refine(entry))
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
    cur = engine.set_vehicle(v.model_dump(exclude_none=True))
    await broadcast({"type": "vehicle", "vehicle": cur})
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
            _spawn(_deliver(result["entry"]))
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
                   "speed_mph": where.get("speed_mph")},
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
            "vision": explainer.vision, "llm_usage": explainer.usage(), "telegram": notifier.enabled, "server_time": time.time()}


@app.get("/api/audit")
async def audit(limit: int = 100) -> list[dict[str, Any]]:
    return store.audit_tail(max(1, min(limit, 500)))


@app.get("/api/readings")
async def readings(minutes: int = 60) -> list[dict[str, float]]:
    until = store.last_sample_t(VEHICLE["device_id"]) or time.time()
    rows = store.recent_readings(VEHICLE["device_id"], max(1, min(minutes, 360)) * 60, until)
    return [{"t": r.t, "temp_c": r.temp_c, "hum": r.hum, "light_pct": round(r.light_pct, 1)} for r in rows]


@app.post("/api/package/{pkg_id}/override")
async def override(pkg_id: str, body: OverrideIn) -> dict[str, Any]:
    entry = engine.override(pkg_id, body.status, body.reason, body.supervisor, engine.now())
    if not entry:
        raise HTTPException(404, "unknown package")
    await broadcast({"type": "audit", "entry": entry})
    return {"entry": entry, "package": store.get_package(pkg_id)}


def _human_entry(kind: str, by: str, reasoning: str, params: dict[str, Any], actions: list[str],
                 affected: list[str] | None = None) -> dict[str, Any]:
    now = engine.now()
    entry = {"kind": kind, "scenario": kind, "ping_type": "MANUAL", "trigger": None, "severity": "info",
             "device_id": None, "params": {"by": by, **params}, "findings": [], "verdict": "NONE",
             "verdict_label": "NO CHANGE", "risk_score": 0, "affected": affected or [], "actions": actions,
             "reasoning": reasoning, "recommended_actions": [], "explanation_source": "human", "notified": False}
    entry["id"] = store.add_audit(now, entry)
    entry["ts"] = now
    return entry


@app.post("/api/audit/{audit_id}/ack")
async def acknowledge(audit_id: int, body: ActorIn) -> dict[str, Any]:
    """Someone has seen the alert and is acting on it (stops reminders being needed)."""
    original = store.audit_get(audit_id)
    if not original:
        raise HTTPException(404, "unknown alert")
    entry = _human_entry("ALERT_ACKNOWLEDGED", body.by, f"{body.by} acknowledged {original['kind']} (#{audit_id}).",
                         {"alert": audit_id}, [])
    doc = store.update_audit(audit_id, {"acked_by": body.by, "acked_at": entry["ts"]})
    await broadcast({"type": "audit", "entry": entry})
    if doc:
        await broadcast({"type": "audit_update", "entry": {"id": audit_id, "ts": original["ts"], **doc}})
    return {"entry": entry}


@app.post("/api/package/{pkg_id}/reassign")
async def reassign(pkg_id: str, body: ReassignIn) -> dict[str, Any]:
    pk = store.get_package(pkg_id)
    if not pk:
        raise HTTPException(404, "unknown package")
    pk["reassign_to"] = body.truck
    store.upsert_package(pk)
    entry = _human_entry("REASSIGN_REQUESTED", body.by,
                         f"{body.by} asked to move {pkg_id} to {body.truck}: {body.reason}",
                         {"package": pkg_id, "truck": body.truck},
                         [f"{pkg_id}: transfer to {body.truck} requested; check its capacity and temperature limits"],
                         [pkg_id])
    await broadcast({"type": "audit", "entry": entry})
    return {"entry": entry, "package": pk}


@app.post("/api/vehicle/action")
async def vehicle_action(body: VehicleActionIn) -> dict[str, Any]:
    label = {"maintenance": "Maintenance requested", "offline": "Offline", "activate": "En route"}[body.action]
    cur = engine.set_vehicle({"route_status": label})
    if body.action == "activate":
        # The truck has left the depot: it is no longer at an authorised stop until GPS or a dock says so.
        cur.update(authorised_stop=False, zone=None)
        store.kv_set("vehicle", cur)
    entry = _human_entry("VEHICLE_ACTION", body.by, f"{body.by}: {label}.", {"action": body.action}, [])
    await broadcast({"type": "vehicle", "vehicle": cur})
    await broadcast({"type": "audit", "entry": entry})
    return {"vehicle": cur, "entry": entry}


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
async def demo_set(body: DemoMockIn) -> dict[str, Any]:
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
        entry = _human_entry("DEMO_MODE", body.by, f"{body.by} {' and '.join(bits)}. Mock readings are labelled as mock.",
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
    return {"ok": True, "llm": explainer.enabled, "telegram": notifier.enabled}


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
