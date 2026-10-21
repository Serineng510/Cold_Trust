"""Seal trust report: which integrity checks ran on a batch of readings, what each one found, and the overall result.

Built from the same rule results the engine already computed, so it adds no new decisions and no AI calls. The Thermal and
Incident agents receive it as evidence ("is this data safe to reason about?"), the dashboard shows it, and a person can check
every line against the raw readings.
"""
from __future__ import annotations

from typing import Any, Sequence

from .config import Settings
from .rules import Sample

CLEAN = "CLEAN"
SUSPICIOUS = "SUSPICIOUS"
NOT_TRUSTED = "NOT_TRUSTED"
LABEL = {
    CLEAN: "no integrity problems found",
    SUSPICIOUS: "suspicious, treat the readings with care",
    NOT_TRUSTED: "not trusted, readings may be fake or frozen",
}
# What each finding code does to the overall result.
FAIL_CODES = {"DOOR_OPEN_NO_THERMAL_RESPONSE", "HUMIDITY_NO_RESPONSE"}
WARN_CODES = {"LIGHT_ZERO_VARIANCE", "SENSOR_DISAGREEMENT", "HUMIDITY_TEMP_MISMATCH"}


def _check(cid: str, name: str, status: str, detail: str) -> dict[str, str]:
    """status: pass | warn | fail | skip (could not be run, and why)."""
    return {"id": cid, "name": name, "status": status, "detail": detail}


def build(cfg: Settings, *, sig: str, seq: int, seq_state: str, last_seq: int | None, history: Sequence[Sample],
          codes: set[str], humidity: str | None, humidity_skip: str | None, simulated: list[str]) -> dict[str, Any]:
    """`humidity` is rules.humidity_response(); `humidity_skip` says why it was not run (mocked readings)."""
    latest = history[-1]
    checks: list[dict[str, str]] = [
        _check("signature", "Signature", "pass", f"HMAC-SHA256 matches the device key (signature {sig[:8]}…)"),
        _check("sequence", "Sequence number", "warn" if seq_state == "gap" else "pass",
               f"{seq} follows {last_seq}" if seq_state == "ok" and last_seq is not None
               else f"{seq} skipped ahead of {last_seq}; earlier pings may have been lost" if seq_state == "gap"
               else f"{seq} is the first batch from this device"),
        _check("fresh", "New readings", "pass", "newer than everything already stored (not a replayed batch)"),
        _check("clock", "Time window", "pass" if cfg.strict_time else "skip",
               f"timestamp within {cfg.ts_window_s:.0f}s of server time" if cfg.strict_time
               else "not checked in timelapse simulation"),
    ]
    zv = cfg.zero_variance_min_samples
    if "LIGHT_ZERO_VARIANCE" in codes:
        checks.append(_check("light_variance", "Light jitter", "warn",
                             f"the last {zv} light readings are identical; a real light sensor jitters"))
    elif len(history) >= zv:
        checks.append(_check("light_variance", "Light jitter", "pass", f"light varies across the last {zv} readings"))
    else:
        checks.append(_check("light_variance", "Light jitter", "skip", f"needs {zv} readings, have {len(history)}"))

    if "DOOR_OPEN_NO_THERMAL_RESPONSE" in codes:
        checks.append(_check("door_vs_temp", "Door versus temperature", "fail",
                             f"door looked open over {cfg.door_open_contradiction_s:.0f}s in {cfg.ambient_c:.0f}°C air "
                             "but the temperature did not move"))
    else:
        checks.append(_check("door_vs_temp", "Door versus temperature", "pass" if latest.light_pct >= cfg.light_bright_pct
                             else "skip", "temperature behaves as expected while the door is open"
                             if latest.light_pct >= cfg.light_bright_pct else "door closed, nothing to compare"))

    if latest.temp2_c is None:
        checks.append(_check("second_thermometer", "Second thermometer", "skip", "not fitted on this node"))
    elif "SENSOR_DISAGREEMENT" in codes:
        checks.append(_check("second_thermometer", "Second thermometer", "warn",
                             f"the two thermometers differ by more than {cfg.temp_cross_check_c:.0f}°C"))
    else:
        checks.append(_check("second_thermometer", "Second thermometer", "pass", "both thermometers agree"))

    uncal = "" if cfg.hum_calibrated else " (humidity threshold not yet calibrated on the hardware)"
    if humidity_skip:
        checks.append(_check("humidity", "Humidity response", "skip", humidity_skip))
    elif humidity == "both_flat":
        checks.append(_check("humidity", "Humidity response", "fail",
                             f"door open over {cfg.door_open_contradiction_s:.0f}s but neither temperature nor humidity "
                             f"moved{uncal}"))
    elif humidity == "hum_only":
        checks.append(_check("humidity", "Humidity response", "warn",
                             "humidity moved while the temperature stayed flat; the two sensors contradict each other"
                             + uncal))
    elif humidity == "temp_only":
        checks.append(_check("humidity", "Humidity response", "pass",
                             f"temperature moved, humidity moved less than {cfg.hum_move_pct:.0f}%RH (noted only){uncal}"))
    elif humidity == "both_move":
        checks.append(_check("humidity", "Humidity response", "pass",
                             f"temperature and humidity both moved with the open door{uncal}"))
    else:
        checks.append(_check("humidity", "Humidity response", "skip", "door not open long enough to compare"))

    statuses = {c["status"] for c in checks}
    result = NOT_TRUSTED if "fail" in statuses else SUSPICIOUS if "warn" in statuses else CLEAN
    label = LABEL[result] + (f" (contains simulated {' + '.join(simulated)})" if simulated else "")
    return {"result": result, "label": label, "checks": checks, "simulated": simulated,
            "ran": sum(1 for c in checks if c["status"] != "skip"), "total": len(checks)}


def data_check_line(report: dict[str, Any] | None) -> list[str]:
    """The one short Telegram line, present only when the Seal agent found something wrong with the data itself."""
    if not report or report["result"] == CLEAN:
        return []
    return [f"Data check: {report['label']}"]


def for_agents(report: dict[str, Any]) -> dict[str, Any]:
    """The short form the Thermal and Incident agents receive: the result plus only the checks that need attention."""
    return {"result": report["label"],
            "checks_run": report["ran"], "checks_total": report["total"],
            "problems": [f"{c['name']}: {c['detail']}" for c in report["checks"] if c["status"] in ("warn", "fail")],
            "not_run": [f"{c['name']}: {c['detail']}" for c in report["checks"] if c["status"] == "skip"]}
