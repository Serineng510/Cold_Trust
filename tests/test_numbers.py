"""Number check (the AI may only quote figures that are in its input) and the AI follow-up on Telegram."""
import importlib
import json
import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

from backend import llm
from backend.config import Settings
from backend.llm import unsupported_numbers
from sim.sim import SECRET, build_payload

CTX = {"findings": [{"code": "TEMP_THRESHOLD", "detail": "Temperature 6.1 C, limit 2-4 C for 12 min"}],
       "readings": {"temp_c": 11.3, "slope_c_per_min": -0.84, "light_pct": 70.5},
       "products": [{"id": "SHP-2364", "weight_kg": 13010}, {"id": "SHP-7841"}, {"id": "SHP-5678"}],
       "per_day": [{"date": "2026-10-19", "time_in_range_pct": 100.0}]}


@pytest.mark.parametrize("text", [
    "The hold reached 6.1 C against the 2-4 C limit for 12 minutes.",
    "It is about 11 C now and falling 0.8 C per minute.",          # rounding of 11.3 and 0.84 is fine
    "Temperature is changing -0.84 C/min.",
    "Fresh salmon SHP-2364 is affected; SHP-9999 is not mentioned.",  # identifiers are not numbers
    "3 products are affected.",                                      # a count of a list in the input
    "Cargo weighs 13,010 kg.",
    "On 19 October the hold was in range 100% of the time.",
    "No numbers at all here.",
])
def test_supported_numbers_pass(text):
    assert unsupported_numbers([text], CTX) == []


@pytest.mark.parametrize("text, bad", [
    ("The hold is at 7.5 C.", ["7.5"]),                               # invented reading
    ("42% of the budget is used.", ["42"]),                           # a calculation the AI was not given
    ("It will reach the limit in 25 minutes.", ["25"]),               # a prediction
    ("Light at 70.5% and humidity 63%.", ["63"]),                     # one good, one invented
])
def test_unsupported_numbers_are_caught(text, bad):
    assert unsupported_numbers([text], CTX) == bad


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(self.payload)}]}}]}


GOOD = {"explanation": "The hold is too warm for the salmon.", "product_impact": "Salmon is at risk.",
        "recommended_actions": ["Check the cooling unit"]}
INVENTED = {**GOOD, "explanation": "The hold is at 987.6 C."}


def make(env, payload):
    env.setenv("GEMINI_API_KEY", "k")
    env.setattr(llm.httpx, "post", lambda *a, **k: FakeResp(payload))
    return llm.Explainer(Settings(llm_mode="auto", gemini_rpm=100, gemini_rpd=1000,
                                  gemini_usage_file=os.path.join(tempfile.mkdtemp(), "u.json")))


def test_explainer_rejects_an_invented_number(monkeypatch):
    ex = make(monkeypatch, INVENTED)
    res, bad = ex.explain_checked({"temp_c": 9}, "THERMAL_EXCURSION")
    assert res is None and bad == ["987.6"] and ex.usage()["ai_text_rejected"] == 1
    assert ex.explain({"temp_c": 9}, "THERMAL_EXCURSION") is None


def test_explainer_accepts_a_faithful_answer(monkeypatch):
    ex = make(monkeypatch, {**GOOD, "explanation": "The hold is at 9 C."})
    res, bad = ex.explain_checked({"temp_c": 9}, "THERMAL_EXCURSION")
    assert res and bad == [] and res["explained_by"] == "Gemini · Thermal agent"


def test_maintenance_review_is_checked_too(monkeypatch):
    rep = {"overall": {"avg_recovery_min": 11.3}, "enough_data": True}
    bad_review = {"assessment": "Recovery takes 19.9 min on average.", "cooling_unit": "possible weakening",
                  "urgency": "monitor", "evidence": [], "recommended_actions": []}
    res, bad = make(monkeypatch, bad_review).maintenance_checked(rep)
    assert res is None and bad == ["19.9"]


# ------------------------------------------------------------------ end to end: ONE Telegram message per alert

