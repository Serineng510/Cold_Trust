"""Maintenance review: calculations, endpoints, AI call limits and fallbacks (no network)."""
import importlib
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from backend import llm, maintenance
from backend.rules import Sample

BASE = 1_800_000_000
DARK, BRIGHT = 20, 700   # raw light values (about 2 % and 68 %)


def S(t, temp, light=DARK):
    return Sample(t, temp, 50, light, None)


def timeline(days, recovery_minutes):
    """One reading a minute. Three door openings a day; `recovery_minutes(day)` sets how long the hold takes to come back."""
    out = []
    for m in range(days * 1440):
        t, hour, day = BASE + m * 60, (m % 1440) / 60, m // 1440
        opened = next((h for h in (8, 12, 16) if h <= hour < h + 2 / 60), None)       # door open for 2 minutes
        since_close = (m % 1440) - (opened * 60 + 2 if opened else 0)
        temp, light = 3.0, DARK
        if opened is not None:
            temp, light = 6.0, BRIGHT
        else:
            for h in (8, 12, 16):
                el = (m % 1440) - (h * 60 + 2)
                rec = recovery_minutes(day)
                if 0 <= el < rec:
                    temp = 3.0 + 3.0 * (1 - el / rec)
        out.append(S(t, round(temp, 2), light))
    return out


def test_recovery_minutes_after_one_door_opening():
    temps = [3, 3, 3, 3, 3, 3, 4, 6, 5.5, 4.5, 3.4, 3.0]
    lights = [DARK] * 6 + [BRIGHT, BRIGHT] + [DARK] * 4
    samples = [S(i * 60, temps[i], lights[i]) for i in range(12)]
    rec = maintenance.recoveries(samples, 30)
    assert rec == [{"at": 360, "minutes": 2.0}]                      # closed at 480 s, back at 600 s


def test_a_door_still_open_at_the_end_is_not_measured():
    samples = [S(i * 60, 3 if i < 5 else 6, DARK if i < 5 else BRIGHT) for i in range(8)]
    assert maintenance.recoveries(samples, 30) == []


def test_trend_words():
    assert maintenance._trend([5, 5, 5.2, 4.9]) == "steady"
    assert maintenance._trend([5, 6, 8, 10]) == "slower"
    assert maintenance._trend([10, 8, 5, 4]) == "faster"
    assert maintenance._trend([5, 9]) == "unknown"                    # fewer than 3 recoveries cannot show a trend


def test_week_report_finds_slower_recovery_and_groups_by_day():
    samples = timeline(7, lambda d: 4 + d * 3)                       # 4 min on day 1 ... 22 min on day 7
    rep = maintenance.report(samples, hours=168, hold_limits=[2, 8], bright_pct=30, tz_offset="+08:00")
    assert rep["enough_data"] and rep["recovery_enough"]
    assert 7 <= rep["days_covered"] <= 8 and len(rep["per_day"]) == rep["days_covered"]
    assert rep["overall"]["door_openings"] == 21 and rep["overall"]["recovery_trend"] == "slower"
    titles = [i["title"] for i in rep["indicators"]]
    assert any("getting slower" in t for t in titles)
    summary = maintenance.rule_summary(rep)
    assert summary["cooling_unit"] == "possible weakening" and summary["urgency"] == "service soon"


def test_steady_week_has_no_warning():
    rep = maintenance.report(timeline(7, lambda d: 6), hours=168, hold_limits=[2, 8], bright_pct=30)
    assert rep["overall"]["recovery_trend"] == "steady" and rep["overall"]["time_in_range_pct"] == 100.0
    summary = maintenance.rule_summary(rep)
    assert summary["cooling_unit"] == "no sign of weakening" and summary["urgency"] == "none"


def test_too_little_data_says_so():
    rep = maintenance.report([S(BASE + i * 60, 3) for i in range(10)], hours=6, hold_limits=[2, 8], bright_pct=30)
    assert rep["enough_data"] is False
    assert maintenance.rule_summary(rep)["cooling_unit"] == "not enough data"
    empty = maintenance.report([], hours=6, hold_limits=[2, 8], bright_pct=30)
    assert empty["overall"] is None and maintenance.rule_summary(empty)["cooling_unit"] == "not enough data"


def test_few_door_openings_cannot_judge_the_unit():
    rep = maintenance.report(timeline(1, lambda d: 6), hours=24, hold_limits=[2, 8], bright_pct=30)   # 3 openings, 1 day
    rep["overall"]["recovery_trend"] = "unknown"
    rep["recovery_enough"] = False
    assert maintenance.rule_summary(rep)["cooling_unit"] == "not enough data"


# ------------------------------------------------------------------ endpoints and AI behaviour

GOOD = {"assessment": "Recovery after door openings has slowed over the week; this is an early-warning indicator.",
        "cooling_unit": "possible weakening", "urgency": "service soon",
        "evidence": ["Recovery trend: slower"], "recommended_actions": ["Service the unit before the next long haul"]}


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(self.payload)}]}}]}


