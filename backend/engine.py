"""Orchestrator: integrity gate -> findings -> cross-signal verdict -> per-package risk -> enforcement.

Deterministic. The LLM (llm.py) only rewrites the explanation after the fact; it never
changes a status. That keeps a model mistake from quarantining or releasing cargo.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

from . import epcis, rules
from .config import Settings
from .rules import Sample
from .schemas import IngestIn
from .seed import BACKUP_CUSTODIAN, VEHICLE, fence_at, location_gln
from .store import Store

SEVERITY = {"info": 0, "warning": 1, "critical": 2, "emergency": 3}
STATUS_ORDER = {"NORMAL": 0, "UNDER_REVIEW": 1, "QUARANTINED": 2}
STATUS_LABEL = {
    "NORMAL": "NORMAL",
    "UNDER_REVIEW": "UNDER REVIEW",
    "QUARANTINED": "QUARANTINED - DO NOT ACCEPT",
}
TAMPER_RISK_FLOOR = {"THEFT_SUSPECTED": 90, "SPOOF_SUSPECTED": 50, "SUSPICIOUS_PERSON": 40, "DOOR_AJAR": 35}
PERSON_FRESH_S = 120  # a camera sighting older than this no longer counts
DEMO_DEFAULTS = {"mock": False, "temp_c": 4.0, "light_mock": False, "light_pct": 0.0}
DEMO_FRESH_S = 600
HISTORY_WINDOW_S = 6 * 3600
REMINDER_S = 600  # unresolved problems are re-sent at most every 10 min
DATA_TRUST_CODES = {"LIGHT_ZERO_VARIANCE", "DOOR_OPEN_NO_THERMAL_RESPONSE", "SENSOR_DISAGREEMENT"}


@dataclass
class Finding:
    code: str
    severity: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


class Engine:
    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.cfg = settings
        self._last_notify: dict[str, tuple[float, int]] = {}
        self._clock: float | None = None  # newest device time seen; the 'now' in timelapse mode

    def now(self) -> float:
        """Wall clock normally; the device's own clock when running a timelapse simulation."""
        return time.time() if self.cfg.strict_time else (self._clock or time.time())

    # ================================================================ ingest
    def ingest(self, p: IngestIn, now: float | None = None) -> dict[str, Any]:
        cfg = self.cfg
        now = float(now if now is not None else (time.time() if cfg.strict_time else p.ts))
        samples = [Sample(s.t, s.temp_c, s.hum, float(s.light), s.temp2_c)
                   for s in sorted(p.samples, key=lambda x: x.t)]

        # ---- integrity gate (rules decide; nothing here involves the LLM)
        message = rules.canonical(p.device_id, p.seq, p.ts, p.ping_type, p.trigger, samples)
        if not rules.verify(cfg.device_secret, message, p.sig):
            return self._reject(p, now, "BAD_SIGNATURE", "Payload signature does not match; data discarded.")
        dev = self.store.kv_get(f"device:{p.device_id}", {})
        seq_state = rules.sequence_ok(dev.get("last_seq"), p.seq)
        if seq_state == "replay":
            return self._reject(p, now, "REPLAY_SEQUENCE",
                                f"Sequence {p.seq} is not newer than {dev.get('last_seq')}; replay suspected.")
        if cfg.strict_time and abs(now - p.ts) > cfg.ts_window_s:
            return self._reject(p, now, "STALE_TIMESTAMP",
                                f"Payload timestamp is {abs(now - p.ts):.0f}s from server time.")
        if not rules.timestamps_fresh(samples, self.store.last_sample_t(p.device_id)):
            return self._reject(p, now, "REPLAY_SAMPLES", "Samples are not newer than stored data; replay suspected.")

        self._clock = now
        self.store.kv_set(f"device:{p.device_id}", {"last_seq": p.seq, "last_seen": now})
        self.store.add_readings(p.device_id, samples, p.ping_type)
        history = self.store.recent_readings(p.device_id, HISTORY_WINDOW_S, until=samples[-1].t)
        latest = history[-1]

        # ---- context
        vehicle = self.store.kv_get("vehicle", {})
        cam = self.store.kv_get("camera", {})
        moving = (vehicle.get("speed_mph") or 0) > 5
        authorised = bool(vehicle.get("authorised_stop"))
        person_recent = bool(cam.get("person_present")) and (now - cam.get("ts", 0)) <= 120
        # After "Activate Route" the truck should be driving; standing still with the hold lit is suspicious.
        stopped_on_route = vehicle.get("route_status") == "En route" and (vehicle.get("speed_mph") or 0) < 1

        # ---- features
        slope = rules.slope_c_per_min(history, cfg.rate_window_s)
        step = rules.step_change_c(history, cfg.step_window_s)
        bright_s = rules.bright_duration_s(history, cfg.light_bright_pct)
        bright_now = rules.is_bright(latest, cfg.light_bright_pct)
        recent = [s for s in history if s.t >= latest.t - 600]
        bright_recent = any(rules.is_bright(s, cfg.light_bright_pct) for s in recent)

        packages = self.store.list_packages()
        findings: list[Finding] = []
        if seq_state == "gap":
            findings.append(Finding("SEQ_GAP", "info", f"Sequence jumped to {p.seq}; earlier pings may have been lost."))

        if bright_now:
            ctx = "moving" if moving else "stationary"
            findings.append(Finding(
                "LIGHT_BRIGHT", "warning" if authorised else "critical",
                f"Cargo light at {latest.light_pct:.0f}% for {bright_s:.0f}s while {ctx}"
                + (" at an authorised stop" if authorised else " outside an authorised stop")))

        violating = [pk for pk in packages
                     if rules.out_of_range_now(history, pk["temp_min_c"], pk["temp_max_c"])]
        if violating:
            findings.append(Finding(
                "TEMP_THRESHOLD", "critical",
                f"{latest.temp_c:.0f}°C is outside the limits of {len(violating)} package(s): "
                + ", ".join(pk["id"] for pk in violating[:3]) + ("..." if len(violating) > 3 else "")))
        if slope is not None and abs(slope) >= cfg.rate_limit_c_per_min:
            findings.append(Finding("TEMP_RATE", "warning",
                                    f"Temperature changing {slope:+.2f}°C/min (limit {cfg.rate_limit_c_per_min}°C/min)"))
        if abs(step) >= cfg.step_change_c:
            findings.append(Finding("TEMP_STEP", "critical",
                                    f"{step:+.0f}°C change within {cfg.step_window_s:.0f}s"))
        if rules.zero_variance_light(history, cfg.zero_variance_min_samples):
            findings.append(Finding("LIGHT_ZERO_VARIANCE", "warning",
                                    "Light reading is perfectly flat; an analog LDR should jitter (static spoof?)"))
        if rules.sensors_disagree(history, cfg.temp_cross_check_c):
            findings.append(Finding("SENSOR_DISAGREEMENT", "warning",
                                    f"The two thermometers differ by more than {cfg.temp_cross_check_c:.0f}°C "
                                    f"({latest.temp_c:.0f} vs {latest.temp2_c:.1f}°C); one is wrong, failing or "
                                    "tampered with"))
        if rules.door_open_without_thermal_response(history, cfg.ambient_c, cfg.light_bright_pct,
                                                    cfg.door_open_contradiction_s):
            findings.append(Finding("DOOR_OPEN_NO_THERMAL_RESPONSE", "critical",
                                    f"Door looks open >{cfg.door_open_contradiction_s:.0f}s in {cfg.ambient_c:.0f}°C "
                                    "ambient yet hold temperature has not moved (contradictory telemetry)"))

        codes = {f.code for f in findings}
        cooling = step <= -cfg.step_change_c or any(latest.temp_c < pk["temp_min_c"] for pk in violating)
        scenario = self._classify(codes, authorised=authorised, person_recent=person_recent, cooling=cooling,
                                  stopped_on_route=stopped_on_route)

        # ---- per-package assessment and enforcement
        actions: list[str] = []
        changed: list[str] = []
        transitions: dict[tuple[str, bool], list[str]] = {}  # (new status, was quarantined) -> package ids
        worst = "NORMAL"
        max_risk = 0
        has_event = scenario not in ("NORMAL",) and any(SEVERITY[f.severity] >= 1 for f in findings)
        for pk in packages:
            lo, hi = pk["temp_min_c"], pk["temp_max_c"]
            mins = rules.minutes_outside(history, lo, hi)
            budget = max(pk["excursion_budget_min"] * cfg.budget_scale, 0.1)
            consumed = mins / budget
            risk = rules.risk_score(
                budget_consumed=consumed,
                peak_dev_c=rules.peak_deviation_c(history, lo, hi),
                rate_c_per_min=slope,
                light_sensitive_exposure=("Cold chain" in pk.get("tags", [])) and bright_recent,
                data_trust_low=bool(codes & DATA_TRUST_CODES))
            # Tamper scenarios carry their own weight: a stolen-from hold is not "risk 0" just because it is still cold.
            risk = max(risk, TAMPER_RISK_FLOOR.get(scenario, 0))
            computed = self._package_status(risk, consumed, scenario, rules.out_of_range_now(history, lo, hi))
            old = pk["status"]
            new = old
            if has_event and STATUS_ORDER[computed] > STATUS_ORDER[old]:
                new = computed
            elif (old == "UNDER_REVIEW" and not pk.get("manual") and computed == "NORMAL"
                  and not bright_recent and risk < 30):
                new = "NORMAL"
            pk.update(risk_score=risk, excursion_min=round(mins, 1), budget_consumed=round(consumed, 3))
            if new != old:
                pk["status"] = new
                changed.append(pk["id"])
                transitions.setdefault((new, old == "QUARANTINED"), []).append(pk["id"])
                if new == "QUARANTINED":
                    pk["token_valid"] = False
                    pk["revoked_at"] = now
                    actions.append(f"{pk['id']}: status -> {STATUS_LABEL[new]}; custody token revoked")
                else:
                    actions.append(f"{pk['id']}: status -> {STATUS_LABEL[new]}")
            self.store.upsert_package(pk)
            max_risk = max(max_risk, risk)
            if STATUS_ORDER[pk["status"]] > STATUS_ORDER[worst]:
                worst = pk["status"]

        severity = max((f.severity for f in findings), key=lambda s: SEVERITY[s], default="info")
        if scenario == "THEFT_SUSPECTED":
            severity = "emergency"
        entry: dict[str, Any] = {
            "kind": scenario if scenario != "NORMAL" else p.ping_type,
            "scenario": scenario,
            "ping_type": p.ping_type,
            "trigger": p.trigger,
            "severity": severity,
            "device_id": p.device_id,
            "params": {
                "seq": p.seq, "n_samples": len(samples), "temp_c": latest.temp_c, "hum": latest.hum,
                "light_pct": round(latest.light_pct, 1),
                "slope_c_per_min": None if slope is None else round(slope, 2),
                "step_c": round(step, 1), "bright_s": round(bright_s),
                "speed_mph": vehicle.get("speed_mph"), "zone": vehicle.get("zone"),
                "lat": vehicle.get("lat"), "lon": vehicle.get("lon"), "route_status": vehicle.get("route_status"),
                "person_in_frame": person_recent, "simulated": bool(p.demo),
                "mocked": list((p.demo_sensors or ["temp"]) if p.demo else []),
            },
            "findings": [f.as_dict() for f in findings],
            "verdict": worst,
            "verdict_label": STATUS_LABEL[worst],
            "risk_score": max_risk,
            "affected": changed,
            "actions": actions,
            "reasoning": self._template_reasoning(scenario, findings, latest, authorised, moving, person_recent),
            "recommended_actions": self._recommended(scenario),
            "explanation_source": "rules",
        }
        entry["voice"] = self._voice_text(scenario, vehicle)
        notify_reason = self._notify_reason(scenario, severity, now, status_changed=bool(changed))
        notify = notify_reason is not None
        entry["notified"] = notify
        entry["notify_reason"] = notify_reason
        entry["id"] = self.store.add_audit(now, entry)
        entry["ts"] = now
        sensors = (p.demo_sensors or ["temp"]) if p.demo else []
        self.store.kv_set("demo_last", {"ts": now, "demo": bool(p.demo), "sensors": sensors})   # was the newest message mock?
        self._record_trip_events(now=now, samples=samples, codes=codes, scenario=scenario,
                                 transitions=transitions, packages=packages, audit_id=entry["id"],
                                 simulated=bool(p.demo))
        return {"accepted": True, "entry": entry, "notify": notify, "scenario": scenario}

    # ================================================================ other inputs
    def set_vehicle(self, data: dict[str, Any]) -> dict[str, Any]:
        cur = self.store.kv_get("vehicle", {})
        cur.update({k: v for k, v in data.items() if v is not None})
        if data.get("lat") is not None and data.get("lon") is not None and data.get("zone") is None:
            # GPS decides the zone; an authorised stop means inside an authorised geofence and not moving
            fence = fence_at(data["lat"], data["lon"])
            cur["zone"] = fence["name"] if fence else None
            if data.get("authorised_stop") is None:
                cur["authorised_stop"] = bool(fence and fence["authorised"] and (cur.get("speed_mph") or 0) < 5)
        cur["updated_at"] = self.now()
        self.store.kv_set("vehicle", cur)
        return cur

    def camera_observation(self, observation: dict[str, Any], now: float | None = None) -> None:
        self.store.kv_set("camera", {**observation, "ts": self.now() if now is None else now})

    def person_observed(self, obs: dict[str, Any], now: float | None = None) -> dict[str, Any] | None:
        """The camera reports whether a person is in the hold; the agent decides what that means.

        At an authorised stop it is handling (logged). Anywhere else it is a SUSPICIOUS_PERSON alert and the cargo
        goes under review; with the hold lit at the same time (door open) it is THEFT_SUSPECTED and everything is
        quarantined. The camera only says "person" plus pose cues; it never identifies anyone.
        Returns {"entry", "notify"} when something worth logging happened, else None.
        """
        now = self.now() if now is None else now
        prev = self.store.kv_get("camera", {})
        self.store.kv_set("camera", {**obs, "ts": now})
        present = bool(obs.get("person_present"))
        was = bool(prev.get("person_present")) and now - prev.get("ts", 0) <= PERSON_FRESH_S
        if not present:
            if not was:
                return None
            return self._camera_entry(now, "PERSON_LEFT", "info", "No person visible in the cargo hold any more.",
                                      obs, scenario_key=None)
        behaviours = list(obs.get("behaviours") or [])
        if was and not set(behaviours) - set(prev.get("behaviours") or []):
            return None  # same person, nothing new: keep the log readable

        vehicle = self.store.kv_get("vehicle", {})
        where = vehicle.get("zone") or "between stops"
        moving = (vehicle.get("speed_mph") or 0) > 5
        n = int(obs.get("persons") or 1)
        who = f"{n} person{'s' if n > 1 else ''} in the cargo hold" + (f" ({', '.join(behaviours)})" if behaviours else "")
        if vehicle.get("authorised_stop"):
            return self._camera_entry(now, "PERSON_AT_AUTHORISED_STOP", "info",
                                      f"{who} at {where}, an authorised stop: logged as handling.", obs,
                                      scenario_key=None)
        device = VEHICLE["device_id"]
        last_t = self.store.last_sample_t(device)
        recent = self.store.recent_readings(device, PERSON_FRESH_S, until=last_t) if last_t else []
        lit = bool(recent) and rules.is_bright(recent[-1], self.cfg.light_bright_pct) and now - recent[-1].t <= PERSON_FRESH_S
        context = "while the truck is moving" if moving else f"while stopped ({where})"
        if lit:
            scenario, severity, target = "THEFT_SUSPECTED", "emergency", "QUARANTINED"
            reasoning = (f"{who} with the hold lit (door open) {context}, outside an authorised stop: "
                         "treated as unauthorised access.")
        else:
            scenario, severity, target = "SUSPICIOUS_PERSON", "critical", "UNDER_REVIEW"
            reasoning = f"{who} {context}, outside an authorised stop. Nobody should be in the hold here."
        return self._camera_entry(now, scenario, severity, reasoning, obs, scenario_key=scenario, target=target,
                                  extra={"zone": vehicle.get("zone"), "speed_mph": vehicle.get("speed_mph"), "lit": lit,
                                         "lat": vehicle.get("lat"), "lon": vehicle.get("lon")})

    def _camera_entry(self, now: float, kind: str, severity: str, reasoning: str, obs: dict[str, Any], *,
                      scenario_key: str | None, target: str | None = None,
                      extra: dict[str, Any] | None = None) -> dict[str, Any]:
        actions: list[str] = []
        changed: list[str] = []
        transitions: dict[tuple[str, bool], list[str]] = {}
        if target:
            for pk in self.store.list_packages():
                pk["risk_score"] = max(pk.get("risk_score", 0), TAMPER_RISK_FLOOR.get(kind, 0))
                if STATUS_ORDER[target] > STATUS_ORDER[pk["status"]]:
                    transitions.setdefault((target, False), []).append(pk["id"])
                    pk["status"] = target
                    changed.append(pk["id"])
                    if target == "QUARANTINED":
                        pk["token_valid"] = False
                        pk["revoked_at"] = now
                        actions.append(f"{pk['id']}: status -> {STATUS_LABEL[target]}; custody token revoked")
                    else:
                        actions.append(f"{pk['id']}: status -> {STATUS_LABEL[target]}")
                self.store.upsert_package(pk)
        packages = self.store.list_packages()
        worst = max((p["status"] for p in packages), key=lambda s: STATUS_ORDER[s], default="NORMAL")
        entry: dict[str, Any] = {
            "kind": kind, "scenario": kind, "ping_type": "CAMERA", "trigger": "PERSON", "severity": severity,
            "device_id": obs.get("source") or "camera",
            "params": {"persons": obs.get("persons"), "behaviours": obs.get("behaviours") or [], **(extra or {})},
            "findings": [Finding("PERSON_IN_HOLD" if obs.get("person_present") else "PERSON_LEFT", severity,
                                 reasoning).as_dict()],
            "verdict": worst if target else "NONE", "verdict_label": STATUS_LABEL[worst] if target else "NO CHANGE",
            "risk_score": max((p["risk_score"] for p in packages), default=0), "affected": changed,
            "actions": actions, "reasoning": reasoning, "recommended_actions": self._recommended(kind)
            if kind in ("SUSPICIOUS_PERSON", "THEFT_SUSPECTED") else [],
            "explanation_source": "rules", "voice": self._voice_text(kind, {}) if scenario_key else "",
        }
        reason = self._notify_reason(scenario_key, severity, now, status_changed=bool(changed)) if scenario_key else None
        entry["notified"] = reason is not None
        entry["notify_reason"] = reason
        entry["id"] = self.store.add_audit(now, entry)
        entry["ts"] = now
        for (new_status, was_quarantined), ids in transitions.items():
            self.store.add_epcis(epcis.status_event(
                [epcis.package_epc(i) for i in ids], now, self.cfg.tz_offset, new_status, read_point=self._read_point(),
                cause=kind, vehicle=epcis.giai(VEHICLE["id"]), was_quarantined=was_quarantined, audit_id=entry["id"]))
        return {"entry": entry, "notify": reason is not None}

    def override(self, pkg_id: str, status: str, reason: str, supervisor: str, now: float) -> dict[str, Any] | None:
        pk = self.store.get_package(pkg_id)
        if not pk:
            return None
        old = pk["status"]
        pk["status"] = status
        pk["manual"] = True
        if status == "QUARANTINED":
            pk["token_valid"] = False
        elif old == "QUARANTINED":
            pk["custody_token"] = secrets.token_hex(8)  # re-issue; the old token stays dead
            pk["token_valid"] = True
        self.store.upsert_package(pk)
        entry = {
            "kind": "SUPERVISOR_OVERRIDE", "scenario": "OVERRIDE", "ping_type": "MANUAL", "trigger": None,
            "severity": "warning", "device_id": None, "params": {"package": pkg_id, "supervisor": supervisor},
            "findings": [], "verdict": status, "verdict_label": STATUS_LABEL[status], "risk_score": pk["risk_score"],
            "affected": [pkg_id], "actions": [f"{pkg_id}: {old} -> {status} by {supervisor}"],
            "reasoning": f"Supervisor override: {reason}", "recommended_actions": [],
            "explanation_source": "human", "notified": False,
        }
        entry["id"] = self.store.add_audit(now, entry)
        entry["ts"] = now
        self.store.add_epcis(epcis.status_event(
            [epcis.package_epc(pkg_id)], now, self.cfg.tz_offset, status, read_point=self._read_point(),
            cause="SUPERVISOR_OVERRIDE", vehicle=epcis.giai(VEHICLE["id"]), was_quarantined=old == "QUARANTINED",
            supervisor=supervisor, reason=reason, audit_id=entry["id"]))
        return entry

    # ================================================================ watchdog
    def check_health(self, now: float) -> list[dict[str, Any]]:
        """Silence is an alarm too: CISA asks for a plan for when "connectivity is lost" and for active GPS
        surveillance in transit. Each problem is reported once when it starts and once when it clears.

        Off in timelapse mode: simulated device time and the wall clock cannot be compared."""
        cfg = self.cfg
        if not cfg.strict_time:
            return []
        state = self.store.kv_get("health", {"node_silent": False, "gps_stale": False})
        out: list[dict[str, Any]] = []
        device = VEHICLE["device_id"]
        dev = self.store.kv_get(f"device:{device}")
        if dev:  # only after first contact
            gap = now - dev["last_seen"]
            if gap > cfg.silent_after_s and not state["node_silent"]:
                state["node_silent"] = True
                self._last_notify.pop("NODE_RESTORED", None)
                out.append(self._health_entry(
                    now, "NODE_SILENT", "critical",
                    f"No telemetry from {device} for {gap / 60:.0f} min, so the cargo is unobserved",
                    ["Driver: check the sensor node's power and Wi-Fi",
                     "Check the hold with a handheld thermometer",
                     f"If the hold cannot be verified, divert to the backup cold store ({BACKUP_CUSTODIAN})"],
                    "Warning. The cargo sensor has gone silent. Check the sensor node."))
            elif gap <= cfg.silent_after_s and state["node_silent"]:
                state["node_silent"] = False
                self._last_notify.pop("NODE_SILENT", None)
                out.append(self._health_entry(now, "NODE_RESTORED", "info",
                                              f"{device} is reporting again; buffered readings were uploaded", [], ""))
        veh = self.store.kv_get("vehicle", {})
        updated = veh.get("updated_at")
        en_route = (veh.get("speed_mph") or 0) > 0 or not veh.get("authorised_stop")
        if updated is not None and en_route:
            stale = now - updated > cfg.gps_stale_s
            if stale and not state["gps_stale"]:
                state["gps_stale"] = True
                self._last_notify.pop("GPS_RESTORED", None)
                out.append(self._health_entry(
                    now, "GPS_STALE", "warning",
                    f"No vehicle position for {(now - updated) / 60:.0f} min while en route",
                    ["Call the driver to confirm location", "Check the GPS / phone tracker is on"],
                    "Notice. Vehicle position is not updating."))
            elif not stale and state["gps_stale"]:
                state["gps_stale"] = False
                self._last_notify.pop("GPS_STALE", None)
                out.append(self._health_entry(now, "GPS_RESTORED", "info", "Vehicle position is updating again",
                                              [], ""))
        self.store.kv_set("health", state)
        return out

    def _health_entry(self, now: float, kind: str, severity: str, detail: str, recommended: list[str],
                      voice: str) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "kind": kind, "scenario": kind, "ping_type": "WATCHDOG", "trigger": None, "severity": severity,
            "device_id": VEHICLE["device_id"], "params": {}, "findings": [Finding(kind, severity, detail).as_dict()],
            "verdict": "NONE", "verdict_label": "NO CHANGE", "risk_score": 0, "affected": [], "actions": [],
            "reasoning": detail + ".", "recommended_actions": recommended, "explanation_source": "rules",
            "voice": voice,
        }
        reason = self._notify_reason(kind, severity, now)
        entry["notified"] = reason is not None
        entry["notify_reason"] = reason
        entry["id"] = self.store.add_audit(now, entry)
        entry["ts"] = now
        return {"entry": entry, "notify": reason is not None}

    # ================================================================ GS1 EPCIS trip record
    def _read_point(self) -> str | None:
        veh = self.store.kv_get("vehicle", {})
        if veh.get("lat") is None or veh.get("lon") is None:
            return None
        return epcis.geo_uri(veh["lat"], veh["lon"])

    def _record_trip_events(self, *, now: float, samples: list[Sample], codes: set[str], scenario: str,
                            transitions: dict[tuple[str, bool], list[str]], packages: list[dict[str, Any]],
                            audit_id: int, simulated: bool = False) -> None:
        cfg = self.cfg
        epcs = [epcis.package_epc(pk["id"]) for pk in packages]
        truck = epcis.giai(VEHICLE["id"])
        read_point = self._read_point()
        if not self.store.kv_get("epcis_departed"):
            self.store.add_epcis(epcis.departing_event(epcs, now, cfg.tz_offset, location_gln("Shah Alam Depot"), truck))
            self.store.kv_set("epcis_departed", True)
        self.store.add_epcis(epcis.transport_event(
            epcs, now, cfg.tz_offset, read_point=read_point, vehicle=truck, device=epcis.giai(VEHICLE["device_id"]),
            samples=samples, hold_limits=tuple(VEHICLE["hold_limits_c"]), light_alarm_pct=cfg.light_bright_pct,
            data_suspect=bool(codes & DATA_TRUST_CODES), audit_id=audit_id, simulated=simulated))
        for (new_status, was_quarantined), ids in transitions.items():
            self.store.add_epcis(epcis.status_event(
                [epcis.package_epc(i) for i in ids], now, cfg.tz_offset, new_status, read_point=read_point,
                cause=scenario, vehicle=truck, was_quarantined=was_quarantined, audit_id=audit_id))

    # ================================================================ demo (simulated temperature)
    def demo_state(self) -> dict[str, Any]:
        """Mock temperature the dashboard can switch on: checked = the node reports temp_c, unchecked = its sensor."""
        cur = {**DEMO_DEFAULTS, **self.store.kv_get("demo_mock", {})}
        last = self.store.kv_get("demo_last")
        fresh = isinstance(last, dict) and self.now() - last["ts"] <= DEMO_FRESH_S
        sensors = set(last.get("sensors", [])) if fresh and last.get("demo") else set()
        on = self.cfg.demo_enabled
        return {"enabled": on, "mock": bool(cur["mock"]) and on, "temp_c": cur["temp_c"],
                "light_mock": bool(cur["light_mock"]) and on, "light_pct": cur["light_pct"],
                # simulated_*: the newest message from the node contained mock readings of that sensor;
                # it clears with the first message that does not
                "simulated_temp": "temp" in sensors, "simulated_light": "light" in sensors,
                "simulated": bool(sensors)}

    def set_demo_mock(self, mock: bool | None = None, temp_c: float | None = None, light_mock: bool | None = None,
                      light_pct: float | None = None) -> dict[str, Any]:
        cur = {**DEMO_DEFAULTS, **self.store.kv_get("demo_mock", {})}
        if mock is not None:
            cur["mock"] = mock
        if temp_c is not None:
            cur["temp_c"] = round(temp_c)
        if light_mock is not None:
            cur["light_mock"] = light_mock
        if light_pct is not None:
            cur["light_pct"] = round(light_pct)
        self.store.kv_set("demo_mock", cur)
        return self.demo_state()

    # ================================================================ summaries
    def state(self) -> dict[str, Any]:
        packages = self.store.list_packages()
        counts = {k: 0 for k in STATUS_ORDER}
        for pk in packages:
            counts[pk["status"]] += 1
        return {
            "vehicle": self.store.kv_get("vehicle", {}),
            "camera": self.store.kv_get("camera", {}),
            "demo": self.demo_state(),
            "packages": packages,
            "totals": {
                "items": len(packages),
                "weight_lbs": sum(p["weight_lbs"] for p in packages),
                "volume_ft3": sum(p["volume_ft3"] for p in packages),
                "status_counts": counts,
            },
        }

    # ================================================================ internals
    def _reject(self, p: IngestIn, now: float, code: str, detail: str) -> dict[str, Any]:
        entry = {
            "kind": f"REJECTED_{code}", "scenario": "DATA_TRUST", "ping_type": p.ping_type, "trigger": p.trigger,
            "severity": "critical", "device_id": p.device_id, "params": {"seq": p.seq, "ts": p.ts},
            "findings": [Finding(code, "critical", detail).as_dict()], "verdict": "NONE",
            "verdict_label": "NO CHANGE", "risk_score": 0, "affected": [],
            "actions": ["Payload discarded; no readings stored"],
            "reasoning": detail, "recommended_actions": ["Check the device and its network path"],
            "explanation_source": "rules",
            "voice": "Warning. A telemetry payload failed integrity checks and was rejected.",
        }
        notify_reason = self._notify_reason(f"REJECT_{code}", "critical", now)  # a replay flood must not spam alerts
        notify = notify_reason is not None
        entry["notified"] = notify
        entry["notify_reason"] = notify_reason
        entry["id"] = self.store.add_audit(now, entry)
        entry["ts"] = now
        return {"accepted": False, "entry": entry, "notify": notify, "scenario": "DATA_TRUST", "reason": code}

    @staticmethod
    def _classify(codes: set[str], *, authorised: bool, person_recent: bool, cooling: bool,
                  stopped_on_route: bool = False) -> str:
        light = "LIGHT_BRIGHT" in codes
        thermal = bool(codes & {"TEMP_THRESHOLD", "TEMP_RATE", "TEMP_STEP"})
        spoof = "DOOR_OPEN_NO_THERMAL_RESPONSE" in codes
        if light and person_recent and not authorised:
            return "THEFT_SUSPECTED"
        if light and stopped_on_route and not authorised:
            return "THEFT_SUSPECTED"   # door opened while the truck stands still mid-route
        if spoof:
            return "SPOOF_SUSPECTED"
        if light and authorised and not thermal:
            return "AUTHORISED_HANDLING"   # harmless only while nothing else is wrong; a temperature alarm still wins
        if light and not authorised:
            return "DOOR_AJAR"
        if thermal:
            return "FREEZE_RISK" if cooling else "THERMAL_EXCURSION"
        if codes & {"LIGHT_ZERO_VARIANCE", "SENSOR_DISAGREEMENT"}:
            return "DATA_TRUST"
        return "NORMAL"

    @staticmethod
    def _package_status(risk: int, consumed: float, scenario: str, out_now: bool) -> str:
        if scenario == "THEFT_SUSPECTED" or consumed >= 1.0 or risk >= 70:
            return "QUARANTINED"
        # DATA_TRUST on its own is an alert, not a custody change: one weak signal must not block cargo.
        if risk >= 30 or out_now or scenario in ("DOOR_AJAR", "SPOOF_SUSPECTED",
                                                 "THERMAL_EXCURSION", "FREEZE_RISK"):
            return "UNDER_REVIEW"
        return "NORMAL"

    def _notify_reason(self, scenario: str, severity: str, now: float, status_changed: bool = False) -> str | None:
        """Why people should be messaged about this entry, or None to stay quiet.

        Message on: a new problem, an escalation, a package changing status, or a reminder
        every REMINDER_S while it is unresolved. An all-clear resets everything, so a later
        recurrence counts as new.
        """
        if scenario == "NORMAL":
            self._last_notify.clear()
            return None
        if scenario == "AUTHORISED_HANDLING":
            return None
        sev = SEVERITY[severity]
        last = self._last_notify.get(scenario)
        if last is None:
            reason = "new"
        elif sev > last[1]:
            reason = "escalated"
        elif status_changed:
            reason = "status_change"
        elif now - last[0] >= REMINDER_S:
            reason = "reminder"
        else:
            return None
        self._last_notify[scenario] = (now, max(sev, last[1]) if last else sev)
        return reason

    @staticmethod
    def _template_reasoning(scenario: str, findings: list[Finding], latest: Sample, authorised: bool,
                            moving: bool, person: bool) -> str:
        detail = "; ".join(f.detail for f in findings) or "All readings within limits."
        lead = {
            "THEFT_SUSPECTED": "Bright light and a person in the hold camera outside an authorised stop: "
                               "treated as unauthorised access.",
            "SPOOF_SUSPECTED": "Telemetry contradicts itself, so the sensor data cannot be trusted.",
            "AUTHORISED_HANDLING": "Door/light activity at an authorised stop; logged, no action required.",
            "DOOR_AJAR": "Cargo light detected outside an authorised stop; the rear door may be open.",
            "THERMAL_EXCURSION": "Hold temperature is outside, or heading out of, package limits.",
            "FREEZE_RISK": "Hold temperature is falling below package limits; freeze damage possible.",
            "DATA_TRUST": "Sensor data shows signs of being static, replayed or wrong.",
            "NORMAL": "Within limits.",
        }[scenario]
        if scenario == "THEFT_SUSPECTED" and not person:
            lead = ("Cargo light came on while the truck was stopped (0 mph) on an active route, outside an "
                    "authorised stop: treated as possible theft.")
        return f"{lead} {detail}."

    @staticmethod
    def _recommended(scenario: str) -> list[str]:
        return {
            "THEFT_SUSPECTED": ["Stop in a safe place", "Review camera evidence", "Prepare incident report for review"],
            "SUSPICIOUS_PERSON": ["Driver: stop in a safe place and check the hold", "Watch the live camera",
                                  "Call the police if the person is not authorised"],
            "SPOOF_SUSPECTED": ["Inspect the sensor node physically", "Do not rely on this device's data"],
            "DOOR_AJAR": ["Driver to check the rear door and seal", "Confirm temperature recovers within 10 min"],
            "THERMAL_EXCURSION": ["Check refrigeration unit", "Re-check in 10 min",
                                  f"If not back in range in 10 min, divert to the backup cold store ({BACKUP_CUSTODIAN})",
                                  "Flag for QA review on arrival"],
            "FREEZE_RISK": ["Check thermostat setpoint", "Move freeze-sensitive items if possible",
                            f"If it keeps falling, divert to the backup cold store ({BACKUP_CUSTODIAN})"],
            "DATA_TRUST": ["Verify the sensor wiring", "Cross-check with a handheld thermometer"],
            "AUTHORISED_HANDLING": [],
            "NORMAL": [],
        }[scenario]

    @staticmethod
    def _voice_text(scenario: str, vehicle: dict[str, Any]) -> str:
        return {
            "THEFT_SUSPECTED": "Emergency. Possible theft in the cargo hold. Evidence is being captured.",
            "SUSPICIOUS_PERSON": "Warning. A person has been detected in the cargo hold. Check the cargo now.",
            "SPOOF_SUSPECTED": "Warning. Sensor data is contradictory and may be spoofed.",
            "DOOR_AJAR": "Warning. Bright light in the cargo hold. Check that the rear door is closed.",
            "THERMAL_EXCURSION": "Warning. Cargo temperature is outside safe limits. Check refrigeration.",
            "FREEZE_RISK": "Warning. Cargo temperature is dropping fast. Freezing risk.",
            "DATA_TRUST": "Notice. Sensor readings look unreliable.",
            "AUTHORISED_HANDLING": "",
            "NORMAL": "",
        }[scenario]
