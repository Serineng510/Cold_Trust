"""Measure what ColdTrust adds over a plain threshold alarm, on the simulator's scenarios. No AI calls; deterministic.

    .venv\\Scripts\\python -m tools.eval_detection

The BASELINE is what a standard temperature logger with an alarm does: raise an alarm the moment a reading is outside the
hold's limits (2-8 C) or the light says the door is open (>= 30 %). It alarms immediately (the fastest a threshold alarm can be),
accepts every message, and has no idea of context. ColdTrust is the real engine, fed exactly the same messages.

Four things are measured, each as a plain count or a time from the simulated start of the scenario:
  false alarms          alarms raised when nothing is wrong (normal driving, a door opened at an authorised dock)
  time to first alert   seconds until the system first raises an alert, per scenario
  cargo protected       packages already held (under review or quarantined) at the moment of the first alert
  data attacks caught   replayed, spoofed or contradictory data: a threshold alarm has no way to see these
Results: docs/detection_results.md and docs/detection_results.json. The scenarios are simulated; the numbers say what the
rules do on those scenarios, not how often real incidents happen.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from backend.schemas import IngestIn
from backend.seed import VEHICLE
from sim.sim import SCENARIOS, Clock, build_payload
from tools.eval_agents import fresh_engine

ROOT = Path(__file__).resolve().parent.parent
T0 = 1_800_000_000
BASELINE_LIGHT_PCT = 30.0
QUIET = {"NORMAL", "AUTHORISED_HANDLING"}                       # ColdTrust scenarios that raise no alert
UNTRUSTED = {"SPOOF_SUSPECTED", "DATA_TRUST"}
NO_INCIDENT = {"normal", "authorised_stop"}                     # scenarios where nothing is wrong: any alarm is a false alarm
DATA_ATTACKS = {"replay": "replayed message", "spoof": "spoofed flat temperature", "sensor_disagree": "second thermometer disagrees"}
BASELINE_NAME = "plain threshold alarm"


def baseline_alarms(samples: list[dict[str, Any]]) -> bool:
    lo, hi = VEHICLE["hold_limits_c"]
    return any(s["temp_c"] < lo or s["temp_c"] > hi or s["light"] / 1023 * 100 >= BASELINE_LIGHT_PCT for s in samples)


def play(name: str) -> dict[str, Any]:
    eng = fresh_engine()
    out: dict[str, Any] = {"scenario": name, "baseline_first_s": None, "baseline_alarms": 0, "cold_first_s": None,
                           "cold_kind": None, "temp_at_first_alert": None, "cold_alerts": 0, "held_at_first_alert": 0, "untrusted_caught_s": None}
    last, seq = None, 0
    for st in SCENARIOS[name][1](Clock()):
        if st.vehicle:
            eng.set_vehicle(st.vehicle)
        if st.camera:
            eng.camera_observation(st.camera, T0 + st.at)
        if st.replay_of_previous and last:
            payload, samples = last, []                                # the attacker resends a captured message verbatim
        elif st.samples:
            samples = [dict(s) for s in st.samples]
            for s in st.samples:
                s["t"] += T0
            seq += 1
            payload = last = build_payload(seq, int(T0 + st.at), st.samples, st.ping_type, st.trigger)
        else:
            continue
        result = eng.ingest(IngestIn(**payload))
        e = result["entry"]
        if samples and baseline_alarms(samples):                      # the logger sees only fresh readings
            out["baseline_alarms"] += 1
            out["baseline_first_s"] = st.at if out["baseline_first_s"] is None else out["baseline_first_s"]
        alert = result["notify"] and (not result["accepted"] or e["scenario"] not in QUIET)
        if alert:
            out["cold_alerts"] += 1
            if out["cold_first_s"] is None:
                out["cold_first_s"], out["cold_kind"] = st.at, e["kind"]
                out["temp_at_first_alert"] = (e.get("params") or {}).get("temp_c")
                out["held_at_first_alert"] = sum(1 for p in eng.store.list_packages() if p["status"] != "NORMAL")
            if out["untrusted_caught_s"] is None and (e["scenario"] in UNTRUSTED or e["kind"].startswith("REJECTED_")):
                out["untrusted_caught_s"] = st.at
    out["packages"] = len(eng.store.list_packages())
    return out


def mmss(s: float | None) -> str:
    return "-" if s is None else f"{int(s) // 60}:{int(s) % 60:02d}"


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = {r["scenario"]: r for r in rows}
    quiet = [by[n] for n in NO_INCIDENT]
    incidents = [r for r in rows if r["scenario"] not in NO_INCIDENT and r["scenario"] not in DATA_ATTACKS]
    return {
        "false_alarms_baseline": sum(r["baseline_alarms"] for r in quiet),
        "false_alarms_coldtrust": sum(r["cold_alerts"] for r in quiet),
        "attacks_total": len(DATA_ATTACKS),
        "attacks_caught_coldtrust": sum(1 for n in DATA_ATTACKS if by[n]["untrusted_caught_s"] is not None),
        "incidents": len(incidents),
        "held_baseline": 0,                                              # an alarm holds nothing
        "held_coldtrust": sum(1 for r in incidents if r["held_at_first_alert"] > 0),
        "earlier": {r["scenario"]: r["baseline_first_s"] - r["cold_first_s"] for r in incidents
                    if r["baseline_first_s"] is not None and r["cold_first_s"] is not None
                    and r["baseline_first_s"] > r["cold_first_s"]},
    }


def render(rows: list[dict[str, Any]], s: dict[str, Any]) -> str:
    names = {r["scenario"]: SCENARIOS[r["scenario"]][0] for r in rows}
    lines = [
        "# Detection results: ColdTrust against a plain threshold alarm", "",
        "Both systems receive exactly the same simulated messages (10 scenarios from `sim/sim.py`, deterministic, no AI calls). "
        f"The baseline is a {BASELINE_NAME}: it alarms at once when a reading leaves the hold's limits "
        f"({VEHICLE['hold_limits_c'][0]:g}-{VEHICLE['hold_limits_c'][1]:g} C) or the light shows the door open "
        f"(>= {BASELINE_LIGHT_PCT:g}%), accepts every message, and knows nothing about context. That is the fastest a threshold "
        "alarm can be, so it is a fair, favourable baseline.", "",
        "## Summary", "",
        "| Measure | Plain threshold alarm | ColdTrust |", "|---|---|---|",
        f"| False alarms (normal driving + a door opened at an authorised dock) | {s['false_alarms_baseline']} | {s['false_alarms_coldtrust']} |",
        f"| Data attacks recognised (replay, spoofed flat temperature, contradicting thermometer) | 0 of {s['attacks_total']} | {s['attacks_caught_coldtrust']} of {s['attacks_total']} |",
        f"| Incident scenarios where cargo was already held when the first alert went out | 0 of {s['incidents']} | {s['held_coldtrust']} of {s['incidents']} |",
        "",
    ]
    if s["earlier"]:
        lines += ["| Slow failure: ColdTrust alerted earlier by | " + ", ".join(f"{n}: {mmss(v)} (min:s)" for n, v in s["earlier"].items()) + " |",
                  "|---|---|", ""]
    lines += ["## Per scenario", "",
              "| Scenario | What happens | Alarm (threshold) | Alert (ColdTrust) | ColdTrust says | Packages held at first alert | Untrusted data caught |",
              "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['scenario']} | {names[r['scenario']]} | {mmss(r['baseline_first_s'])} ({r['baseline_alarms']} batch(es)) | "
                     f"{mmss(r['cold_first_s'])} ({r['cold_alerts']} alert(s)) | {r['cold_kind'] or '-'} | "
                     f"{r['held_at_first_alert']} of {r['packages']} | {mmss(r['untrusted_caught_s'])} |")
    lines += ["", "Times are minutes:seconds from the start of the simulated scenario, when the system first has the data.", "",
              "## How to read this", "",
              "- **False alarms.** A door opened at an authorised dock is routine. A threshold alarm cannot tell, so it fires at every "
              "dock stop; ColdTrust checks the zone and the speed first and treats it as handling.",
              "- **Earlier on a slow failure.** ColdTrust judges each product against its own limits (for example 0-4 C for fish, "
              "2-6 C for dairy), while the baseline uses one hold limit. In a slowly warming hold the most sensitive product leaves "
              "its range first, so the alert comes before the single hold alarm.",
              "- **Not faster on a door event.** When the light changes, both systems know at the same moment. The difference is what "
              "happens next: ColdTrust holds the cargo and names the situation (theft, door ajar), the alarm only rings.",
              "- **Data attacks.** A replayed or forged message produces no threshold breach, so a plain alarm never sees it. "
              "ColdTrust rejects the replay and flags the flat-temperature and contradicting-sensor cases. In the spoof scenario the "
              "threshold alarm does ring for the open door, but it never notices that the temperature is fake; ColdTrust says so "
              "after the door has been open for 5 minutes with no temperature response.",
              "- **Cargo held.** A threshold alarm is assumed to hold no cargo: it rings and a person must act. ColdTrust's rules "
              "hold or quarantine the packages at the moment of the alert, and an admin can release them.",
              "- **Limits.** These are 10 simulated scenarios built by the same author as the rules, so they show what the rules do on "
              "known cases, not how often real incidents occur. The baseline is deliberately generous (instant alarm, no delay). The "
              "second-thermometer case needs a sensor that the prototype board does not yet have."]
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = [play(n) for n in SCENARIOS]
    s = summarise(rows)
    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    (out / "detection_results.md").write_text(render(rows, s), encoding="utf-8")
    (out / "detection_results.json").write_text(json.dumps({"summary": s, "scenarios": rows}, indent=1), encoding="utf-8")
    print(render(rows, s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
