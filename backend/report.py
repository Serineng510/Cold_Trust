"""Incident report: turns one alert from the audit log into the data for templates/logistics-incident-report.docx.

Everything factual (times, place, people, cargo, exposure figures, actions taken, evidence files and their hashes) comes from
the stored records, not from the AI. The AI only supplies the wording of two fields, the description and the suspected cause,
written in the same call as the alert analysis and number-checked there; when there is none, a rule-based sentence is used.
What the system cannot know (cargo damage, vehicle damage, injuries, signatures) is left for a person and says so.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import approvals, docfill, maintenance as maint
from .config import Settings
from .maintenance import tz_from_offset
from .seed import VEHICLE
from .store import Store

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_WINDOW_S = 900          # pictures taken within 15 min of the alert belong to it
MAX_PICTURES = 4
NOT_SET = "not set"
REPORT_NAME = re.compile(r"^INC-\d{8}-\d+(?:-r\d+)?\.docx$")

TYPE = {
    "THEFT_SUSPECTED": "Suspected theft or unauthorised access to the cargo hold",
    "SUSPICIOUS_PERSON": "Unauthorised person in the cargo hold",
    "SPOOF_SUSPECTED": "Suspected tampering with the sensor data",
    "DOOR_AJAR": "Cargo door opened outside an authorised stop",
    "THERMAL_EXCURSION": "Temperature excursion (cargo too warm)",
    "FREEZE_RISK": "Temperature excursion (cargo too cold)",
    "DATA_TRUST": "Unreliable sensor data",
}
CAUSE = {
    "THEFT_SUSPECTED": "Suspected unauthorised entry: the cargo hold was lit and a person was seen in it outside an authorised "
                       "stop. This is not confirmed and needs investigation.",
    "SUSPICIOUS_PERSON": "A person was seen in the cargo hold outside an authorised stop. Who they were and why is not known.",
    "SPOOF_SUSPECTED": "The sensor readings contradict each other. Possible causes are a faulty or tampered sensor, or replayed "
                       "data; none is confirmed.",
    "DOOR_AJAR": "The cargo door was opened outside an authorised stop. The reason is not known.",
    "THERMAL_EXCURSION": "The hold temperature rose outside the products' limits. Possible causes include a weakening cooling "
                         "unit or an open door; none is confirmed.",
    "FREEZE_RISK": "The hold temperature fell below the products' limits. The cause is not confirmed.",
    "DATA_TRUST": "The sensor data shows signs of being static, replayed or wrong. The cause is not confirmed.",
}


class NotAnIncident(ValueError):
    pass


class ReportNotAllowed(ValueError):
    """A temperature event whose maintenance review does not show a cooling-unit problem (see maintenance.report_gate)."""


def _when(cfg: Settings, ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz_from_offset(cfg.tz_offset))


def _clock(cfg: Settings, ts: float) -> str:
    return _when(cfg, ts).strftime("%H:%M:%S")


def incident_type(entry: dict[str, Any]) -> str:
    kind = entry["kind"]
    if kind.startswith("REJECTED_"):
        return "Rejected device message (possible replay or forgery)"
    return TYPE.get(entry.get("scenario")) or TYPE.get(kind) or kind.replace("_", " ").capitalize()


def _location(entry: dict[str, Any], vehicle: dict[str, Any]) -> str:
    p = entry.get("params") or {}
    zone, lat, lon = p.get("zone") or vehicle.get("zone"), p.get("lat") or vehicle.get("lat"), p.get("lon") or vehicle.get("lon")
    gps = f"GPS {lat:.4f}, {lon:.4f}" if lat is not None and lon is not None else "GPS position not available"
    route = VEHICLE["route"]
    return (f"{zone or 'On the road, outside any listed zone'} ({gps}). Vehicle {VEHICLE['id']} ({VEHICLE['model']}), "
            f"route {route['from']} to {route['to']}.")


def _people(cfg: Settings, reporter: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, str]]:
    driver = {"name": f"{cfg.driver_name or VEHICLE['driver_name']} ({cfg.driver_id or VEHICLE['driver_id']})",
              "role": "Driver", "contactInfo": cfg.driver_contact or NOT_SET}
    if reporter.get("auto"):          # prepared by the system the moment the alert was raised: no person has reviewed it yet
        admin = cfg.admin_name or cfg.admin_id
        people = [driver] + ([{"name": f"{admin} ({cfg.admin_id})" if cfg.admin_id and cfg.admin_name else admin,
                               "role": "Administrator (notified)", "contactInfo": cfg.admin_contact or NOT_SET}] if admin else [])
        return people, {"name": "ColdTrust system (automatic draft)", "role": "Automatic report, not yet reviewed by a person",
                        "contactInfo": "not applicable"}
    ident = f" ({reporter['id']})" if reporter.get("id") else ""
    me = {"name": f"{reporter['name']}{ident}", "role": "Administrator", "contactInfo": cfg.admin_contact or NOT_SET}
    return [driver, me], me


def _cargo(packages: list[dict[str, Any]]) -> tuple[str, str]:
    kg = sum(p["weight_kg"] for p in packages)
    items = ", ".join(f"{p['description']} ({p['id']})" for p in packages[:5]) + (
        f" and {len(packages) - 5} more" if len(packages) > 5 else "")
    states: dict[str, int] = {}
    for p in packages:
        states[p["status"]] = states.get(p["status"], 0) + 1
    now = ", ".join(f"{n} {s.replace('_', ' ').lower()}" for s, n in states.items())
    details = f"{len(packages)} packages, {kg:,} kg: {items}. Status now: {now}."
    exposed = [p for p in packages if p.get("excursion_min", 0) > 0]
    if exposed:
        worst = max(exposed, key=lambda p: p["excursion_min"])
        used = max(packages, key=lambda p: p.get("budget_consumed") or 0)
        exposure = (f"{len(exposed)} of {len(packages)} packages spent time outside their temperature limits (longest "
                    f"{worst['excursion_min']:g} min, {worst['id']}); the highest share of an excursion budget used is "
                    f"{round((used.get('budget_consumed') or 0) * 100)}% ({used['id']}).")
    else:
        exposure = "No package has spent any time outside its temperature limits according to the sensor readings."
    return details, exposure + " Quarantined or exposed packages must be inspected before release."


def _actions_taken(cfg: Settings, store: Store, entry: dict[str, Any]) -> str:
    lines = []
    if entry.get("actions"):
        acts = entry["actions"]
        lines.append((entry["ts"], f"System: {acts[0]}" if len(acts) <= 3 else
                      f"System: {len(acts)} package status changes ({entry.get('verdict_label')})"))
    if entry.get("notified"):
        lines.append((entry["ts"], "Driver and admin notified (dashboard alarm and Telegram)."))
    for e in store.audit_tail(500):
        if e["id"] <= entry["id"] or e["ts"] < entry["ts"]:
            continue
        p = e.get("params") or {}
        if e["kind"] in ("ALERT_ACKNOWLEDGED", "APPROVAL_DECIDED") and p.get("alert") == entry["id"]:
            extra = f" Result: {'; '.join(e['actions'])}." if e.get("actions") else ""
            lines.append((e["ts"], e["reasoning"] + extra))
        elif e["kind"] in ("SUPERVISOR_OVERRIDE", "VEHICLE_ACTION"):
            lines.append((e["ts"], f"{e['kind'].replace('_', ' ').capitalize()}: {(e.get('actions') or [e['reasoning']])[0]}"))
    return "\n".join(f"{_clock(cfg, ts)}  {text}" for ts, text in sorted(lines)) or "No actions are recorded yet."


def _pictures(cfg: Settings, store: Store, entry: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for e in store.audit_tail(500):
        p = e.get("params") or {}
        if e["kind"] != "EVIDENCE_CAPTURED" or abs(e["ts"] - entry["ts"]) > EVIDENCE_WINDOW_S or not p.get("file"):
            continue
        path = Path(cfg.evidence_dir) / p["file"]
        if not path.is_file():
            continue
        blob = path.read_bytes()
        intact = hashlib.sha256(blob).hexdigest() == p.get("sha256")
        said = f" · {e['reasoning']}" if e.get("explanation_source") == "llm" else ""
        caption = (f"{_clock(cfg, e['ts'])} · {p.get('detected') or 'frame'}"
                   f"{' · person visible' if p.get('person_present') else ''}{said} · file {p['file']} · SHA-256 "
                   f"{p.get('sha256', '')[:16]}…" + ("" if intact else " · WARNING: the file differs from the SHA-256 logged "
                                                                        "when it was captured"))
        out.append({"image": docfill.Image(blob, p["file"], caption), "photoDescription": caption})
    return out[:MAX_PICTURES]


def build(cfg: Settings, store: Store, audit_id: int, reporter: dict[str, Any],
          wording: dict[str, Any] | None = None, review: dict[str, Any] | None = None) -> dict[str, Any]:
    """The data for the template. `reporter` is {name, id, role}: whoever pressed the button (from the login session), or
    {name, auto: True} for the automatic draft. `wording` is the Incident agent's {incident_description, suspected_cause, by},
    or None for rule-based sentences. `review` is the latest maintenance review: a temperature event needs one that supports it."""
    entry = store.audit_get(audit_id)
    if not entry:
        raise KeyError(audit_id)
    is_alert = entry.get("notified") or entry.get("scenario") in approvals.PROPOSAL_SCENARIOS
    if not is_alert or entry.get("severity") == "info" or entry.get("explanation_source") == "human":
        raise NotAnIncident(f"entry #{audit_id} ({entry['kind']}) is not an incident")
    if entry.get("scenario") in approvals.THERMAL:
        allowed, why = maint.report_gate(review, entry["ts"])
        if not allowed:
            raise ReportNotAllowed(why)
    packages = store.list_packages()
    people, me = _people(cfg, reporter)
    when = _when(cfg, entry["ts"])
    cargo, exposure = _cargo(packages)
    ai = wording or {}
    scenario = entry.get("scenario")
    seal = entry.get("seal") or {}
    pictures = _pictures(cfg, store, entry)
    mocked = (entry.get("params") or {}).get("mocked") or ([] if not (entry.get("params") or {}).get("simulated") else ["temp"])

    notes = [f"Alert #{audit_id} ({entry['kind']}), risk score {entry.get('risk_score', 0)} of 100. Status decided by rules: "
             f"{entry.get('verdict_label')}."]
    if seal:
        notes.append(f"Data check (Seal agent): {seal['label']}." + "".join(
            f" {c['name']}: {c['detail']}." for c in seal["checks"] if c["status"] in ("warn", "fail")))
    events = [e["eventID"] for e in store.list_epcis() if e.get("ct:auditEntry") == audit_id]
    if events:
        short = [(re.search(r"sha-256;([0-9a-f]{16})", i) or [None, i[:20]])[1] + "…" for i in events[:3]]
        notes.append(f"GS1 EPCIS event hash IDs linked to this alert: {', '.join(short)}.")
    drafted = [w for w, k in (("description", "incident_description"), ("cause", "suspected_cause")) if ai.get(k)]
    rules_only = [w for w in ("description", "cause") if w not in drafted]
    notes.append((f"The {' and '.join(drafted)} wording was drafted by {ai['by']} (every figure checked against the data)"
                  + (f"; the {' and '.join(rules_only)} is rule-based text." if rules_only else "."))
                 if drafted else "Description and cause are rule-based text: no AI wording was available for this alert.")
    if entry.get("scenario") in approvals.THERMAL and review:
        notes.append(f"Maintenance review ({review.get('by') or 'rules'}, last {review.get('hours')} h): cooling unit - "
                     f"{review.get('cooling_unit')}, urgency - {review.get('urgency')}. {review.get('assessment')}")
    if reporter.get("auto"):
        notes.append("Prepared automatically when the alert was raised. An admin has not reviewed it yet.")
    notes.append("AI-assisted draft: a person must review it before it is signed or sent. The system cannot see the cargo, so "
                 "damage is not assessed.")
    if mocked:
        notes.append(f"DEMO DATA: this alert was raised from mock {' + '.join(mocked)} readings, not a real incident.")

    return {
        "reportNumber": f"INC-{when:%Y%m%d}-{audit_id}",
        "incidentDate": when.strftime("%d %B %Y"),
        "incidentTime": when.strftime("%H:%M:%S") + f" (UTC{cfg.tz_offset})",
        "incidentLocation": _location(entry, store.kv_get("vehicle", {})),
        "personnel": people,
        "incidentDescription": ai.get("incident_description") or
        f"{entry['reasoning']} Status set by the rules: {entry.get('verdict_label')}.",
        "incidentType": incident_type(entry),
        "cargoDetails": cargo,
        "cargoDamageStatus": "Not determined: the system cannot see the cargo, so a physical inspection is needed.",
        "hasCargoDamage": True,
        "cargoDamageDetails": exposure,
        "vehicleDamageStatus": "Not assessed by the system; the reviewer completes this.",
        "hasVehicleDamage": False, "vehicleDamageDetails": "",
        "injuriesStatus": "Not assessed by the system; the reviewer completes this.",
        "hasInjuries": False, "injured": [],
        "incidentCause": ai.get("suspected_cause") or CAUSE.get(scenario) or "The cause has not been determined.",
        "immediateActions": _actions_taken(cfg, store, entry),
        "reporterName": me["name"], "reporterPosition": me["role"], "reporterContact": me["contactInfo"],
        "additionalNotes": "\n".join(notes),
        "hasPhotoEvidence": bool(pictures), "photos": pictures,
        "signatures": [{"name": p["name"], "role": p["role"], "signature": "", "date": ""} for p in people],
    }


def template_bytes(cfg: Settings) -> bytes:
    path = Path(cfg.report_template)
    return (path if path.is_absolute() else PROJECT_ROOT / path).read_bytes()


def generate(cfg: Settings, store: Store, audit_id: int, reporter: dict[str, Any],
             wording: dict[str, Any] | None = None, review: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fill the template, save the file (never overwriting an earlier one) and return its name, hash and number."""
    data = build(cfg, store, audit_id, reporter, wording, review)
    result = docfill.fill(template_bytes(cfg), data)
    folder = Path(cfg.reports_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name, n = f"{data['reportNumber']}.docx", 1
    while (folder / name).exists():
        n += 1
        name = f"{data['reportNumber']}-r{n}.docx"
    (folder / name).write_bytes(result.docx)
    return {"file": name, "sha256": hashlib.sha256(result.docx).hexdigest(), "report_number": data["reportNumber"],
            "pictures": result.images, "missing": sorted(result.missing), "revision": n}