class Net:
    def __init__(self, payload=GOOD):
        self.calls, self.payload = 0, payload

    def __call__(self, url, headers, json, timeout):
        self.calls += 1
        return FakeResp(self.payload)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    env = {"COLDTRUST_DB": str(tmp_path / "t.db"), "COLDTRUST_EVIDENCE_DIR": str(tmp_path / "ev"), "COLDTRUST_STRICT_TIME": "0",
           "TELEGRAM_BOT_TOKEN": "", "COLDTRUST_LLM": "off", "COLDTRUST_GEMINI_USAGE_FILE": str(tmp_path / "usage.json")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import backend.agent as a
    import backend.config as c
    importlib.reload(c)
    importlib.reload(a)
    a.store.add_readings("CC-001", timeline(7, lambda d: 4 + d * 3), "ROUTINE")
    a._maint_cache.clear()
    return a


def with_gemini(app, monkeypatch, tmp_path, **cfg):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("COLDTRUST_LLM", "auto")
    monkeypatch.setenv("COLDTRUST_LLM_PROVIDER", "gemini")
    for k, v in cfg.items():
        monkeypatch.setenv(k, v)
    import backend.config as c
    importlib.reload(c)
    from backend.config import Settings
    app.explainer = llm.Explainer(Settings(gemini_usage_file=str(tmp_path / "usage.json"), **{k: int(v) for k, v in
                                                                                                  {"gemini_rpm": 100, "gemini_rpd": 1000}.items()}))
    assert app.explainer.enabled


def test_report_endpoint_per_range(app):
    client = TestClient(app.app)
    week = client.get("/api/maintenance?hours=168").json()
    day = client.get("/api/maintenance?hours=24").json()
    six = client.get("/api/maintenance?hours=6").json()
    assert week["days_covered"] >= 7 and day["days_covered"] <= 2 and six["samples"] < day["samples"] < week["samples"]
    assert client.get("/api/maintenance?hours=99999").json()["hours"] == 168          # capped at 7 days


def test_summary_without_an_api_key_is_rule_based(app):
    r = TestClient(app.app).post("/api/maintenance/summary", json={"hours": 168}).json()
    assert r["source"] == "rules" and r["agent"] is None and "off" in r["note"]
    assert r["summary"]["cooling_unit"] == "possible weakening" and r["usage"] is None


def test_summary_uses_one_ai_call_and_caches_it(app, monkeypatch, tmp_path):
    net = Net()
    monkeypatch.setattr(llm.httpx, "post", net)
    with_gemini(app, monkeypatch, tmp_path)
    client = TestClient(app.app)
    first = client.post("/api/maintenance/summary", json={"hours": 168}).json()
    assert first["source"] == "ai" and first["agent"] == "Gemini · Thermal agent" and first["cached"] is False
    assert first["summary"]["urgency"] == "service soon" and first["usage"]["used"] == 1
    second = client.post("/api/maintenance/summary", json={"hours": 168}).json()
    assert second["cached"] is True and second["source"] == "ai" and net.calls == 1   # pressing twice costs nothing


def test_the_ai_sees_only_the_summary_never_raw_readings(app, monkeypatch, tmp_path):
    seen = {}

    def spy(url, headers, json, timeout):
        seen["body"] = json
        return FakeResp(GOOD)

    monkeypatch.setattr(llm.httpx, "post", spy)
    with_gemini(app, monkeypatch, tmp_path)
    TestClient(app.app).post("/api/maintenance/summary", json={"hours": 168})
    sent = seen["body"]["contents"][0]["parts"][0]["text"]
    assert len(sent) < 8000 and "per_day" in sent and "temp_c" not in sent       # a week of readings would be hundreds of KB
    assert "MAINTENANCE REVIEW" in seen["body"]["systemInstruction"]["parts"][0]["text"]


def test_a_bad_ai_answer_falls_back_to_rules_and_is_not_cached(app, monkeypatch, tmp_path):
    monkeypatch.setattr(llm.httpx, "post", Net({**GOOD, "cooling_unit": "definitely broken"}))
    with_gemini(app, monkeypatch, tmp_path)
    r = TestClient(app.app).post("/api/maintenance/summary", json={"hours": 168}).json()
    assert r["source"] == "rules" and "did not give a usable answer" in r["note"] and app._maint_cache == {}


def test_when_the_free_limit_is_reached_the_button_degrades_gracefully(app, monkeypatch, tmp_path):
    net = Net()
    monkeypatch.setattr(llm.httpx, "post", net)
    with_gemini(app, monkeypatch, tmp_path)
    app.explainer.cfg = type(app.explainer.cfg)(**{**app.explainer.cfg.__dict__, "gemini_rpd": 0})   # automatic caps
    app.explainer.model = "gemini-3.5-flash-lite"
    for _ in range(app.explainer._caps()[1]):                         # use up today's allowance
        app.explainer.guard.reserve(app.explainer.model, 1000, app.explainer._caps()[1])
    r = TestClient(app.app).post("/api/maintenance/summary", json={"hours": 168}).json()
    assert r["source"] == "rules" and "paused" in r["note"] and net.calls == 0 and r["summary"]["assessment"]


def test_no_ai_call_when_there_is_too_little_history(app, monkeypatch, tmp_path):
    net = Net()
    monkeypatch.setattr(llm.httpx, "post", net)
    with_gemini(app, monkeypatch, tmp_path)
    app.store._db.execute("DELETE FROM readings")
    r = TestClient(app.app).post("/api/maintenance/summary", json={"hours": 24}).json()
    assert r["source"] == "rules" and "Not enough history" in r["note"] and net.calls == 0
    assert r["summary"]["cooling_unit"] == "not enough data"


def test_hours_outside_the_allowed_range_are_rejected(app):
    assert TestClient(app.app).post("/api/maintenance/summary", json={"hours": 1000}).status_code == 422
