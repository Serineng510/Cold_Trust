"""HTTP-level checks through FastAPI's TestClient (no LLM, no Telegram)."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient

from sim.sim import SECRET, build_payload


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COLDTRUST_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("COLDTRUST_EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("COLDTRUST_LLM", "off")
    monkeypatch.setenv("COLDTRUST_STRICT_TIME", "0")
    monkeypatch.setenv("COLDTRUST_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")  # never message real phones from tests, even if .env has a token
    import backend.config as c
    importlib.reload(c)  # settings are read at import time, so refresh them before the app module loads
    import backend.agent as a
    importlib.reload(a)
    return TestClient(a.app)


def sample(t, temp=3, light=20):
    return {"t": t, "temp_c": temp, "hum": 50, "light": light + (t // 5) % 3}  # real LDRs jitter


def test_health_and_state(client):
    assert client.get("/api/health").json()["ok"] is True
    st = client.get("/api/state").json()
    assert st["totals"]["items"] == 10
    assert st["llm"] is False


def test_ingest_then_audit_and_replay_block(client):
    p = build_payload(1, 1_800_000_060, [sample(1_800_000_000 + i * 5) for i in range(1, 13)])
    r = client.post("/api/telemetry", json=p).json()
    assert r["accepted"] is True and r["entry"]["verdict"] == "NORMAL"
    again = client.post("/api/telemetry", json=p).json()
    assert again["accepted"] is False and again["reason"] == "REPLAY_SEQUENCE"
    kinds = [e["kind"] for e in client.get("/api/audit").json()]
    assert kinds == ["ROUTINE", "REJECTED_REPLAY_SEQUENCE"]


def test_quarantine_revokes_custody_token_and_override_reissues(client):
    token = client.get("/api/state").json()["packages"][0]["custody_token"]
    assert client.get(f"/api/custody/{token}").json()["valid"] is True
    client.post("/api/vehicle", json={"speed_mph": 60, "authorised_stop": False})
    base = 1_800_000_000
    client.post("/api/telemetry", json=build_payload(1, base + 60, [sample(base + i * 5, 3, 20) for i in range(1, 13)]))
    client.post("/api/camera/observation", json={"person_present": True, "description": "person at door"})
    ev = client.post("/api/telemetry", json=build_payload(
        2, base + 90, [sample(base + 65 + i * 5, 3, 700) for i in range(6)], "EVENT", "LIGHT")).json()
    assert ev["entry"]["scenario"] == "THEFT_SUSPECTED"
    assert client.get(f"/api/custody/{token}").json()["valid"] is False

    pid = client.get("/api/state").json()["packages"][0]["id"]
    o = client.post(f"/api/package/{pid}/override",
                    json={"status": "NORMAL", "reason": "inspected, seals intact", "supervisor": "Lisa"}).json()
    assert o["package"]["status"] == "NORMAL" and o["package"]["token_valid"] is True
    assert o["package"]["custody_token"] != token
    assert client.get(f"/api/custody/{token}").json()["valid"] is False  # old token stays dead


def test_override_unknown_package_404(client):
    r = client.post("/api/package/NOPE/override", json={"status": "NORMAL", "reason": "because"})
    assert r.status_code == 404


def test_evidence_upload_validates_and_hashes(client):
    import base64
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 200
    r = client.post("/api/evidence", json={"jpeg_base64": base64.b64encode(jpeg).decode(), "trigger": "LIGHT"})
    assert r.status_code == 200 and len(r.json()["sha256"]) == 64
    assert client.get(f"/api/evidence/{r.json()['stored']}").status_code == 200
    bad = client.post("/api/evidence", json={"jpeg_base64": base64.b64encode(b"x" * 200).decode()})
    assert bad.status_code == 400
    assert client.get("/api/evidence/..%2f..%2fetc").status_code in (404, 422)


def test_ack_approve_reassign_and_vehicle_actions(client):
    base = 1_800_000_000
    r = client.post("/api/telemetry", json=build_payload(1, base + 60, [sample(base + i * 5) for i in range(1, 13)])).json()
    aid = r["entry"]["id"]

    ack = client.post(f"/api/audit/{aid}/ack", json={"by": "Lisa"}).json()
    assert ack["entry"]["kind"] == "ALERT_ACKNOWLEDGED"
    assert next(e for e in client.get("/api/audit").json() if e["id"] == aid)["acked_by"] == "Lisa"
    assert client.post("/api/audit/9999/ack", json={"by": "Lisa"}).status_code == 404

    assert client.post(f"/api/audit/{aid}/approve-report", json={"by": "Lisa"}).status_code in (404, 405)

    moved = client.post("/api/package/SHP-8841/reassign",
                        json={"truck": "TX-2210-HX", "reason": "cooling failed on this truck"}).json()
    assert moved["package"]["reassign_to"] == "TX-2210-HX"
    assert client.post("/api/package/SHP-8841/reassign", json={"truck": "TX-2210-HX", "reason": "x"}).status_code == 422
    assert client.post("/api/package/NOPE/reassign", json={"truck": "TX-2210-HX", "reason": "because"}).status_code == 404

    v = client.post("/api/vehicle/action", json={"action": "offline", "by": "Lisa"}).json()
    assert v["vehicle"]["route_status"] == "Offline"
    assert client.post("/api/vehicle/action", json={"action": "explode"}).status_code == 422

    st = client.get("/api/state").json()
    assert len(st["route"]) >= 2 and st["vehicle"]["route_status"] == "Offline"


def test_activate_route_leaves_the_depot_and_evidence_records_location(client):
    client.post("/api/vehicle", json={"lat": 33.05, "lon": -96.10, "speed_mph": 0})
    v = client.post("/api/vehicle/action", json={"action": "activate", "by": "Lisa"}).json()["vehicle"]
    assert v["route_status"] == "En route" and v["authorised_stop"] is False and v["zone"] is None
    import base64
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 200
    r = client.post("/api/evidence", json={"jpeg_base64": base64.b64encode(jpeg).decode(), "trigger": "THEFT_SUSPECTED"})
    p = r.json()["entry"]["params"]
    assert p["detected"] == "THEFT_SUSPECTED" and p["lat"] == 33.05 and p["lon"] == -96.10


def test_mock_controls_are_off_unless_enabled(client):
    assert client.get("/api/demo").json()["enabled"] is False
    r = client.post("/api/demo", json={"mock": True, "temp_c": 9})
    assert r.status_code == 403 and "--demo" in r.json()["detail"]


def test_mock_checkbox_slider_and_labelling(monkeypatch, tmp_path):
    monkeypatch.setenv("COLDTRUST_DEMO", "1")
    monkeypatch.setenv("COLDTRUST_DB", str(tmp_path / "d.db"))
    monkeypatch.setenv("COLDTRUST_LLM", "off")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("COLDTRUST_STRICT_TIME", "0")
    monkeypatch.setenv("COLDTRUST_SECRET", SECRET)
    import backend.config as c
    importlib.reload(c)
    import backend.agent as a
    client = TestClient(importlib.reload(a).app)

    assert client.get("/api/demo").json() == {
        "enabled": True, "mock": False, "temp_c": 4.0, "light_mock": False, "light_pct": 0.0,
        "simulated_temp": False, "simulated_light": False, "simulated": False}
    on = client.post("/api/demo", json={"mock": True, "temp_c": 9, "by": "Lisa"}).json()
    assert on["mock"] is True and on["temp_c"] == 9
    n = len(client.get("/api/audit").json())
    drag = client.post("/api/demo", json={"mock": True, "temp_c": 10, "log": False}).json()
    assert drag["temp_c"] == 10 and len(client.get("/api/audit").json()) == n     # slider drags are not logged
    client.post("/api/demo", json={"mock": True, "temp_c": 11})                    # released: logged
    assert "MOCK temperature to 11" in client.get("/api/audit").json()[-1]["reasoning"]
    assert client.post("/api/demo", json={"mock": True, "temp_c": 999}).status_code == 422

    base = 1_800_000_000
    p = build_payload(1, base + 60, [sample(base + i * 5, 11) for i in range(1, 13)])
    p["demo"] = True                                  # unsigned label, as the firmware sends it when any sample was mocked
    r = client.post("/api/telemetry", json=p).json()
    assert r["accepted"] and r["entry"]["params"]["simulated"] is True
    assert client.get("/api/state").json()["demo"]["simulated"] is True
    ev = [e for e in client.get("/api/epcis").json()["epcisBody"]["eventList"] if e["bizStep"] == "transporting"]
    assert ev and ev[0]["ct:simulated"] is True
    assert client.get("/api/epcis/verify").json()["all_valid"] is True

    off = client.post("/api/demo", json={"mock": False}).json()
    assert off["mock"] is False and off["temp_c"] == 11          # unticked: the node reads its sensor; the slider value is remembered
    assert client.get("/api/demo").json()["simulated"] is True     # unticked, but the node has not sent a real message yet
    real = build_payload(2, base + 120, [sample(base + 60 + i * 5, 4) for i in range(1, 13)])
    assert client.post("/api/telemetry", json=real).json()["entry"]["params"]["simulated"] is False
    assert client.get("/api/demo").json()["simulated"] is False    # the first real message clears the mock label at once


def test_mock_light_is_independent_of_mock_temperature(monkeypatch, tmp_path):
    monkeypatch.setenv("COLDTRUST_DEMO", "1")
    monkeypatch.setenv("COLDTRUST_DB", str(tmp_path / "l.db"))
    monkeypatch.setenv("COLDTRUST_LLM", "off")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("COLDTRUST_STRICT_TIME", "0")
    monkeypatch.setenv("COLDTRUST_SECRET", SECRET)
    import backend.config as c
    importlib.reload(c)
    import backend.agent as a
    client = TestClient(importlib.reload(a).app)

    assert client.post("/api/demo", json={"log": False}).status_code == 422          # must change something
    assert client.post("/api/demo", json={"light_mock": True, "light_pct": 101}).status_code == 422
    s = client.post("/api/demo", json={"light_mock": True, "light_pct": 70, "by": "Lisa"}).json()
    assert s["light_mock"] is True and s["light_pct"] == 70 and s["mock"] is False and s["temp_c"] == 4.0
    assert "MOCK light on, set to 70 %" in client.get("/api/audit").json()[-1]["reasoning"]
    both = client.post("/api/demo", json={"mock": True, "temp_c": 9, "light_mock": False}).json()
    assert both["mock"] is True and both["light_mock"] is False and both["light_pct"] == 70

    base = 1_800_000_000
    p = build_payload(1, base + 60, [sample(base + i * 5, 4, 700) for i in range(1, 13)])
    p["demo"], p["demo_sensors"] = True, ["light"]
    r = client.post("/api/telemetry", json=p).json()
    assert r["entry"]["params"]["mocked"] == ["light"]
    d = client.get("/api/demo").json()
    assert d["simulated_light"] is True and d["simulated_temp"] is False and d["simulated"] is True

    real = build_payload(2, base + 120, [sample(base + 60 + i * 5, 4) for i in range(1, 13)])
    client.post("/api/telemetry", json=real)
    d = client.get("/api/demo").json()
    assert d["simulated"] is False and d["simulated_light"] is False


def test_telegram_alerts_say_when_they_come_from_mock_data(monkeypatch, tmp_path):
    import asyncio
    monkeypatch.setenv("COLDTRUST_DEMO", "1")
    monkeypatch.setenv("COLDTRUST_DB", str(tmp_path / "tg.db"))
    monkeypatch.setenv("COLDTRUST_LLM", "off")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("COLDTRUST_STRICT_TIME", "0")
    monkeypatch.setenv("COLDTRUST_SECRET", SECRET)
    import backend.config as c
    importlib.reload(c)
    import backend.agent as a
    a = importlib.reload(a)
    client = TestClient(a.app)
    sent = []
    monkeypatch.setattr(a.notifier, "send", lambda text: sent.append(text) or 1)
    entry = {"kind": "THERMAL_EXCURSION", "severity": "critical", "reasoning": "11C is outside limits",
             "actions": [], "notify_reason": "new"}

    asyncio.run(a._deliver(entry))                                    # real data: no tag
    assert sent[-1].startswith("[CRITICAL] THERMAL_EXCURSION")

    base = 1_800_000_000
    p = build_payload(1, base + 60, [sample(base + i * 5, 11, 700) for i in range(1, 13)])
    p["demo"], p["demo_sensors"] = True, ["temp", "light"]
    client.post("/api/telemetry", json=p)
    asyncio.run(a._deliver(entry))
    assert sent[-1].startswith("[MOCK DATA: temperature + light] [CRITICAL] THERMAL_EXCURSION")

    client.post("/api/telemetry", json=build_payload(2, base + 120, [sample(base + 60 + i * 5, 4) for i in range(1, 13)]))
    asyncio.run(a._deliver(entry))                                    # real reading again: tag gone
    assert sent[-1].startswith("[CRITICAL]")


def test_saturated_adc_reading_of_1024_is_accepted(client):
    """An ESP8266 analogRead() returns 1024 when the light sensor saturates; that must not be a validation error."""
    t0 = 1_800_000_000
    p = build_payload(1, t0 + 60, [{"t": t0 + 60, "temp_c": 3, "hum": 50, "light": 1024}])
    r = client.post("/api/telemetry", json=p)
    assert r.status_code == 200 and r.json()["accepted"] is True
