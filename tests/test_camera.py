"""Camera: person sightings judged in context, live frames, camera key, photo alerts."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.engine import Engine
from backend.notify import Notifier
from backend.seed import seed
from backend.store import Store
from sim.sim import SECRET, build_payload

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 300


@pytest.fixture()
def eng():
    store = Store(":memory:")
    seed(store)
    return Engine(store, Settings(strict_time=False, device_secret=SECRET))


def person(n=1, behaviours=()):
    return {"person_present": True, "persons": n, "behaviours": list(behaviours), "source": "pose"}


def statuses(eng):
    return {p["status"] for p in eng.store.list_packages()}


def test_person_at_authorised_stop_is_logged_not_alerted(eng):
    eng.set_vehicle({"speed_kmh": 0, "authorised_stop": True, "zone": "Johor Bahru Dock 4"})
    r = eng.person_observed(person(), now=1000)
    assert r["entry"]["kind"] == "PERSON_AT_AUTHORISED_STOP" and r["notify"] is False
    assert statuses(eng) == {"NORMAL"}


def test_person_outside_authorised_stop_is_suspicious(eng):
    eng.set_vehicle({"speed_kmh": 58, "authorised_stop": False})
    r = eng.person_observed(person(1, ["reaching"]), now=1000)
    e = r["entry"]
    assert e["kind"] == "SUSPICIOUS_PERSON" and e["severity"] == "critical" and r["notify"]
    assert "reaching" in e["reasoning"] and "moving" in e["reasoning"]
    assert statuses(eng) == {"UNDER_REVIEW"}
    assert any(ev["bizStep"] == "holding" and ev["ct:cause"] == "SUSPICIOUS_PERSON" for ev in eng.store.list_epcis())


def test_same_person_is_not_logged_twice_but_new_behaviour_is(eng):
    eng.set_vehicle({"speed_kmh": 58, "authorised_stop": False})
    assert eng.person_observed(person(), now=1000)
    assert eng.person_observed(person(), now=1015) is None
    again = eng.person_observed(person(1, ["crouching"]), now=1030)
    assert again and "crouching" in again["entry"]["reasoning"]
    left = eng.person_observed({"person_present": False, "persons": 0}, now=1060)
    assert left["entry"]["kind"] == "PERSON_LEFT" and left["notify"] is False


def test_person_with_door_open_is_theft(eng):
    eng.set_vehicle({"speed_kmh": 0, "authorised_stop": False, "zone": "NSE High-Risk Stretch"})
    base = 1_800_000_000
    samples = [{"t": base + i * 5, "temp_c": 3, "hum": 50, "light": 700 + i % 3} for i in range(1, 7)]
    from backend.schemas import IngestIn
    eng.ingest(IngestIn(**build_payload(1, base + 30, samples, "EVENT", "LIGHT")))
    r = eng.person_observed(person(2), now=base + 35)
    assert r["entry"]["kind"] == "THEFT_SUSPECTED" and r["entry"]["severity"] == "emergency"
    assert statuses(eng) == {"QUARANTINED"}
    assert all(not p["token_valid"] for p in eng.store.list_packages())


def _client(monkeypatch, tmp_path, strict):
    monkeypatch.setenv("COLDTRUST_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("COLDTRUST_EVIDENCE_DIR", str(tmp_path / "ev"))
    monkeypatch.setenv("COLDTRUST_LLM", "off")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("COLDTRUST_STRICT_TIME", "1" if strict else "0")
    monkeypatch.setenv("COLDTRUST_SECRET", "k" * 32 if strict else SECRET)
    import backend.config as c
    importlib.reload(c)
    import backend.agent as a
    return TestClient(importlib.reload(a).app)


def test_live_frame_upload_status_and_latest(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, strict=False)
    assert client.get("/api/camera/status").json()["live"] is False
    assert client.get("/api/camera/latest.jpg").status_code == 404
    r = client.post("/api/camera/frame?persons=2&labels=reaching,crouching", content=JPEG,
                    headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200
    st = client.get("/api/camera/status").json()
    assert st["live"] and st["persons"] == 2 and st["labels"] == ["reaching", "crouching"]
    assert client.get("/api/camera/latest.jpg").content == JPEG
    assert client.get("/api/state").json()["camera_feed"]["live"] is True
    assert client.post("/api/camera/frame", content=b"not a jpeg").status_code == 400


def test_camera_endpoints_need_the_device_key_on_real_hardware(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, strict=True)
    client.post("/api/vehicle", json={"speed_kmh": 55, "authorised_stop": False})  # on the road, not at the depot
    obs = {"person_present": True, "persons": 1}
    assert client.post("/api/camera/observation", json=obs).status_code == 401
    assert client.post("/api/camera/frame", content=JPEG).status_code == 401
    assert client.post("/api/camera/observation", json=obs, headers={"X-ColdTrust-Key": "wrong"}).status_code == 401
    ok = client.post("/api/camera/observation", json=obs, headers={"X-ColdTrust-Key": "k" * 32})
    assert ok.status_code == 200 and ok.json()["entry"]["kind"] == "SUSPICIOUS_PERSON"


def test_photo_alert_is_a_multipart_upload(monkeypatch):
    sent = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"ok": True}).encode()

    def fake_urlopen(req, timeout):
        sent.append(req)
        return Resp()

    import backend.notify as n
    monkeypatch.setattr(n.urllib.request, "urlopen", fake_urlopen)
    notifier = Notifier(Settings(telegram_token="T", telegram_chat_ids=("-100",)))
    assert notifier.send_photo(JPEG, "[CRITICAL] SUSPICIOUS_PERSON: 1 person in the cargo hold") == 1
    req = sent[0]
    assert req.full_url.endswith("/sendPhoto")
    assert req.headers["Content-type"].startswith("multipart/form-data; boundary=")
    assert JPEG in req.data and b"SUSPICIOUS_PERSON" in req.data and b'name="chat_id"' in req.data