def backend(tmp_path, env, post, **extra):
    for k, v in {"COLDTRUST_DB": str(tmp_path / "t.db"), "COLDTRUST_EVIDENCE_DIR": str(tmp_path / "ev"),
                 "COLDTRUST_STRICT_TIME": "0", "COLDTRUST_SECRET": SECRET, "TELEGRAM_BOT_TOKEN": "test-token",
                 "TELEGRAM_CHAT_IDS": "1", "GEMINI_API_KEY": "k", "COLDTRUST_LLM": "auto",
                 "COLDTRUST_LLM_PROVIDER": "gemini", "COLDTRUST_GEMINI_USAGE_FILE": str(tmp_path / "u.json"), **extra}.items():
        env.setenv(k, v)
    env.setattr(llm.httpx, "post", post if callable(post) else (lambda *a, **k: FakeResp(post)))
    import backend.agent as a
    import backend.config as c
    importlib.reload(c)
    importlib.reload(a)
    sent = []
    env.setattr(a.notifier, "send", lambda text: sent.append(text) or 1)
    env.setattr(a.notifier, "send_photo", lambda jpeg, text: sent.append(text) or 1)
    return a, sent


def one_alert(client):
    """One reading batch that is warm for a minute: exactly one new THERMAL_EXCURSION alert."""
    t0 = 1_800_000_000
    samples = [{"t": t0 + i * 5, "temp_c": 9, "hum": 50, "light": 10 + i % 3} for i in range(1, 13)]
    client.post("/api/telemetry", json=build_payload(1, t0 + 65, samples))


