"""AI agents: provider choice, Gemini request/response handling and agent routing (no network)."""
import json
import os
import tempfile
import time

import pytest

from backend import llm
from backend.config import Settings


class FakeResp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def gemini_body(payload, finish="STOP"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}]}


GOOD = {"explanation": "It is warm.", "product_impact": "Fish at risk.", "recommended_actions": ["Check the unit"]}
FRAME = {"person_present": True, "cargo_door_open": True, "description": "A person at the door", "confidence": 0.8}


@pytest.fixture()
def env(monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


_USAGE_DIR = tempfile.mkdtemp()


def make(**kw):
    kw.setdefault("gemini_rpm", 100)
    kw.setdefault("gemini_rpd", 1000)
    kw.setdefault("gemini_usage_file", os.path.join(_USAGE_DIR, f"usage-{time.time_ns()}.json"))
    return llm.Explainer(Settings(**kw))


def test_off_without_any_key(env):
    ex = make(llm_mode="auto", llm_provider="auto")
    assert not ex.enabled and ex.explain({}, "DOOR_AJAR") is None


def test_gemini_chosen_when_key_present(env):
    env.setenv("GEMINI_API_KEY", "k")
    ex = make(llm_mode="auto", llm_provider="auto", gemini_model="gemini-test")
    assert ex.provider == "gemini" and ex.model == "gemini-test" and ex.provider_label == "Gemini"


def test_mode_off_beats_key(env):
    env.setenv("GEMINI_API_KEY", "k")
    assert not make(llm_mode="off").enabled


def test_forced_gemini_without_key_is_disabled(env):
    assert not make(llm_mode="on", llm_provider="gemini").enabled


def test_agent_routing():
    assert llm.agent_for("THERMAL_EXCURSION") == "thermal" and llm.agent_for("FREEZE_RISK") == "thermal"
    assert llm.agent_for("DOOR_AJAR") == "seal" and llm.agent_for("REJECTED_REPLAY") == "seal"
    assert llm.agent_for("THEFT_SUSPECTED") == "incident" and llm.agent_for("SUSPICIOUS_PERSON") == "incident"


def test_schema_conversion_for_gemini():
    out = llm._gemini_schema(llm.EXPLAIN_SCHEMA)
    assert out["type"] == "OBJECT" and out["properties"]["recommended_actions"]["items"]["type"] == "STRING"
    assert "additionalProperties" not in json.dumps(out)
    assert llm.EXPLAIN_SCHEMA["type"] == "object"  # original untouched


def test_explain_request_and_result(env):
    env.setenv("GEMINI_API_KEY", "secret-key")
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, body=json)
        return FakeResp(gemini_body(GOOD))

    env.setattr(llm.httpx, "post", fake_post)
    ex = make(llm_mode="auto", llm_provider="auto", gemini_model="gemini-test")
    res = ex.explain({"scenario": "x"}, "THERMAL_EXCURSION")
    assert res["explanation"] == "It is warm." and res["explained_by"] == "Gemini · Thermal agent"
    assert sent["url"].endswith("/models/gemini-test:generateContent")
    assert sent["headers"]["x-goog-api-key"] == "secret-key"
    assert "secret-key" not in sent["url"]
    assert "THERMAL agent" in sent["body"]["systemInstruction"]["parts"][0]["text"]
    assert sent["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_code_fence_and_errors_fall_back_to_none(env):
    env.setenv("GEMINI_API_KEY", "k")
    ex = make(llm_mode="auto")
    env.setattr(llm.httpx, "post", lambda *a, **k: FakeResp(gemini_body("```json\n" + json.dumps(GOOD) + "\n```")))
    assert ex.explain({}, "DOOR_AJAR")["agent"] == "seal"
    env.setattr(llm.httpx, "post", lambda *a, **k: FakeResp(gemini_body("not json at all")))
    assert ex.explain({}, "DOOR_AJAR") is None
    env.setattr(llm.httpx, "post", lambda *a, **k: FakeResp(gemini_body({"explanation": "partial"})))
    assert ex.explain({}, "DOOR_AJAR") is None
    env.setattr(llm.httpx, "post", lambda *a, **k: FakeResp(gemini_body(GOOD, finish="SAFETY")))
    assert ex.explain({}, "DOOR_AJAR") is None
    env.setattr(llm.httpx, "post", lambda *a, **k: FakeResp({}, status=429))
    assert ex.explain({}, "DOOR_AJAR") is None

    def boom(*a, **k):
        raise llm.httpx.ConnectError("offline")
    env.setattr(llm.httpx, "post", boom)
    assert ex.explain({}, "DOOR_AJAR") is None


def test_frames_are_not_sent_unless_vision_is_enabled(env):
    env.setenv("GEMINI_API_KEY", "k")
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return FakeResp(gemini_body(FRAME))

    env.setattr(llm.httpx, "post", fake_post)
    assert make(llm_mode="auto", vision_enabled=False).describe_frame("QUJD") is None
    assert calls == []
    res = make(llm_mode="auto", vision_enabled=True).describe_frame("QUJD")
    assert res["person_present"] is True and res["explained_by"] == "Gemini · Incident agent"
    assert calls[0]["contents"][0]["parts"][0]["inlineData"] == {"mimeType": "image/jpeg", "data": "QUJD"}


def test_backend_attaches_agent_label_to_audit(tmp_path, env):
    """Full path: a hot reading -> rules decide -> Thermal agent (fake Gemini) rewrites the explanation."""
    import importlib
    import time

    from fastapi.testclient import TestClient

    from sim.sim import SECRET, build_payload

    for k, v in {"COLDTRUST_DB": str(tmp_path / "t.db"), "COLDTRUST_EVIDENCE_DIR": str(tmp_path / "ev"),
                 "COLDTRUST_STRICT_TIME": "0", "COLDTRUST_SECRET": SECRET, "TELEGRAM_BOT_TOKEN": "",
                 "GEMINI_API_KEY": "k", "COLDTRUST_LLM": "auto", "COLDTRUST_LLM_PROVIDER": "gemini",
                 "COLDTRUST_GEMINI_USAGE_FILE": os.path.join(_USAGE_DIR, "e2e.json")}.items():
        env.setenv(k, v)
    env.setattr(llm.httpx, "post", lambda *a, **k: FakeResp(gemini_body(GOOD)))
    import backend.agent as a
    import backend.config as c
    importlib.reload(c)
    importlib.reload(a)
    with TestClient(a.app) as client:
        assert client.get("/api/state").json()["llm_provider"] == "Gemini"
        t0 = 1_800_000_000
        for i in range(1, 6):  # sustained warm readings: rules call a thermal excursion
            p = build_payload(i, t0 + i * 60, [{"t": t0 + i * 60, "temp_c": 9, "hum": 50, "light": 10 + i}])
            client.post("/api/telemetry", json=p)
        for _ in range(50):
            tagged = [e for e in client.get("/api/audit").json() if e.get("explained_by")]
            if tagged:
                break
            time.sleep(0.1)
        assert tagged and tagged[0]["explained_by"] == "Gemini · Thermal agent"
        assert tagged[0]["reasoning"] == "It is warm."


# ---------------------------------------------------------------- free-tier guard (never exceed the free limits)

def http_error(status, headers=None):
    req = llm.httpx.Request("POST", "https://generativelanguage.googleapis.com/x")
    resp = llm.httpx.Response(status, request=req, headers=headers or {})
    return llm.httpx.HTTPStatusError(f"HTTP {status}", request=req, response=resp)


class Counting:
    def __init__(self, body=None, err=None):
        self.calls, self.urls, self.body, self.err = 0, [], body or gemini_body(GOOD), err

    def __call__(self, url, headers, json, timeout):
        self.calls += 1
        self.urls.append(url)
        if self.err:
            raise self.err
        return FakeResp(self.body)


def real_caps(model, **kw):
    """An Explainer with automatic caps (no test overrides)."""
    return llm.Explainer(Settings(llm_mode="auto", gemini_model=model, gemini_rpm=0, gemini_rpd=0,
                                  gemini_usage_file=os.path.join(_USAGE_DIR, f"caps-{time.time_ns()}.json"), **kw))


def test_default_model_has_a_real_free_allowance(env):
    env.setenv("GEMINI_API_KEY", "k")
    ex = real_caps("gemini-3.5-flash-lite")
    assert ex._caps() == (9, 400)            # 60 % of 15/min and 80 % of 500/day
    assert real_caps("gemini-3.8-flash")._caps() == (3, 16)   # the full Flash model only allows 20 a day
    assert real_caps("some-new-model")._caps() == (3, 16)     # unknown = strictest assumption
    assert Settings().gemini_model == "gemini-3.5-flash-lite"


def test_daily_cap_stops_calls_before_google_does(env):
    env.setenv("GEMINI_API_KEY", "k")
    net = Counting()
    env.setattr(llm.httpx, "post", net)
    ex = make(llm_mode="auto", gemini_rpd=2)
    assert ex.explain({}, "DOOR_AJAR") and ex.explain({}, "DOOR_AJAR")
    assert ex.explain({}, "DOOR_AJAR") is None          # third: blocked by us, rule text is used
    assert net.calls == 2                                # and Google was never called for it
    assert ex.usage()["used"] == 2 and "daily cap" in ex.usage()["paused"]


def test_per_minute_cap(env):
    env.setenv("GEMINI_API_KEY", "k")
    net = Counting()
    env.setattr(llm.httpx, "post", net)
    ex = make(llm_mode="auto", gemini_rpm=3)
    results = [ex.explain({}, "DOOR_AJAR") for _ in range(6)]
    assert net.calls == 3 and sum(r is not None for r in results) == 3


def test_restart_does_not_reset_the_daily_count(env, tmp_path):
    env.setenv("GEMINI_API_KEY", "k")
    net = Counting()
    env.setattr(llm.httpx, "post", net)
    usage = str(tmp_path / "usage.json")
    first = make(llm_mode="auto", gemini_rpd=3, gemini_usage_file=usage)
    for _ in range(3):
        first.explain({}, "DOOR_AJAR")
    again = make(llm_mode="auto", gemini_rpd=3, gemini_usage_file=usage)   # a server restart / --reset
    assert again.explain({}, "DOOR_AJAR") is None and net.calls == 3


def test_daily_count_resets_on_a_new_pacific_day(env, tmp_path):
    env.setenv("GEMINI_API_KEY", "k")
    net = Counting()
    env.setattr(llm.httpx, "post", net)
    ex = make(llm_mode="auto", gemini_rpd=1, gemini_usage_file=str(tmp_path / "u.json"))
    clock = {"t": 1_800_000_000.0}
    ex.guard.clock = lambda: clock["t"]
    assert ex.explain({}, "DOOR_AJAR") and ex.explain({}, "DOOR_AJAR") is None
    clock["t"] += 25 * 3600
    assert ex.explain({}, "DOOR_AJAR") is not None


def test_429_pauses_ai_calls_instead_of_retrying(env):
    env.setenv("GEMINI_API_KEY", "k")
    net = Counting(err=http_error(429))
    env.setattr(llm.httpx, "post", net)
    ex = make(llm_mode="auto")
    assert ex.explain({}, "DOOR_AJAR") is None and net.calls == 1
    for _ in range(5):
        assert ex.explain({}, "DOOR_AJAR") is None
    assert net.calls == 1 and "cooling down" in ex.usage()["paused"]   # no hammering a limited key


def test_unknown_model_name_falls_back_once(env):
    env.setenv("GEMINI_API_KEY", "k")
    net = Counting(err=http_error(404))
    env.setattr(llm.httpx, "post", net)
    ex = make(llm_mode="auto", gemini_model="gemini-not-real", gemini_fallback_models="gemini-3.1-flash-lite")
    assert ex.explain({}, "DOOR_AJAR") is None and ex.model == "gemini-3.1-flash-lite"
    net.err = None
    assert ex.explain({}, "DOOR_AJAR") is not None and net.urls[-1].endswith("/models/gemini-3.1-flash-lite:generateContent")


def test_no_calls_at_all_when_ai_is_off(env):
    net = Counting()
    env.setattr(llm.httpx, "post", net)
    ex = make(llm_mode="off")
    assert ex.explain({}, "DOOR_AJAR") is None and ex.usage() is None and net.calls == 0
