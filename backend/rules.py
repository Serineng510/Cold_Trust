"""Deterministic integrity and detection rules.

Everything here is pure (no I/O, no clock) so it can be unit-tested and so the
LLM never decides *whether* something is wrong, only what it means.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Iterable, Sequence

LIGHT_ADC_MAX = 1023.0


@dataclass(frozen=True)
class Sample:
    t: float          # epoch seconds
    temp_c: float
    hum: float
    light_raw: float  # 0..1023, higher = brighter (firmware inverts if the module is active-low)
    temp2_c: float | None = None  # optional independent thermometer (e.g. DS18B20), per CISA guidance

    @property
    def light_pct(self) -> float:
        return max(0.0, min(100.0, self.light_raw / LIGHT_ADC_MAX * 100.0))


# ---------------------------------------------------------------- signatures

def canonical(device_id: str, seq: int, ts: int, ping_type: str, trigger: str | None,
              samples: Iterable[Sample]) -> str:
    """String the device signs. Kept trivial so the Arduino sketch can build it."""
    body = ";".join(f"{int(s.t)}:{s.temp_c:.1f}:{s.hum:.0f}:{int(s.light_raw)}"
                    + (f":{s.temp2_c:.1f}" if s.temp2_c is not None else "") for s in samples)
    return f"{device_id}|{seq}|{ts}|{ping_type}|{trigger or '-'}|{body}"


def sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify(secret: str, message: str, signature: str) -> bool:
    return hmac.compare_digest(sign(secret, message), (signature or "").lower())


# ---------------------------------------------------------------- features

def slope_c_per_min(samples: Sequence[Sample], window_s: float = 240, min_span_s: float = 60) -> float | None:
    """Least-squares temperature slope over the trailing window, in °C/min.

    Returns None when there is not enough data to say anything. A regression
    (not last-minus-first) is used because the DHT11 only reports whole degrees.
    """
    if not samples:
        return None
    end = samples[-1].t
    pts = [s for s in samples if s.t >= end - window_s]
    if len(pts) < 3 or pts[-1].t - pts[0].t < min_span_s:
        return None
    n = len(pts)
    mt = sum(p.t for p in pts) / n
    mv = sum(p.temp_c for p in pts) / n
    den = sum((p.t - mt) ** 2 for p in pts)
    if den == 0:
        return None
    return sum((p.t - mt) * (p.temp_c - mv) for p in pts) / den * 60.0


def step_change_c(samples: Sequence[Sample], window_s: float = 60) -> float:
    """Signed change (newest - oldest) inside the trailing window."""
    if len(samples) < 2:
        return 0.0
    end = samples[-1].t
    pts = [s for s in samples if s.t >= end - window_s]
    if len(pts) < 2:
        return 0.0
    return pts[-1].temp_c - pts[0].temp_c


def minutes_outside(samples: Sequence[Sample], lo: float, hi: float, max_gap_s: float = 120) -> float:
    """Cumulative minutes spent outside [lo, hi]. Gaps are capped so a dropout is not billed in full."""
    total = 0.0
    for a, b in zip(samples, samples[1:]):
        if a.temp_c < lo or a.temp_c > hi:
            total += min(max(b.t - a.t, 0.0), max_gap_s)
    return total / 60.0


def peak_deviation_c(samples: Sequence[Sample], lo: float, hi: float) -> float:
    worst = 0.0
    for s in samples:
        if s.temp_c > hi:
            worst = max(worst, s.temp_c - hi)
        elif s.temp_c < lo:
            worst = max(worst, lo - s.temp_c)
    return worst


def out_of_range_now(samples: Sequence[Sample], lo: float, hi: float, consecutive: int = 2) -> bool:
    """True when the newest `consecutive` samples are all outside [lo, hi].

    The DHT11 is only accurate to +/-2 C, so a single stray reading must not flip a status.
    """
    tail = samples[-consecutive:]
    return bool(tail) and all(s.temp_c < lo or s.temp_c > hi for s in tail)


def out_of_limits_run_s(samples: Sequence[Sample], limits: Sequence[tuple[float, float]]) -> float:
    """Seconds the hold has been outside the limits of at least one package, without a break, up to the newest sample."""
    def out(s: Sample) -> bool:
        return any(s.temp_c < lo or s.temp_c > hi for lo, hi in limits)
    if not samples or not out(samples[-1]):
        return 0.0
    start = samples[-1].t
    for s in reversed(samples):
        if not out(s):
            break
        start = s.t
    return samples[-1].t - start


def is_bright(sample: Sample, threshold_pct: float) -> bool:
    return sample.light_pct >= threshold_pct


def bright_duration_s(samples: Sequence[Sample], threshold_pct: float) -> float:
    """Length of the current unbroken bright run ending at the newest sample."""
    if not samples or not is_bright(samples[-1], threshold_pct):
        return 0.0
    start = samples[-1].t
    for s in reversed(samples):
        if not is_bright(s, threshold_pct):
            break
        start = s.t
    return samples[-1].t - start


# ---------------------------------------------------------------- trust checks

def zero_variance_light(samples: Sequence[Sample], min_samples: int = 10) -> bool:
    """A real analog LDR always jitters a few ADC counts; a perfectly flat series is suspicious.

    A reading pinned at the ADC rails (0 or 1023) is excluded: many light modules saturate
    legitimately in a dark hold, and flagging that would be a constant false positive.
    """
    vals = [s.light_raw for s in samples[-min_samples:]]
    if len(vals) < min_samples or max(vals) != min(vals):
        return False
    return 0 < vals[0] < LIGHT_ADC_MAX


def door_open_without_thermal_response(samples: Sequence[Sample], ambient_c: float, bright_pct: float,
                                       min_open_s: float) -> bool:
    """Light says the door has been open a while in hot weather, yet the hold temperature has not moved."""
    if bright_duration_s(samples, bright_pct) < min_open_s:
        return False
    window = [s for s in samples if s.t >= samples[-1].t - min_open_s]
    temps = [s.temp_c for s in window]
    if len(temps) < 3:
        return False
    return (max(temps) - min(temps)) == 0 and max(temps) <= ambient_c - 10


def sensors_disagree(samples: Sequence[Sample], tolerance_c: float = 3.0, consecutive: int = 3) -> bool:
    """The two thermometers differ by more than their combined error for several samples in a row.

    DHT11 is +/-2 C and DS18B20 +/-0.5 C, so a gap above ~3 C means one of them is wrong, failing or tampered with.
    """
    tail = [s for s in samples if s.temp2_c is not None][-consecutive:]
    return len(tail) >= consecutive and all(abs(s.temp_c - s.temp2_c) > tolerance_c for s in tail)


def sequence_ok(last_seq: int | None, seq: int) -> str:
    """'ok' | 'replay' (not increasing) | 'gap' (skipped, probably lost pings)."""
    if last_seq is None:
        return "ok"
    if seq <= last_seq:
        return "replay"
    if seq > last_seq + 1:
        return "gap"
    return "ok"


def timestamps_fresh(samples: Sequence[Sample], last_sample_t: float | None) -> bool:
    """False if the newest sample is not newer than what we already stored (replayed batch)."""
    if not samples or last_sample_t is None:
        return True
    return samples[-1].t > last_sample_t


# ---------------------------------------------------------------- risk

def risk_score(*, budget_consumed: float, peak_dev_c: float, rate_c_per_min: float | None,
               light_sensitive_exposure: bool, data_trust_low: bool) -> int:
    """0-100. Weights are a starting point; see PLAN.md section 8.3."""
    budget = min(max(budget_consumed, 0.0), 1.0)
    peak = min(peak_dev_c / 10.0, 1.0)
    rate = min(abs(rate_c_per_min or 0.0) / 2.0, 1.0)
    score = 50 * budget + 20 * peak + 15 * (1 if light_sensitive_exposure else 0) + 10 * rate \
        + 5 * (1 if data_trust_low else 0)
    return int(round(min(score, 100)))
