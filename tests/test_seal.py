"""Seal trust report: the humidity response check, the report on each log entry, and what the other agents receive."""
import pytest

from backend import rules, trust
from backend.config import Settings
from backend.engine import Engine
from backend.llm import unsupported_numbers
from backend.schemas import IngestIn
from backend.seed import seed
from backend.store import Store
from sim.sim import SECRET, build_payload

T0 = 1_800_000_000
BRIGHT, DARK = 900, 20


@pytest.fixture()
def eng():
    store = Store(":memory:")
    seed(store)
    return Engine(store, Settings(strict_time=False, device_secret=SECRET))


def S(t, temp=25.0, hum=50.0, light=BRIGHT):
    return rules.Sample(t, temp, hum, light)


def door_open(temp_fn, hum_fn, light=BRIGHT, n=41, step=10):
    return [S(i * step, temp_fn(i), hum_fn(i), light) for i in range(n)]   # 400 s of readings


# ---- the rule on its own
def test_humidity_response_needs_the_door_open_long_enough():
    assert rules.humidity_response(door_open(lambda i: 25, lambda i: 50, light=DARK), 30, 300) is None
    assert rules.humidity_response(door_open(lambda i: 25, lambda i: 50, n=10), 30, 300) is None   # 90 s only


@pytest.mark.parametrize("temp_fn, hum_fn, expected", [
    (lambda i: 25, lambda i: 50, "both_flat"),
    (lambda i: 25, lambda i: 50 + i // 5, "hum_only"),                  # humidity rises 8 %RH, temperature stays put
    (lambda i: 25 + i // 10, lambda i: 50, "temp_only"),                # temperature rises 4 C, humidity stays put
    (lambda i: 25 + i // 10, lambda i: 50 + i // 5, "both_move"),
])
def test_humidity_response_cases(temp_fn, hum_fn, expected):
    assert rules.humidity_response(door_open(temp_fn, hum_fn), 30, 300, hum_move_pct=3) == expected


def test_humidity_threshold_is_the_one_asked_for():
    s = door_open(lambda i: 25, lambda i: 50 + i // 20)                 # moves 2 %RH
    assert rules.humidity_response(s, 30, 300, hum_move_pct=3) == "both_flat"
    assert rules.humidity_response(s, 30, 300, hum_move_pct=2) == "hum_only"


# ---- through the engine
def batch(eng, seq, temp_fn, hum_fn, start=0, n=41, demo=False, sensors=None, light_fn=None):
    light_fn = light_fn or (lambda i: BRIGHT + i % 3)                   # a real light sensor jitters
    samples = [{"t": T0 + start + i * 10, "temp_c": temp_fn(i), "hum": hum_fn(i), "light": light_fn(i)} for i in range(n)]
    p = build_payload(seq, samples[-1]["t"], samples)
    p["demo"], p["demo_sensors"] = demo, sensors or []
    return eng.ingest(IngestIn(**p))


def check(entry, cid):
    return next(c for c in entry["seal"]["checks"] if c["id"] == cid)


def test_clean_batch_reports_no_problems_and_says_what_was_not_run(eng):
    r = batch(eng, 1, lambda i: 3, lambda i: 55 + i % 3, light_fn=lambda i: DARK + i % 3)
    seal = r["entry"]["seal"]
    assert seal["result"] == trust.CLEAN and "no integrity problems" in seal["label"]
    assert check(r["entry"], "signature")["status"] == "pass"
    assert check(r["entry"], "second_thermometer") == {
        "id": "second_thermometer", "name": "Second thermometer", "status": "skip", "detail": "not fitted on this node"}
    assert check(r["entry"], "humidity")["status"] == "skip"           # door closed: nothing to compare
    assert seal["ran"] < seal["total"]


def test_frozen_temperature_and_humidity_is_not_trusted(eng):
    r = batch(eng, 1, lambda i: 25, lambda i: 50)
    assert "HUMIDITY_NO_RESPONSE" in {f["code"] for f in r["entry"]["findings"]}
    assert r["entry"]["seal"]["result"] == trust.NOT_TRUSTED
    assert check(r["entry"], "humidity")["status"] == "fail"
    assert "not yet calibrated" in check(r["entry"], "humidity")["detail"]


def test_humidity_moving_without_temperature_is_suspicious(eng):
    r = batch(eng, 1, lambda i: 25, lambda i: 50 + i // 5)
    assert "HUMIDITY_TEMP_MISMATCH" in {f["code"] for f in r["entry"]["findings"]}
    assert r["entry"]["seal"]["result"] == trust.SUSPICIOUS


def test_temperature_moving_without_humidity_is_only_a_note(eng):
    r = batch(eng, 1, lambda i: 25 + i // 10, lambda i: 50)
    assert not {"HUMIDITY_NO_RESPONSE", "HUMIDITY_TEMP_MISMATCH"} & {f["code"] for f in r["entry"]["findings"]}
    assert check(r["entry"], "humidity")["status"] == "pass"
    assert "noted only" in check(r["entry"], "humidity")["detail"]


def test_calibrated_flag_removes_the_uncalibrated_note():
    cfg = Settings(strict_time=False, device_secret=SECRET, hum_calibrated=True)
    store = Store(":memory:")
    seed(store)
    r = batch(Engine(store, cfg), 1, lambda i: 25, lambda i: 50)
    assert "not yet calibrated" not in check(r["entry"], "humidity")["detail"]


def test_mocked_readings_skip_the_humidity_check_and_say_simulated(eng):
    r = batch(eng, 1, lambda i: 25, lambda i: 50, demo=True, sensors=["temp"])
    assert "HUMIDITY_NO_RESPONSE" not in {f["code"] for f in r["entry"]["findings"]}
    assert check(r["entry"], "humidity")["status"] == "skip" and "simulated" in check(r["entry"], "humidity")["detail"]
    assert r["entry"]["seal"]["simulated"] == ["temp"] and "simulated temp" in r["entry"]["seal"]["label"]
    # the mock is switched off, but the window still holds mocked readings, so the check stays off for one window
    later = batch(eng, 2, lambda i: 25, lambda i: 50, start=410, n=11)      # ends 110 s after the last mocked sample
    assert check(later["entry"], "humidity")["status"] == "skip"
    # a full window of real readings later it is back on
    real = batch(eng, 3, lambda i: 25, lambda i: 50, start=1000)
    assert check(real["entry"], "humidity")["status"] == "fail"


def test_a_second_thermometer_that_disagrees_is_suspicious(eng):
    samples = [{"t": T0 + i * 10, "temp_c": 3, "temp2_c": 9.0, "hum": 55 + i % 3, "light": DARK + i % 3} for i in range(5)]
    p = build_payload(1, samples[-1]["t"], samples)
    r = eng.ingest(IngestIn(**p))
    assert check(r["entry"], "second_thermometer")["status"] == "warn"
    assert r["entry"]["seal"]["result"] == trust.SUSPICIOUS


def test_flat_light_is_suspicious(eng):
    r = batch(eng, 1, lambda i: 3, lambda i: 55 + i % 3, light_fn=lambda i: DARK)
    assert check(r["entry"], "light_variance")["status"] == "warn"


# ---- what the other agents and Telegram get
def test_agents_receive_only_the_problems_and_what_was_not_run(eng):
    r = batch(eng, 1, lambda i: 25, lambda i: 50)
    brief = trust.for_agents(r["entry"]["seal"])
    assert brief["result"] == r["entry"]["seal"]["label"]
    assert len(brief["problems"]) == 1 and brief["problems"][0].startswith("Humidity response")
    assert any(x.startswith("Second thermometer") for x in brief["not_run"])


def test_agent_text_may_quote_numbers_from_the_seal_report(eng):
    r = batch(eng, 1, lambda i: 25, lambda i: 50)
    ctx = {"data_check_by_seal_agent": trust.for_agents(r["entry"]["seal"])}
    assert unsupported_numbers(["The door was open for over 300 seconds with flat readings."], ctx) == []
    assert unsupported_numbers(["The door was open for 999 seconds."], ctx) == ["999"]


def test_telegram_line_only_when_the_data_is_not_clean(eng):
    clean = batch(eng, 1, lambda i: 3, lambda i: 55 + i % 3, light_fn=lambda i: DARK + i % 3)
    assert trust.data_check_line(clean["entry"]["seal"]) == []
    bad = batch(eng, 2, lambda i: 25, lambda i: 50, start=500)
    assert trust.data_check_line(bad["entry"]["seal"]) == ["Data check: not trusted, readings may be fake or frozen"]
    assert trust.data_check_line(None) == []


def test_the_report_is_stored_with_the_log_entry(eng):
    r = batch(eng, 1, lambda i: 25, lambda i: 50)
    stored = eng.store.audit_get(r["entry"]["id"])
    assert stored["seal"]["result"] == r["entry"]["seal"]["result"] == trust.NOT_TRUSTED
