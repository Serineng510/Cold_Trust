"""The measured benefits (docs/detection_results.md) must stay true: these tests fail if a later change loses one."""
import pytest

from backend.seed import VEHICLE
from tools import eval_detection as ev

ROWS = {r["scenario"]: r for r in (ev.play(n) for n in ev.SCENARIOS)}
SUMMARY = ev.summarise(list(ROWS.values()))


def test_normal_driving_raises_no_alert_in_either_system():
    assert ROWS["normal"]["baseline_alarms"] == 0 and ROWS["normal"]["cold_alerts"] == 0


def test_a_door_opened_at_an_authorised_dock_is_a_false_alarm_for_the_threshold_alarm_only():
    r = ROWS["authorised_stop"]
    assert r["baseline_alarms"] >= 1 and r["cold_alerts"] == 0 and r["held_at_first_alert"] == 0
    assert SUMMARY["false_alarms_baseline"] >= 1 and SUMMARY["false_alarms_coldtrust"] == 0


@pytest.mark.parametrize("name", sorted(ev.DATA_ATTACKS))
def test_each_data_attack_is_recognised_and_a_threshold_alarm_cannot_see_it(name):
    assert ROWS[name]["untrusted_caught_s"] is not None                       # ColdTrust says the data cannot be trusted
    # the plain alarm has no check for it: it never reports untrusted data, whatever else it rings for
    assert SUMMARY["attacks_caught_coldtrust"] == SUMMARY["attacks_total"] == 3


def test_a_replayed_message_is_rejected_and_not_counted_as_a_reading():
    assert ROWS["replay"]["cold_kind"] == "REJECTED_REPLAY_SEQUENCE" and ROWS["replay"]["baseline_first_s"] is None


def test_every_real_incident_leaves_the_cargo_held_when_the_first_alert_goes_out():
    assert SUMMARY["incidents"] == 5 and SUMMARY["held_coldtrust"] == 5
    for n in ("door_ajar", "theft", "compressor_fail", "freeze", "stopped_theft"):
        assert ROWS[n]["held_at_first_alert"] > 0 and ROWS[n]["cold_first_s"] is not None


def test_a_slow_failure_is_alerted_before_the_single_hold_limit_is_crossed():
    r = ROWS["compressor_fail"]
    assert r["cold_first_s"] < r["baseline_first_s"] and r["cold_kind"] == "THERMAL_EXCURSION"
    assert r["temp_at_first_alert"] <= VEHICLE["hold_limits_c"][1]            # still inside the hold's 2-8 C when ColdTrust spoke
    assert SUMMARY["earlier"]["compressor_fail"] >= 300                       # at least five minutes earlier on this scenario


@pytest.mark.parametrize("name", ["door_ajar", "theft", "freeze", "stopped_theft"])
def test_on_a_sudden_event_the_two_systems_are_equally_fast_and_never_slower(name):
    r = ROWS[name]
    assert r["cold_first_s"] <= r["baseline_first_s"]                          # the benefit there is what happens next, not speed


def test_the_report_is_written(tmp_path):
    text = ev.render(list(ROWS.values()), SUMMARY)
    assert "False alarms" in text and "Data attacks recognised" in text and "0 of 3" in text and "3 of 3" in text
