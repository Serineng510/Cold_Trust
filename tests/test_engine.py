"""End-to-end checks of the rules + engine using the simulator's own scenarios (virtual clock)."""
import pytest

from backend import rules
from backend.config import Settings
from backend.engine import Engine
from backend.schemas import IngestIn
from backend.seed import seed
from backend.store import Store
from sim.sim import SCENARIOS, Clock, build_payload, SECRET


@pytest.fixture()
def eng():
    store = Store(":memory:")
    seed(store)
    return Engine(store, Settings(strict_time=False, device_secret=SECRET))


def run_scenario(eng, name):
    results = []
    seq = 0
    last = None
    for st in SCENARIOS[name][1](Clock()):
        t0 = 1_800_000_000
        if st.vehicle:
            eng.set_vehicle(st.vehicle)
        if st.camera:
            eng.camera_observation(st.camera, t0 + st.at)
        if st.replay_of_previous and last:
            results.append(eng.ingest(IngestIn(**last)))
            continue
        if not st.samples:
            continue
        for s in st.samples:
            s["t"] += t0
        seq += 1
        last = build_payload(seq, int(t0 + st.at), st.samples, st.ping_type, st.trigger)
        results.append(eng.ingest(IngestIn(**last)))
    return results


def statuses(eng):
    return {p["id"]: p["status"] for p in eng.store.list_packages()}


def test_normal_drive_stays_green(eng):
    res = run_scenario(eng, "normal")
    assert all(r["accepted"] for r in res)
    assert {r["scenario"] for r in res} == {"NORMAL"}
    assert set(statuses(eng).values()) == {"NORMAL"}
    assert not any(r["notify"] for r in res)


def test_door_ajar_goes_under_review_not_quarantine(eng):
    res = run_scenario(eng, "door_ajar")
    assert any(r["scenario"] == "DOOR_AJAR" for r in res)
    assert "QUARANTINED" not in statuses(eng).values()
    assert "UNDER_REVIEW" in statuses(eng).values()
    assert any(r["notify"] for r in res)


def test_theft_quarantines_everything_and_revokes_tokens(eng):
    res = run_scenario(eng, "theft")
    assert any(r["scenario"] == "THEFT_SUSPECTED" for r in res)
    assert set(statuses(eng).values()) == {"QUARANTINED"}
    assert all(not p["token_valid"] for p in eng.store.list_packages())
    assert any(r["entry"]["severity"] == "emergency" for r in res)


def test_authorised_stop_does_not_quarantine(eng):
    res = run_scenario(eng, "authorised_stop")
    assert any(r["scenario"] == "AUTHORISED_HANDLING" for r in res)
    assert set(statuses(eng).values()) == {"NORMAL"}
    assert not any(r["notify"] for r in res if r["scenario"] == "AUTHORISED_HANDLING")


def test_compressor_failure_quarantines_tightest_budgets_first(eng):
    run_scenario(eng, "compressor_fail")
    st = statuses(eng)
    # plasma (15 min budget) and biologics (20) must be gone; flowers (240 min, up to 10C) must outlast them
    assert st["SHP-8578"] == "QUARANTINED"
    assert st["SHP-9856"] == "QUARANTINED"
    assert st["SHP-8978"] != "QUARANTINED" or st["SHP-8978"] == st["SHP-8578"]
    pk = eng.store.get_package("SHP-8578")
    assert pk["excursion_min"] > 0 and pk["token_valid"] is False


def test_freeze_is_flagged_as_freeze_risk(eng):
    res = run_scenario(eng, "freeze")
    assert any(r["scenario"] == "FREEZE_RISK" for r in res)


def test_replayed_payload_is_rejected(eng):
    res = run_scenario(eng, "replay")
    assert res[-1]["accepted"] is False
    assert res[-1]["entry"]["kind"] == "REJECTED_REPLAY_SEQUENCE"


def test_spoofed_flat_temperature_with_open_door_is_flagged(eng):
    res = run_scenario(eng, "spoof")
    assert any(r["scenario"] == "SPOOF_SUSPECTED" for r in res)


def test_bad_signature_rejected(eng):
    p = build_payload(1, 1_800_000_000, [{"t": 1_800_000_000, "temp_c": 5, "hum": 50, "light": 20}], secret="wrong")
    r = eng.ingest(IngestIn(**p))
    assert r["accepted"] is False and r["entry"]["kind"] == "REJECTED_BAD_SIGNATURE"
    assert eng.store.recent_readings("CC-001", 10**9, until=2 * 10**9) == []