def wait_for(fn, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture()
def env(monkeypatch):
    for k in ("GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "COLDTRUST_TELEGRAM_AI", "COLDTRUST_TELEGRAM_AI_WAIT_S"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_one_telegram_message_per_alert_and_it_is_the_ai_analysis(tmp_path, env):
    a, sent = backend(tmp_path, env, GOOD)
    with TestClient(a.app) as client:
        one_alert(client)
        assert wait_for(lambda: len(sent) >= 1)
        time.sleep(0.8)                                              # a second message would have arrived by now
        audit = client.get("/api/audit").json()
    assert len(sent) == 1
    msg = sent[0]
    assert "[CRITICAL] THERMAL_EXCURSION (Gemini · Thermal agent)" in msg and "The hold is too warm for the salmon." in msg
    assert "Next: Check the cooling unit" in msg and "Status: UNDER REVIEW" in msg and "SHP-" in msg   # facts from the rules
    assert "Hold temperature is outside" not in msg                  # the rule-based text is not sent as well
    logged = next(e for e in audit if e["kind"] == "THERMAL_EXCURSION")
    assert logged["explained_by"] == "Gemini · Thermal agent"        # and the dashboard log has the AI text


def test_an_invented_number_sends_the_rule_text_once_and_is_logged(tmp_path, env):
    a, sent = backend(tmp_path, env, INVENTED)
    with TestClient(a.app) as client:
        one_alert(client)
        assert wait_for(lambda: any(e.get("ai_rejected") for e in client.get("/api/audit").json()))
        time.sleep(0.5)
        audit = client.get("/api/audit").json()
    assert len(sent) == 1 and "THERMAL_EXCURSION" in sent[0] and "987.6" not in sent[0]
    assert "Hold temperature is outside" in sent[0]                  # the rule-based text
    note = next(e["ai_rejected"] for e in audit if e.get("ai_rejected"))
    assert "987.6" in note and "rule-based text is kept" in note


def test_a_slow_ai_never_delays_the_alert_and_never_adds_a_second_message(tmp_path, env):
    def slow(url, headers, json, timeout):
        time.sleep(1.5)
        return FakeResp(GOOD)

    a, sent = backend(tmp_path, env, slow, COLDTRUST_TELEGRAM_AI_WAIT_S="0.3")
    with TestClient(a.app) as client:
        start = time.time()
        one_alert(client)
        assert wait_for(lambda: len(sent) >= 1)
        assert time.time() - start < 1.2                             # sent without waiting for the slow AI
        assert wait_for(lambda: any(e.get("explained_by") for e in client.get("/api/audit").json()), 6)
        time.sleep(0.5)
    assert len(sent) == 1 and "Hold temperature is outside" in sent[0]   # the late AI text went to the log only


def test_with_the_ai_follow_up_off_the_rule_text_goes_out_at_once(tmp_path, env):
    a, sent = backend(tmp_path, env, GOOD, COLDTRUST_TELEGRAM_AI="0")
    with TestClient(a.app) as client:
        one_alert(client)
        assert wait_for(lambda: len(sent) >= 1)
        assert wait_for(lambda: any(e.get("explained_by") for e in client.get("/api/audit").json()))
        time.sleep(0.4)
    assert len(sent) == 1 and "Hold temperature is outside" in sent[0] and "Gemini" not in sent[0]


def test_without_an_api_key_the_rule_text_is_sent(tmp_path, env):
    a, sent = backend(tmp_path, env, GOOD, COLDTRUST_LLM="off")
    with TestClient(a.app) as client:
        one_alert(client)
        assert wait_for(lambda: len(sent) >= 1)
        time.sleep(0.3)
    assert len(sent) == 1 and "Hold temperature is outside" in sent[0]


# ------------------------------------------------------------------ wording for the incident report
RAMBLE = ("The possible cause is unauthorized access. The output is a single valid JSON object adhering to the schema. "
          "All constraints are strictly followed. Final review of all requirements confirms compliance.")


@pytest.mark.parametrize("text, ok", [
    ("A person was seen in the cargo hold at a stop where the door is not expected to open.", True),
    ("Possible unauthorised access. Not confirmed.", True),
    ("", False), (None, False),
    ("x" * 700, False),                                              # too long
    ("One. Two. Three. Four. Five.", False),                         # too many sentences
    (RAMBLE, False),                                                 # the model talking about its own instructions
    ("The input says allowed_actions include QUARANTINE.", False),
])
def test_report_wording_filter(text, ok):
    assert (llm.report_wording(text, 600, 4) is not None) is ok


def test_the_seal_agent_may_be_named_in_a_report():
    assert llm.report_wording("The data check by the Seal agent found the readings unreliable.", 600, 4)


def test_rambling_report_wording_is_dropped_but_the_good_field_is_kept(monkeypatch):
    ex = make(monkeypatch, {"incident_description": "A person was seen in the hold.", "suspected_cause": RAMBLE})
    res = ex.report_text({"temp_c": 9})
    assert res["incident_description"] == "A person was seen in the hold." and res["suspected_cause"] is None
    assert res["by"] == "Gemini · Incident agent"


def test_report_wording_with_an_invented_number_is_dropped(monkeypatch):
    ex = make(monkeypatch, {"incident_description": "The hold reached 41 C.", "suspected_cause": "Possible fault at 9 C."})
    res = ex.report_text({"temp_c": 9})
    assert res["incident_description"] is None and res["suspected_cause"] == "Possible fault at 9 C."
    assert ex.report_text({"temp_c": 9}) is not None
    assert make(monkeypatch, {"incident_description": "x " * 500, "suspected_cause": RAMBLE}).report_text({}) is None


def test_the_alert_call_no_longer_asks_for_report_wording():
    # asking for it in the same call made the Incident agent's answer run away (docs/agent_eval.md)
    assert "incident_description" not in llm.EXPLAIN_SCHEMA["properties"]
    assert "incident_description" not in llm.INCIDENT_SCHEMA["properties"]
    assert "incident_description" not in llm.AGENTS["incident"]["system"]
    assert set(llm.REPORT_SCHEMA["required"]) == {"incident_description", "suspected_cause"}


def test_control_characters_in_an_ai_answer_are_removed(monkeypatch):
    # seen live: the model wrote a NUL where it meant a degree sign
    ex = make(monkeypatch, {**GOOD, "explanation": "The hold dropped to 0.0\u0000C.\x0b", "recommended_actions": ["Check\x07 it"]})
    res, bad = ex.explain_checked({"temp_c": 0}, "THERMAL_EXCURSION")
    assert bad == [] and res["explanation"] == "The hold dropped to 0.0C." and res["recommended_actions"] == ["Check it"]
    assert llm.clean_text({"a": ["x\x00y", {"b": "ok\n\ttext"}], "n": 3}) == {"a": ["xy", {"b": "ok\n\ttext"}], "n": 3}
