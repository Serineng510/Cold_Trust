"""Approvals queue: the Incident agent (or, failing that, the rules) PROPOSES an action; only an admin can approve it.

The agent never executes anything and cannot invent an action. It can only pick from the fixed menu below, and only from
the actions that the rules say are allowed in the current situation (`allowed`). Whatever it writes as the reason is shown
to the admin as text and is never run. Approving is a separate, logged, admin-only call (see agent.py).
"""
from __future__ import annotations

from typing import Any

from .seed import BACKUP_CUSTODIAN

QUARANTINE, DIVERT, OFFLINE, PREPARE_REPORT = "QUARANTINE", "DIVERT", "OFFLINE", "PREPARE_REPORT"
ACTIONS = (QUARANTINE, DIVERT, OFFLINE, PREPARE_REPORT)       # the whole menu; the agent cannot add to it
LABEL = {
    QUARANTINE: "Quarantine the cargo",
    DIVERT: f"Divert to the backup cold store ({BACKUP_CUSTODIAN})",
    OFFLINE: "Take the vehicle offline",
    PREPARE_REPORT: "Prepare the incident report",
}
EXPIRY_S = 3600          # a proposal nobody answered within an hour is closed; the situation has moved on
REPEAT_S = 600           # after a decision, the same proposal is not raised again for this long
KEEP = 60
MAX_PROPOSALS = 3
MAX_REASON = 200

THERMAL = {"THERMAL_EXCURSION", "FREEZE_RISK"}
SECURITY = {"THEFT_SUSPECTED", "SUSPICIOUS_PERSON", "SPOOF_SUSPECTED"}
PROPOSAL_SCENARIOS = THERMAL | SECURITY | {"DOOR_AJAR"}


def allowed(entry: dict[str, Any], packages: list[dict[str, Any]], vehicle: dict[str, Any], report_ok: bool = True) -> list[str]:
    """Which menu actions make sense for this alert right now, decided by rules (never by the AI). `report_ok` is False for a
    temperature event that no maintenance review supports yet (maintenance.report_gate): then no report is offered."""
    scenario = entry.get("scenario")
    if scenario not in PROPOSAL_SCENARIOS:
        return []
    out = []
    if any(p["status"] != "QUARANTINED" for p in packages):
        out.append(QUARANTINE)
    if scenario in THERMAL:
        out.append(DIVERT)
    if scenario in THERMAL | SECURITY and vehicle.get("route_status") != "Offline":
        out.append(OFFLINE)
    if report_ok:
        out.append(PREPARE_REPORT)
    return out


def rule_proposals(entry: dict[str, Any], menu: list[str]) -> list[dict[str, str]]:
    """What is proposed when no AI answer is available: a plain default per situation, in the rules' own words."""
    scenario = entry.get("scenario")
    why = {
        "THEFT_SUSPECTED": "The cargo hold was opened by a person outside an authorised stop.",
        "SUSPICIOUS_PERSON": "A person was seen in the cargo hold outside an authorised stop.",
        "SPOOF_SUSPECTED": "The sensor data contradicts itself and cannot be trusted.",
        "DOOR_AJAR": "The cargo door was opened outside an authorised stop.",
        "THERMAL_EXCURSION": "The hold temperature is outside the products' limits.",
        "FREEZE_RISK": "The hold is getting too cold for the products.",
    }[scenario]
    pick = {
        "THEFT_SUSPECTED": [OFFLINE, PREPARE_REPORT],
        "SUSPICIOUS_PERSON": [QUARANTINE, PREPARE_REPORT],
        "SPOOF_SUSPECTED": [QUARANTINE, PREPARE_REPORT],
        "DOOR_AJAR": [PREPARE_REPORT],
        "THERMAL_EXCURSION": [DIVERT, PREPARE_REPORT],
        "FREEZE_RISK": [DIVERT, PREPARE_REPORT],
    }[scenario]
    return [{"action": a, "reason": why} for a in pick if a in menu]


def clean(proposals: Any, menu: list[str]) -> list[dict[str, str]]:
    """Keep only well-formed proposals for allowed actions: no unknown actions, no repeats, short reasons."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for p in proposals if isinstance(proposals, list) else []:
        if not isinstance(p, dict) or p.get("action") not in menu or p["action"] in seen:
            continue
        reason = " ".join(str(p.get("reason", "")).split())[:MAX_REASON]
        if not reason:
            continue
        seen.add(p["action"])
        out.append({"action": p["action"], "reason": reason})
    return out[:MAX_PROPOSALS]


class Queue:
    """Stored in the database's key-value table so it survives a restart and is part of the same audit trail."""

    KEY = "approvals"

    def __init__(self, store):
        self.store = store

    def _load(self) -> dict[str, Any]:
        return self.store.kv_get(self.KEY, {"seq": 0, "items": []})

    def _expire(self, doc: dict[str, Any], now: float) -> None:
        for it in doc["items"]:
            if it["status"] == "pending" and now - it["created"] > EXPIRY_S:
                it.update(status="expired", decided_by="system", decided_at=now)

    def list(self, now: float) -> list[dict[str, Any]]:
        doc = self._load()
        self._expire(doc, now)
        self.store.kv_set(self.KEY, doc)
        return doc["items"]

    def pending(self, now: float) -> list[dict[str, Any]]:
        return [i for i in self.list(now) if i["status"] == "pending"]

    def for_audit(self, audit_id: int, now: float) -> list[dict[str, Any]]:
        return [i for i in self.pending(now) if i["audit_id"] == audit_id]

    def get(self, approval_id: int) -> dict[str, Any] | None:
        return next((i for i in self._load()["items"] if i["id"] == approval_id), None)

    def add(self, entry: dict[str, Any], proposals: list[dict[str, str]], source: str, now: float) -> list[dict[str, Any]]:
        """Queue the proposals, skipping any that is already waiting or was decided a moment ago."""
        doc = self._load()
        self._expire(doc, now)
        added = []
        for p in proposals:
            # waiting, or answered a moment ago (an expired proposal was never answered, so it does not hold a new one back)
            # a report belongs to one event, so "prepare report" is judged per alert; the other actions are per kind of situation
            if any(i["action"] == p["action"] and (p["action"] != PREPARE_REPORT or i["audit_id"] == entry["id"])
                   and (i["status"] == "pending" or (
                    i["status"] in ("approved", "rejected") and now - (i["decided_at"] or 0) < REPEAT_S))
                   for i in doc["items"]):
                continue
            doc["seq"] += 1
            item = {"id": doc["seq"], "audit_id": entry["id"], "scenario": entry["scenario"], "kind": entry["kind"],
                    "action": p["action"], "label": LABEL[p["action"]], "reason": p["reason"], "source": source,
                    "created": now, "expires": now + EXPIRY_S, "status": "pending",
                    "decided_by": None, "decided_at": None, "note": None}
            doc["items"].append(item)
            added.append(item)
        doc["items"] = doc["items"][-KEEP:]
        self.store.kv_set(self.KEY, doc)
        return added

    def decide(self, approval_id: int, approve: bool, who: str, now: float, note: str | None = None) -> dict[str, Any] | None:
        """Close a pending proposal. None if it does not exist or is no longer pending (decided, or expired)."""
        doc = self._load()
        self._expire(doc, now)
        item = next((i for i in doc["items"] if i["id"] == approval_id), None)
        if not item or item["status"] != "pending":
            self.store.kv_set(self.KEY, doc)
            return None
        item.update(status="approved" if approve else "rejected", decided_by=who, decided_at=now, note=note)
        self.store.kv_set(self.KEY, doc)
        return item
