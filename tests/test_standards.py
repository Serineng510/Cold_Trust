"""GS1 EPCIS 2.0 records and the CISA-derived safeguards."""
import importlib
import json
import urllib.request
from pathlib import Path

import pytest

from backend import epcis
from backend.config import DEFAULT_SECRET, Settings
from backend.engine import Engine
from backend.seed import seed
from backend.store import Store
from sim.sim import SECRET
from tests.test_engine import run_scenario

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "tests" / "fixtures" / "gs1-sandbox-hash-vectors.json"
SCHEMA_URL = "https://ref.gs1.org/standards/epcis/epcis-json-schema.json"
SCHEMA_CACHE = Path(__file__).resolve().parent / ".cache" / "epcis-json-schema.json"


@pytest.fixture()
def eng():
    store = Store(":memory:")
    seed(store)
    return Engine(store, Settings(strict_time=False, device_secret=SECRET))


def events(eng):
    return eng.store.list_epcis()


# ------------------------------------------------------------------ hashing and identifiers

def test_event_hash_matches_gs1_sandbox_vectors():
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"]
    assert vectors
    for v in vectors:
        doc = v["document"]
        for ev in doc["epcisBody"]["eventList"]:
            body = {k: x for k, x in ev.items() if k != "eventID"}
            assert epcis.event_hash_id(body, doc.get("@context")) == v["expected"], v["name"]


def test_gs1_check_digits_and_digital_links():
    assert epcis.gs1_check_digit("400638133393") == "1"  # published GTIN-13 4006381333931
    for uri in (epcis.sscc(8841), epcis.gln(1), epcis.pgln(7)):
        assert epcis.is_valid_digital_link(uri), uri
    assert not epcis.is_valid_digital_link(epcis.sscc(8841)[:-1] + "0" if not epcis.sscc(8841).endswith("0")
                                           else epcis.sscc(8841)[:-1] + "1")


def test_utc_millis_matches_javascript_iso_format():
    assert epcis.utc_millis("2023-09-12T15:00:03.321Z") == "2023-09-12T15:00:03.321Z"
    assert epcis.utc_millis("2023-09-12T17:00:03+02:00") == "2023-09-12T15:00:03.000Z"
    assert epcis.utc_millis(1_800_000_000.1239) == "2027-01-15T08:00:00.123Z"


# ------------------------------------------------------------------ the trip record

def test_door_ajar_trip_record(eng):
    run_scenario(eng, "door_ajar")
    evs = events(eng)
    assert evs[0]["bizStep"] == "departing" and evs[0]["disposition"] == "in_transit"
    transport = [e for e in evs if e["bizStep"] == "transporting"]
    assert transport and all(e["sensorElementList"][0]["sensorReport"][0]["type"] == "Temperature" for e in transport)
    assert any(r.get("type") == "Illuminance" and r.get("exception") == "ALARM_CONDITION"
               for e in transport for r in e["sensorElementList"][0]["sensorReport"])
    holding = [e for e in evs if e["bizStep"] == "holding"]
    assert holding and holding[0]["disposition"] == "unknown" and len(holding[0]["epcList"]) == 10
    assert all(v["valid"] for v in epcis.verify_events(evs))


def test_theft_records_quarantine_but_never_claims_stolen(eng):
    run_scenario(eng, "theft")
    q = [e for e in events(eng) if e["disposition"] == "non_conformant"]
    assert q and q[0]["bizStep"] == "inspecting"
    assert q[0]["persistentDisposition"] == {"set": ["non_conformant"]}
    assert q[0]["ct:cause"] == "THEFT_SUSPECTED"
    assert not any(e["disposition"] == "stolen" for e in events(eng))


def test_supervisor_release_unsets_the_quarantine(eng):
    run_scenario(eng, "theft")
    eng.override("SHP-8841", "NORMAL", "seals intact, inspected on site", "Lisa", eng.now())
    last = events(eng)[-1]
    assert last["disposition"] == "conformant"
    assert last["persistentDisposition"] == {"unset": ["non_conformant"]}
    assert last["ct:supervisor"] == "Lisa" and last["epcList"] == [epcis.package_epc("SHP-8841")]


def test_editing_a_stored_event_is_detected(eng):
    run_scenario(eng, "door_ajar")
    evs = events(eng)
    report = evs[1]["sensorElementList"][0]["sensorReport"][0]
    report["maxValue"] = report["maxValue"] - 4  # someone "improves" the temperature history
    results = epcis.verify_events(evs)
    assert results[1]["valid"] is False and all(r["valid"] for i, r in enumerate(results) if i != 1)