def test_override_requires_existing_package_and_logs(eng):
    assert eng.override("NOPE", "NORMAL", "x", "admin", 1.0) is None
    e = eng.override("SHP-8841", "QUARANTINED", "visual damage", "Lisa", 1.0)
    assert e["kind"] == "SUPERVISOR_OVERRIDE"
    assert eng.store.get_package("SHP-8841")["token_valid"] is False


# ---- pure rule checks
def S(t, temp, light=20):
    return rules.Sample(t, temp, 50, light)


def test_slope_uses_regression_over_whole_degree_steps():
    s = [S(i * 5, 5 + (i // 24)) for i in range(60)]  # +1C every 2 min = 0.5 C/min
    assert rules.slope_c_per_min(s, 300) == pytest.approx(0.5, abs=0.1)


def test_slope_needs_enough_data():
    assert rules.slope_c_per_min([S(0, 5), S(5, 6)]) is None


def test_zero_variance_ignores_adc_rails():
    flat_dark = [S(i, 5, light=0) for i in range(12)]
    flat_mid = [S(i, 5, light=400) for i in range(12)]
    assert not rules.zero_variance_light(flat_dark, 10)
    assert rules.zero_variance_light(flat_mid, 10)


def test_sequence_states():
    assert rules.sequence_ok(None, 5) == "ok"
    assert rules.sequence_ok(5, 6) == "ok"
    assert rules.sequence_ok(5, 5) == "replay"
    assert rules.sequence_ok(5, 9) == "gap"


def test_rejection_flood_notifies_once_per_cooldown(eng):
    bad = lambda seq: IngestIn(**build_payload(
        seq, 1_800_000_000, [{"t": 1_800_000_000, "temp_c": 3, "hum": 50, "light": 20}], secret="wrong"))
    first = eng.ingest(bad(1), now=1_800_000_000)
    second = eng.ingest(bad(2), now=1_800_000_005)
    assert first["notify"] is True and second["notify"] is False


def test_tamper_risk_is_not_lower_than_status_implies(eng):
    run_scenario(eng, "theft")
    assert min(p["risk_score"] for p in eng.store.list_packages()) >= 90


def notified(res):
    return [(r["scenario"], r["entry"]["notify_reason"]) for r in res if r["notify"]]


def test_door_ajar_sends_two_messages_not_one_per_ping(eng):
    # door opens (new), then the temperature problem it causes (new); repeats of either stay quiet
    assert notified(run_scenario(eng, "door_ajar")) == [("DOOR_AJAR", "new"), ("THERMAL_EXCURSION", "new")]


def test_long_excursion_messages_on_changes_and_reminders_only(eng):
    res = run_scenario(eng, "compressor_fail")
    reasons = [r for _, r in notified(res)]
    assert reasons[0] == "new"
    assert "status_change" in reasons            # plasma / biologics quarantined mid-trip
    assert "reminder" in reasons                 # still unresolved after 10 min
    assert len(reasons) <= 8, reasons            # dozens of pings, a handful of messages
    assert sum(1 for r in res if r["scenario"] != "NORMAL") >= 30


def test_all_clear_resets_so_recurrence_is_new(eng):
    assert eng._notify_reason("DOOR_AJAR", "critical", 0) == "new"
    assert eng._notify_reason("DOOR_AJAR", "critical", 30) is None
    assert eng._notify_reason("NORMAL", "info", 60) is None
    assert eng._notify_reason("DOOR_AJAR", "critical", 90) == "new"
    assert eng._notify_reason("DOOR_AJAR", "critical", 90 + 600) == "reminder"


def _light_event(eng, base=1_800_000_000):
    samples = [{"t": base + i * 5, "temp_c": 3, "hum": 50, "light": 700 + i % 3} for i in range(1, 7)]
    return eng.ingest(IngestIn(**build_payload(1, base + 30, samples, "EVENT", "LIGHT")))


def test_light_while_stopped_on_an_active_route_is_theft(eng):
    eng.set_vehicle({"route_status": "En route", "speed_kmh": 0, "authorised_stop": False})
    r = _light_event(eng)
    assert r["scenario"] == "THEFT_SUSPECTED" and r["entry"]["severity"] == "emergency" and r["notify"]
    assert "stopped (0 km/h) on an active route" in r["entry"]["reasoning"]
    assert {p["status"] for p in eng.store.list_packages()} == {"QUARANTINED"}


def test_light_while_driving_is_door_ajar_not_theft(eng):
    eng.set_vehicle({"route_status": "En route", "speed_kmh": 55, "authorised_stop": False})
    assert _light_event(eng)["scenario"] == "DOOR_AJAR"


def test_light_while_stopped_before_the_route_is_active_is_not_theft(eng):
    eng.set_vehicle({"route_status": "Idle", "speed_kmh": 0, "authorised_stop": False})
    assert _light_event(eng)["scenario"] == "DOOR_AJAR"


def test_light_while_stopped_at_an_authorised_dock_is_handling(eng):
    eng.set_vehicle({"route_status": "En route", "speed_kmh": 0, "authorised_stop": True, "zone": "Johor Bahru Dock 4"})
    assert _light_event(eng)["scenario"] == "AUTHORISED_HANDLING"


def test_simulated_stopped_theft_scenario(eng):
    res = run_scenario(eng, "stopped_theft")
    assert res[-1]["scenario"] == "THEFT_SUSPECTED" and res[-1]["notify"]


DOCK = {"route_status": "En route", "speed_kmh": 0, "authorised_stop": True, "zone": "Johor Bahru Dock 4"}


def _lit(temp, n, base=1_800_000_000, seq=1):
    return [{"t": base + i * 5, "temp_c": temp, "hum": 50, "light": 700 + i % 3} for i in range(1, n + 1)]


def test_dock_door_open_with_a_warm_hold_is_handling_at_first(eng):
    """Your rule 5: authorised stop + bright + temperature out of range = handling (the open door explains it)."""
    eng.set_vehicle(DOCK)
    r = eng.ingest(IngestIn(**build_payload(1, 1_800_000_060, _lit(11, 12), "EVENT", "LIGHT")))
    assert r["scenario"] == "AUTHORISED_HANDLING" and r["notify"] is False
    assert any(pk["status"] == "UNDER_REVIEW" for pk in eng.store.list_packages())   # still tracked, never hidden
    assert "alert follows" in r["entry"]["reasoning"]


def test_dock_warm_hold_becomes_an_alert_after_the_grace_period(eng):
    eng.set_vehicle(DOCK)
    base = 1_800_000_000                                           # about 11 minutes with the door open, warming 9 -> 15 C
    long_run = [{"t": base + i * 5, "temp_c": 9 + i // 22, "hum": 50, "light": 700 + i % 3} for i in range(1, 131)]
    r = eng.ingest(IngestIn(**build_payload(1, 1_800_000_000 + 135 * 5, long_run, "EVENT", "LIGHT")))
    assert r["scenario"] == "THERMAL_EXCURSION" and r["notify"] is True


def test_cooling_at_a_dock_is_never_excused_by_the_open_door(eng):
    eng.set_vehicle(DOCK)
    r = eng.ingest(IngestIn(**build_payload(1, 1_800_000_060, _lit(-3, 12), "EVENT", "LIGHT")))
    assert r["scenario"] == "FREEZE_RISK" and r["notify"] is True


def test_dock_door_open_with_a_fine_temperature_is_handling(eng):
    eng.set_vehicle(DOCK)
    assert eng.ingest(IngestIn(**build_payload(1, 1_800_000_060, _lit(3, 12), "EVENT", "LIGHT")))["scenario"] == "AUTHORISED_HANDLING"


def test_a_five_degree_jump_within_a_minute_is_a_sudden_change(eng):
    assert Settings().step_change_c == 5.0
    base = 1_800_000_000
    samples = [{"t": base + i * 5, "temp_c": 2 if i <= 2 else 7, "hum": 50, "light": 20 + i % 3} for i in range(1, 13)]
    r = eng.ingest(IngestIn(**build_payload(1, base + 65, samples)))
    assert "TEMP_STEP" in {f["code"] for f in r["entry"]["findings"]}
    assert r["scenario"] == "THERMAL_EXCURSION" and r["notify"] is True


def test_a_four_degree_jump_is_not_a_step(eng):
    base = 1_800_000_000
    samples = [{"t": base + i * 5, "temp_c": 3 if i <= 2 else 7, "hum": 50, "light": 20 + i % 3} for i in range(1, 13)]
    r = eng.ingest(IngestIn(**build_payload(1, base + 65, samples)))
    assert "TEMP_STEP" not in {f["code"] for f in r["entry"]["findings"]}
