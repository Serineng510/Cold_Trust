"""Device + vehicle simulator.

Produces the same signed payloads the ESP8266 firmware sends, so the backend and
dashboard can be exercised without hardware (and as a safety net on demo day).

    python -m sim.sim --list
    python -m sim.sim door_ajar                 # real time, server clock must match
    python -m sim.sim compressor_fail --timelapse   # run the server with COLDTRUST_STRICT_TIME=0
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from backend import rules
from backend.seed import GEOFENCES, ROUTE

DEVICE_ID = "CC-001"
from backend.config import load as _load_settings  # noqa: E402 (reads cold-chain/.env)

SECRET = _load_settings().device_secret  # same key the backend and firmware use
DT = 5  # detection sample spacing, seconds
BATCH_S = 60  # demo-mode routine ping spacing

DARK = 20       # raw ADC in a closed hold (~2%)
BRIGHT = 720    # raw ADC with the door open (~70%)


@dataclass
class Step:
    at: float                       # virtual seconds from scenario start when this is sent
    samples: list[dict]
    ping_type: str = "ROUTINE"
    trigger: str | None = None
    vehicle: dict | None = None
    camera: dict | None = None
    replay_of_previous: bool = False  # resend the previous payload verbatim (replay attack)


def build_payload(seq: int, ts: int, samples: list[dict], ping_type: str = "ROUTINE",
                  trigger: str | None = None, device_id: str = DEVICE_ID, secret: str = SECRET) -> dict:
    ss = [rules.Sample(s["t"], s["temp_c"], s["hum"], s["light"], s.get("temp2_c")) for s in samples]
    sig = rules.sign(secret, rules.canonical(device_id, seq, ts, ping_type, trigger, ss))
    return {"device_id": device_id, "seq": seq, "ts": ts, "ping_type": ping_type, "trigger": trigger,
            "samples": samples, "sig": sig}


# ---------------------------------------------------------------- scenario building blocks

class Clock:
    """Generates samples on a fixed grid with per-sample functions of virtual time."""

    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)

    def window(self, end: float, temp_fn: Callable[[float], float], light_fn: Callable[[float], float],
               span: float = BATCH_S, t0: float = 0.0) -> list[dict]:
        out = []
        t = end - span + DT
        while t <= end + 1e-9:
            out.append({"t": int(t0 + t), "temp_c": round(temp_fn(t)),  # DHT11: whole degrees
                        "hum": 55 + self.rng.randint(-2, 2),
                        "light": max(0, min(1023, int(light_fn(t) + self.rng.randint(-4, 4))))})
            t += DT
        return out


def _ramp(t, t_a, t_b, v_a, v_b):
    if t <= t_a:
        return v_a
    if t >= t_b:
        return v_b
    return v_a + (v_b - v_a) * (t - t_a) / (t_b - t_a)


def normal_drive(clock: Clock, minutes: int = 6) -> list[Step]:
    veh = {"speed_mph": 58, "zone": None, "authorised_stop": False, "route_status": "En route"}
    return [Step(at=m * BATCH_S,
                 samples=clock.window(m * BATCH_S, lambda t: 3 + 0.3 * ((t // 60) % 2), lambda t: DARK),
                 vehicle=veh if m == 1 else None)
            for m in range(1, minutes + 1)]


def door_ajar(clock: Clock) -> list[Step]:
    steps = normal_drive(clock, 3)
    t_open = 3 * BATCH_S + 20
    temp = lambda t: _ramp(t, t_open, t_open + 240, 3, 10)
    light = lambda t: BRIGHT if t_open <= t < t_open + 90 else DARK
    steps.append(Step(at=t_open + DT, samples=clock.window(t_open + DT, temp, light, span=30),
                      ping_type="EVENT", trigger="LIGHT"))
    for m in range(4, 9):
        steps.append(Step(at=m * BATCH_S + 20, samples=clock.window(m * BATCH_S + 20, temp, light)))
    return steps


def theft(clock: Clock) -> list[Step]:
    steps = normal_drive(clock, 3)
    t_open = 3 * BATCH_S + 20
    temp = lambda t: _ramp(t, t_open, t_open + 180, 3, 8)
    light = lambda t: BRIGHT if t >= t_open else DARK
    steps.append(Step(at=t_open - 1, samples=[], camera={"person_present": True,
                                                         "description": "A person is standing in the open cargo door"}))
    steps.append(Step(at=t_open + DT, samples=clock.window(t_open + DT, temp, light, span=30),
                      ping_type="EVENT", trigger="LIGHT"))
    return [s for s in steps if s.samples or s.camera]


def authorised_stop(clock: Clock) -> list[Step]:
    steps = normal_drive(clock, 2)
    t = 2 * BATCH_S + 10
    steps.append(Step(at=t, samples=[], vehicle={"speed_mph": 0, "zone": "Memphis Dock 4",
                                                 "authorised_stop": True, "route_status": "Unloading"}))
    light = lambda x: BRIGHT if x >= t else DARK
    steps.append(Step(at=t + DT, samples=clock.window(t + DT, lambda x: 3, light, span=30),
                      ping_type="EVENT", trigger="LIGHT"))
    return [s for s in steps if s.samples or s.vehicle]


def compressor_fail(clock: Clock) -> list[Step]:
    temp = lambda t: _ramp(t, 4 * 60, 30 * 60, 3, 13)
    return [Step(at=m * BATCH_S, samples=clock.window(m * BATCH_S, temp, lambda t: DARK),
                 ping_type="EVENT" if m in (14, 20) else "ROUTINE", trigger="RATE" if m in (14, 20) else None,
                 vehicle={"speed_mph": 61, "route_status": "En route", "authorised_stop": False} if m == 1 else None)
            for m in range(1, 46)]


def freeze(clock: Clock) -> list[Step]:
    steps = normal_drive(clock, 2)
    t = 2 * BATCH_S + 10
    temp = lambda x: _ramp(x, t, t + 60, 4, 0)
    steps.append(Step(at=t + 60, samples=clock.window(t + 60, temp, lambda x: DARK, span=60),
                      ping_type="EVENT", trigger="STEP"))
    return steps


def replay(clock: Clock) -> list[Step]:
    steps = normal_drive(clock, 3)
    steps.append(Step(at=3 * BATCH_S + 10, samples=[], replay_of_previous=True))
    return steps


def spoof(clock: Clock) -> list[Step]:
    """Door reads open for 7 min in hot weather, yet the hold temperature never moves."""
    steps = normal_drive(clock, 1)
    veh = {"speed_mph": 60, "authorised_stop": False, "zone": None}
    for m in range(2, 10):
        steps.append(Step(at=m * BATCH_S, samples=clock.window(m * BATCH_S, lambda t: 3, lambda t: BRIGHT)))
    steps[0].vehicle = veh
    return steps


def stopped_theft(clock: Clock) -> list[Step]:
    """Route active, the truck stops mid-route (0 mph, no depot or dock) and the cargo door opens."""
    steps = normal_drive(clock, 2)
    t = 2 * BATCH_S + 20
    steps.append(Step(at=t, samples=[], vehicle={"speed_mph": 0, "route_status": "En route", "authorised_stop": False}))
    light = lambda x: BRIGHT if x >= t + 10 else DARK
    steps.append(Step(at=t + 15, samples=clock.window(t + 15, lambda x: 3, light, span=30),
                      ping_type="EVENT", trigger="LIGHT"))
    return [st for st in steps if st.samples or st.vehicle]


def sensor_disagree(clock: Clock) -> list[Step]:
    """A second thermometer (DS18B20) is fitted. The DHT11 keeps saying 3 C while the DS18B20 climbs to 10 C,
    as if the DHT11 had been swapped or fed a fake signal. CISA: use an independent thermometer to verify."""
    steps = []
    for m in range(1, 7):
        samples = clock.window(m * BATCH_S, lambda t: 3, lambda t: DARK)
        for x in samples:
            x["temp2_c"] = round(3.2 + max(0.0, (x["t"] - 120) / 240 * 7), 1)
        steps.append(Step(at=m * BATCH_S, samples=samples,
                          vehicle={"speed_mph": 59, "authorised_stop": False, "zone": None} if m == 1 else None))
    return steps


SCENARIOS: dict[str, tuple[str, Callable[[Clock], list[Step]]]] = {
    "normal": ("Six quiet minutes on the road", lambda c: normal_drive(c, 6)),
    "door_ajar": ("Door opens while moving, temperature climbs", door_ajar),
    "theft": ("Door opens, person seen in camera, outside any authorised stop", theft),
    "authorised_stop": ("Door opens at an authorised dock (should NOT quarantine)", authorised_stop),
    "compressor_fail": ("Slow temperature rise over 45 virtual minutes; budgets run out", compressor_fail),
    "freeze": ("Sudden drop from 4 C to 0 C (DHT11 floor)", freeze),
    "replay": ("Attacker resends a captured payload", replay),
    "spoof": ("Door open 7 min yet temperature perfectly flat", spoof),
    "sensor_disagree": ("Second thermometer disagrees with the DHT11 (one sensor faked or failing)", sensor_disagree),
    "stopped_theft": ("Route active, truck stops mid-route at 0 mph and the door opens (theft rule)", stopped_theft),
}


# ---------------------------------------------------------------- runner

def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-ColdTrust-Key": SECRET})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # surface the server's reason
        return {"http_error": e.code, "body": e.read().decode()[:300]}


_CLOCK_FILE = os.path.join(os.path.dirname(__file__), ".clock")


def _read_clock() -> float:
    try:
        return float(open(_CLOCK_FILE).read())
    except (OSError, ValueError):
        return 0.0


def _write_clock(v: float) -> None:
    with open(_CLOCK_FILE, "w") as f:
        f.write(str(v))


def position_along_route(fraction: float) -> tuple[float, float]:
    """Point at `fraction` (0..1) of the planned route, by straight segments."""
    import math
    seg = [math.dist(a, b) for a, b in zip(ROUTE, ROUTE[1:])]
    target = max(0.0, min(1.0, fraction)) * sum(seg)
    for (a, b), length in zip(zip(ROUTE, ROUTE[1:]), seg):
        if target <= length:
            f = target / length if length else 0
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        target -= length
    return ROUTE[-1]


# Where along the route each scenario plays out (fraction of the trip).
SCENARIO_LEG = {"normal": (0.05, 0.20), "door_ajar": (0.25, 0.35), "theft": (0.32, 0.34),
                "authorised_stop": (0.97, 1.0), "compressor_fail": (0.36, 0.70), "freeze": (0.40, 0.45),
                "replay": (0.20, 0.24), "spoof": (0.50, 0.58), "sensor_disagree": (0.60, 0.66),
                "stopped_theft": (0.12, 0.13)}


def _vehicle_at(name: str, step: Step, total: float) -> dict:
    zone = (step.vehicle or {}).get("zone")
    fence = next((g for g in GEOFENCES if g["name"] == zone), None)
    if fence:
        lat, lon = fence["lat"], fence["lon"]
    else:
        a, b = SCENARIO_LEG.get(name, (0.0, 1.0))
        lat, lon = position_along_route(a + (b - a) * (step.at / total if total else 0))
    return {**(step.vehicle or {}), "lat": round(lat, 5), "lon": round(lon, 5)}


def run(name: str, base: str, timelapse: bool) -> None:
    desc, build = SCENARIOS[name]
    steps = build(Clock())
    total = max(s.at for s in steps)
    if timelapse:
        # Virtual time must keep moving forward across runs or the server's replay guard (rightly) rejects it.
        t0 = max(time.time() - total, _read_clock() + 60)
        _write_clock(t0 + total)
    else:
        t0 = time.time()
    print(f"[sim] {name}: {desc} ({len(steps)} steps, {total / 60:.0f} virtual min)"
          f"{' [timelapse: server needs COLDTRUST_STRICT_TIME=0]' if timelapse else ''}")
    seq = int(time.time() * 1000)  # monotonic across runs, so the server's replay guard stays happy
    last: dict | None = None
    for st in steps:
        if not timelapse:
            time.sleep(max(0.0, t0 + st.at - time.time()))
        _post(f"{base}/api/vehicle", _vehicle_at(name, st, total))
        if st.camera:
            _post(f"{base}/api/camera/observation", st.camera)
        if st.replay_of_previous and last:
            res = _post(f"{base}/api/telemetry", last)
            print(f"  t+{st.at:>5.0f}s  REPLAYED payload seq={last['seq']} -> {res.get('entry', res).get('kind', res)}")
            continue
        if not st.samples:
            continue
        for s in st.samples:
            s["t"] = int(t0 + s["t"]) if not timelapse else int(t0 + s["t"])
        seq += 1
        payload = build_payload(seq, int(t0 + st.at), st.samples, st.ping_type, st.trigger)
        res = _post(f"{base}/api/telemetry", payload)
        last = payload
        entry = res.get("entry", {})
        print(f"  t+{st.at:>5.0f}s  {st.ping_type:<7} temp={st.samples[-1]['temp_c']:>2}C light={st.samples[-1]['light']:>4}"
              f"  -> {entry.get('kind', res)}  {entry.get('verdict_label', '')}  risk={entry.get('risk_score', '')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS))
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--timelapse", action="store_true", help="send immediately with virtual timestamps")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.list or not a.scenario:
        for k, (d, _) in SCENARIOS.items():
            print(f"{k:<16} {d}")
        return 0
    run(a.scenario, a.url.rstrip("/"), a.timelapse)
    return 0


if __name__ == "__main__":
    sys.exit(main())