def _schema():
    if not SCHEMA_CACHE.exists():
        try:
            with urllib.request.urlopen(SCHEMA_URL, timeout=20) as r:
                data = r.read()
        except OSError as exc:
            pytest.skip(f"cannot download the official EPCIS schema: {exc}")
        SCHEMA_CACHE.parent.mkdir(exist_ok=True)
        SCHEMA_CACHE.write_bytes(data)
    return json.loads(SCHEMA_CACHE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("scenario", ["theft", "compressor_fail", "sensor_disagree"])
def test_trip_document_validates_against_official_epcis_schema(eng, scenario):
    jsonschema = pytest.importorskip("jsonschema")
    run_scenario(eng, scenario)
    eng.override("SHP-4574", "NORMAL", "re-checked with handheld thermometer", "Lisa", eng.now())
    doc = epcis.document(events(eng), epcis.utc_millis(eng.now()))
    errors = sorted(jsonschema.Draft7Validator(_schema()).iter_errors(doc), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message[:200]}" for e in errors[:5])


# ------------------------------------------------------------------ CISA-derived safeguards

def test_independent_thermometer_disagreement_is_a_data_trust_alarm(eng):
    res = run_scenario(eng, "sensor_disagree")
    flagged = [r for r in res if any(f["code"] == "SENSOR_DISAGREEMENT" for f in r["entry"]["findings"])]
    assert flagged and flagged[0]["scenario"] == "DATA_TRUST" and flagged[0]["notify"]
    # one weak signal alerts people but does not change custody
    assert {p["status"] for p in eng.store.list_packages()} == {"NORMAL"}
    errs = [r for e in events(eng) if e["bizStep"] == "transporting"
            for r in e["sensorElementList"][0]["sensorReport"] if r.get("exception") == "ERROR_CONDITION"]
    assert errs


def _live_engine():
    """Wall-clock engine, as on a real trip (the watchdog is off in timelapse simulations)."""
    store = Store(":memory:")
    seed(store)
    return Engine(store, Settings(strict_time=True, device_secret=SECRET))


def test_watchdog_reports_silence_once_and_recovery_once():
    eng = _live_engine()
    t = 1_900_000_000.0
    assert eng.check_health(t) == []  # never heard from the device yet: nothing to miss
    eng.store.kv_set("device:CC-001", {"last_seq": 5, "last_seen": t})
    assert eng.check_health(t + 60) == []
    silent = eng.check_health(t + eng.cfg.silent_after_s + 5)
    assert [x["entry"]["kind"] for x in silent] == ["NODE_SILENT"] and silent[0]["notify"]
    assert eng.check_health(t + eng.cfg.silent_after_s + 600) == []  # reported once, not every loop
    eng.store.kv_set("device:CC-001", {"last_seq": 6, "last_seen": t + eng.cfg.silent_after_s + 700})
    back = eng.check_health(t + eng.cfg.silent_after_s + 710)
    assert [x["entry"]["kind"] for x in back] == ["NODE_RESTORED"]


def test_watchdog_flags_stale_gps_only_while_en_route():
    eng = _live_engine()
    eng.set_vehicle({"speed_mph": 55, "authorised_stop": False})
    t = eng.store.kv_get("vehicle")["updated_at"]
    assert "GPS_STALE" in [x["entry"]["kind"] for x in eng.check_health(t + eng.cfg.gps_stale_s + 1)]
    parked = _live_engine()
    parked.set_vehicle({"speed_mph": 0, "authorised_stop": True})
    t = parked.store.kv_get("vehicle")["updated_at"]
    assert parked.check_health(t + parked.cfg.gps_stale_s + 1) == []


def test_watchdog_is_off_in_timelapse(eng):
    run_scenario(eng, "normal")
    assert eng.check_health(eng.now() + 10 ** 6) == []


def test_thermal_advice_names_the_backup_custodian(eng):
    res = run_scenario(eng, "compressor_fail")
    advice = next(r["entry"]["recommended_actions"] for r in res if r["scenario"] == "THERMAL_EXCURSION")
    assert any("Texarkana Cold Store" in a for a in advice)


def _load_agent(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("COLDTRUST_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("COLDTRUST_LLM", "off")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import backend.config as c
    importlib.reload(c)
    import backend.agent as a
    return importlib.reload(a)


def test_real_hardware_mode_refuses_the_default_secret(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="Refusing to start"):
        _load_agent(monkeypatch, tmp_path, COLDTRUST_STRICT_TIME="1", COLDTRUST_SECRET=DEFAULT_SECRET)
    with pytest.raises(RuntimeError):
        _load_agent(monkeypatch, tmp_path, COLDTRUST_STRICT_TIME="1", COLDTRUST_SECRET="short")
    assert _load_agent(monkeypatch, tmp_path, COLDTRUST_STRICT_TIME="1", COLDTRUST_SECRET="x" * 32).app
    assert _load_agent(monkeypatch, tmp_path, COLDTRUST_STRICT_TIME="0", COLDTRUST_SECRET=DEFAULT_SECRET).app


def test_epcis_endpoints(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from sim.sim import build_payload
    a = _load_agent(monkeypatch, tmp_path, COLDTRUST_STRICT_TIME="0", COLDTRUST_SECRET=SECRET)
    client = TestClient(a.app)
    base = 1_800_000_000
    samples = [{"t": base + i * 5, "temp_c": 3, "hum": 50, "light": 20 + i % 3} for i in range(1, 13)]
    assert client.post("/api/telemetry", json=build_payload(1, base + 60, samples)).json()["accepted"]
    doc = client.get("/api/epcis")
    assert doc.headers["content-type"].startswith("application/ld+json")
    assert [e["bizStep"] for e in doc.json()["epcisBody"]["eventList"]] == ["departing", "transporting"]
    q = client.get("/api/epcis/events", params={"EQ_bizStep": "transporting"}).json()
    assert len(q["epcisBody"]["queryResults"]["resultsBody"]["eventList"]) == 1
    assert client.get("/api/epcis/verify").json() == {"events": 2, "all_valid": True, "invalid": []}


def test_gps_position_sets_zone_and_authorised_stop(eng):
    eng.set_vehicle({"lat": 32.7767, "lon": -96.7970, "speed_mph": 0})       # parked at Dallas Depot
    v = eng.store.kv_get("vehicle")
    assert v["zone"] == "Dallas Depot" and v["authorised_stop"] is True
    eng.set_vehicle({"lat": 33.0500, "lon": -96.1000, "speed_mph": 60})      # on the highway
    v = eng.store.kv_get("vehicle")
    assert v["zone"] is None and v["authorised_stop"] is False
    eng.set_vehicle({"lat": 33.6, "lon": -94.4, "speed_mph": 0})              # stopped in the high-risk zone
    v = eng.store.kv_get("vehicle")
    assert v["zone"] == "I-30 High-Risk Stretch" and v["authorised_stop"] is False
