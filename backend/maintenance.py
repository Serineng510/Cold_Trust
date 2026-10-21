"""Maintenance review: is the refrigeration unit weakening? Early-warning indicators from the sensor history.

Everything here is plain arithmetic on the stored readings (no model). The Thermal agent may then put the numbers into
words (llm.Explainer.maintenance), but it only ever sees this summary, never raw readings, and the rule-based text in
rule_summary() is always available when the AI is off, paused or out of free calls.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from . import rules
from .rules import Sample

MIN_RECOVERIES = 3      # fewer door openings than this cannot show a trend
MIN_SAMPLES = 60        # fewer readings than this (30 minutes) is too little to judge anything
TARGET_IN_RANGE = 90.0  # per cent of time the hold should sit inside its limits
SLOWER_RATIO = 1.3      # recovery this much slower than at the start counts as "getting slower"


def tz_from_offset(offset: str) -> timezone:
    sign = -1 if offset.startswith("-") else 1
    hh, _, mm = offset.lstrip("+-").partition(":")
    return timezone(sign * timedelta(hours=int(hh or 0), minutes=int(mm or 0)))


def recoveries(samples: Sequence[Sample], bright_pct: float) -> list[dict[str, Any]]:
    """After each door opening (light up through the threshold): minutes until the temperature is back near where it was."""
    out: list[dict[str, Any]] = []
    i = 1
    while i < len(samples):
        if samples[i].light_pct >= bright_pct and samples[i - 1].light_pct < bright_pct:
            start = samples[i].t
            before = sorted(s.temp_c for s in samples if start - 300 <= s.t < start)
            base = before[len(before) // 2] if before else samples[i - 1].temp_c
            j = i
            while j < len(samples) and samples[j].light_pct >= bright_pct:
                j += 1
            if j >= len(samples):
                break  # still open at the end of the window: nothing to measure yet
            end = samples[j].t
            back = next((s for s in samples[j:] if s.temp_c <= base + 0.5), None)
            out.append({"at": start, "minutes": None if back is None else round((back.t - end) / 60, 1)})
            i = j
        i += 1
    return out


def _trend(minutes: list[float]) -> str:
    """slower / faster / steady, comparing the first and last part of the period; unknown with too little data."""
    if len(minutes) < MIN_RECOVERIES:
        return "unknown"
    k = max(1, len(minutes) // 2) if len(minutes) >= 4 else 1
    first, last = statistics.mean(minutes[:k]), statistics.mean(minutes[-k:])
    if first > 0 and last > first * SLOWER_RATIO:
        return "slower"
    if first > 0 and last < first / SLOWER_RATIO:
        return "faster"
    return "steady"


def _stats(samples: Sequence[Sample], lo: float, hi: float, bright_pct: float) -> dict[str, Any]:
    temps = [s.temp_c for s in samples]
    rec = recoveries(samples, bright_pct)
    done = [r["minutes"] for r in rec if r["minutes"] is not None]
    return {
        "time_in_range_pct": round(sum(lo <= t <= hi for t in temps) / len(temps) * 100, 1) if temps else None,
        "peak_c": max(temps) if temps else None,
        "mean_c": round(statistics.mean(temps), 1) if temps else None,
        "door_openings": len(rec),
        "not_recovered": len(rec) - len(done),
        "avg_recovery_min": round(statistics.mean(done), 1) if done else None,
        "recovery_trend": _trend(done),
    }


def report(samples: Sequence[Sample], *, hours: int, hold_limits: Sequence[float], bright_pct: float,
           tz_offset: str = "+00:00") -> dict[str, Any]:
    """The full summary for the last `hours` of readings, overall and per local day, plus the early-warning indicators."""
    lo, hi = hold_limits
    tz = tz_from_offset(tz_offset)
    samples = list(samples)
    rep: dict[str, Any] = {
        "hours": hours, "samples": len(samples), "hold_limits_c": [lo, hi], "light_alarm_pct": bright_pct,
        "from": samples[0].t if samples else None, "to": samples[-1].t if samples else None,
        "enough_data": len(samples) >= MIN_SAMPLES, "overall": None, "per_day": [], "indicators": [],
    }
    if not samples:
        return rep
    rep["overall"] = _stats(samples, lo, hi, bright_pct)
    days: dict[str, list[Sample]] = {}
    for s in samples:
        days.setdefault(datetime.fromtimestamp(s.t, tz).strftime("%Y-%m-%d"), []).append(s)
    rep["per_day"] = [{"date": d, "samples": len(v), **{k: x for k, x in _stats(v, lo, hi, bright_pct).items()
                                                        if k in ("time_in_range_pct", "peak_c", "door_openings", "avg_recovery_min")}}
                      for d, v in sorted(days.items())]
    rep["days_covered"] = len(days)
    ov = rep["overall"]
    rep["recovery_enough"] = ov["door_openings"] - ov["not_recovered"] >= MIN_RECOVERIES
    slope = rules.slope_c_per_min(samples[-60:] if len(samples) > 60 else samples, 240)
    rep["current_slope_c_per_min"] = None if slope is None else round(slope, 2)
    rep["indicators"] = _indicators(rep, hi)
    return rep


def _indicators(rep: dict[str, Any], hi: float) -> list[dict[str, str]]:
    ov, out = rep["overall"], []
    if not rep["enough_data"]:
        return [{"tone": "info", "title": "Not enough history yet",
                 "detail": f"Only {rep['samples']} readings in this period; at least {MIN_SAMPLES} are needed."}]
    tir = ov["time_in_range_pct"]
    out.append({"tone": "warn" if tir < TARGET_IN_RANGE else "ok",
                "title": f"Time in range {tir:.0f}%" + (f" (target {TARGET_IN_RANGE:.0f}%)" if tir < TARGET_IN_RANGE else ""),
                "detail": "Investigate the excursions" if tir < TARGET_IN_RANGE else "Within target"})
    if ov["recovery_trend"] == "slower":
        out.append({"tone": "warn", "title": "Recovery after door openings is getting slower",
                    "detail": "Service the refrigeration unit before the next long haul"})
    elif not rep["recovery_enough"]:
        out.append({"tone": "info", "title": "Too few door openings to judge the cooling unit",
                    "detail": f"{ov['door_openings']} opening(s) in this period; at least {MIN_RECOVERIES} with a recovery are needed"})
    slope = rep.get("current_slope_c_per_min")
    if slope is not None and slope > 0.2:
        out.append({"tone": "warn", "title": f"Hold warming at +{slope} C/min", "detail": f"Check the unit; the upper limit is {hi} C"})
    if ov["not_recovered"]:
        out.append({"tone": "warn", "title": f"{ov['not_recovered']} door opening(s) never recovered within the period",
                    "detail": "The hold did not return to its earlier temperature"})
    if len(out) == 1 and out[0]["tone"] == "ok":
        out.append({"tone": "ok", "title": "No sign of weakening cooling", "detail": "Keep monitoring"})
    return out


def rule_summary(rep: dict[str, Any]) -> dict[str, Any]:
    """The same answer shape the AI gives, written by rules. Used when the AI is off, paused or out of free calls."""
    if not rep["enough_data"] or rep["overall"] is None:
        return {"assessment": "There is not enough sensor history in this period to judge the refrigeration unit.",
                "cooling_unit": "not enough data", "urgency": "none", "evidence": [i["title"] for i in rep["indicators"]][:4],
                "recommended_actions": ["Run a longer trip, then review again"]}
    ov = rep["overall"]
    slower = ov["recovery_trend"] == "slower"
    warm = (rep.get("current_slope_c_per_min") or 0) > 0.2
    low_tir = ov["time_in_range_pct"] < TARGET_IN_RANGE
    if not rep["recovery_enough"]:
        unit = "not enough data"
    else:
        unit = "possible weakening" if slower else "no sign of weakening"
    urgency = "service soon" if slower else ("monitor" if (low_tir or warm or ov["not_recovered"]) else "none")
    avg = ov["avg_recovery_min"]
    text = (f"Over the last {rep['hours']} h the hold was in range {ov['time_in_range_pct']:.0f}% of the time, peaking at "
            f"{ov['peak_c']:.1f} C, with {ov['door_openings']} door opening(s)"
            + (f" and an average recovery of {avg:g} min" if avg is not None else "") + ".")
    actions = {"service soon": ["Service the refrigeration unit before the next long haul", "Re-check after servicing"],
               "monitor": ["Keep monitoring the hold temperature", "Review again after the next trip"],
               "none": ["No action needed; keep monitoring"]}[urgency]
    return {"assessment": text, "cooling_unit": unit, "urgency": urgency,
            "evidence": [i["title"] for i in rep["indicators"]][:4], "recommended_actions": actions}


def report_gate(review: dict[str, Any] | None, incident_ts: float) -> tuple[bool, str]:
    """May an incident report be made for a TEMPERATURE event? Only when a maintenance review has shown that it is a maintenance
    matter: the AI wrote the review, there was enough history, it found the cooling unit possibly weakening, and the review's
    period covers the event. Returns (allowed, reason when not). Security events (person, theft, tampering) never use this."""
    if not review:
        return False, "No maintenance review has been done yet. Open Maintenance, choose a period and press Summarise with AI."
    if review.get("source") != "ai":
        return False, "The latest maintenance review was written by rules because the AI was unavailable. Run it again with the AI."
    if not review.get("enough_data"):
        return False, "The maintenance review did not have enough sensor history to judge the cooling unit."
    if review.get("cooling_unit") != "possible weakening":
        return False, (f"The maintenance review found: {review.get('cooling_unit')}. A temperature event is only reported "
                       "when the review shows the cooling unit may be failing.")
    if review.get("from") is None or not review["from"] <= incident_ts <= (review.get("to") or 0):
        return False, "The latest maintenance review does not cover the time of this event. Run a new review."
    return True, ""
