"""What an AI agent is told about an alert. One function, used by the live system and by the evaluation (tools/eval_agents.py),
so the evaluation tests exactly the input the agents get in real use."""
from __future__ import annotations

from typing import Any

from . import approvals, trust
from .llm import agent_for


def build(entry: dict[str, Any], packages: list[dict[str, Any]], vehicle: dict[str, Any], report_ok: bool = True) -> dict[str, Any]:
    return {
        "scenario": entry["scenario"], "severity": entry["severity"], "trigger": entry.get("trigger"),
        "findings": entry["findings"], "readings": entry["params"],
        "data_check_by_seal_agent": trust.for_agents(entry["seal"]) if entry.get("seal") else None,
        "allowed_actions": approvals.allowed(entry, packages, vehicle, report_ok) if agent_for(entry["kind"]) == "incident" else None,
        "vehicle": {k: v for k, v in vehicle.items() if k in ("speed_kmh", "zone", "authorised_stop")},
        "status_decided_by_rules": entry["verdict_label"], "risk_score": entry["risk_score"],
        "products": [
            {"id": pk["id"], "description": pk["description"], "limits_c": [pk["temp_min_c"], pk["temp_max_c"]],
             "excursion_budget_min": pk["excursion_budget_min"], "minutes_outside_limits": pk["excursion_min"],
             "budget_used_pct": round((pk.get("budget_consumed") or 0) * 100),  # given, so the AI never has to calculate it
             "status": pk["status"], "risk_score": pk["risk_score"]}
            for pk in packages if pk["id"] in entry["affected"] or pk["status"] != "NORMAL"][:6],
    }
