"""Metric units: km/h, km, litres, kg, m3."""
from backend.config import Settings
from backend.engine import Engine
from backend.seed import GEOFENCES, ROUTE_DISTANCE_KM, VEHICLE, migrate_units, seed
from backend.store import Store
from sim.sim import SECRET

DEPOT = next(g for g in GEOFENCES if g["kind"] == "depot")


def make():
    store = Store(":memory:")
    seed(store)
    return Engine(store, Settings(strict_time=False, device_secret=SECRET))


def test_demo_cargo_fits_the_truck_in_metric_units():
    eng = make()
    totals = eng.state()["totals"]
    assert 0 < totals["weight_kg"] <= VEHICLE["payload_capacity_kg"] == 20000
    assert 0 < totals["volume_m3"] <= VEHICLE["volume_capacity_m3"] == 68
    assert 340 < ROUTE_DISTANCE_KM < 380 and VEHICLE["route"]["distance_km"] == ROUTE_DISTANCE_KM
    assert eng.store.kv_get("vehicle")["speed_kmh"] == 0 and eng.store.kv_get("vehicle")["fuel_l"] > 100


def test_authorised_stop_needs_a_speed_under_8_kmh():
    eng = make()
    eng.set_vehicle({"lat": DEPOT["lat"], "lon": DEPOT["lon"], "speed_kmh": 7})
    assert eng.store.kv_get("vehicle")["authorised_stop"] is True
    eng.set_vehicle({"lat": DEPOT["lat"], "lon": DEPOT["lon"], "speed_kmh": 9})
    assert eng.store.kv_get("vehicle")["authorised_stop"] is False


def test_standing_still_on_the_route_means_under_2_kmh():
    from tests.test_engine import _light_event
    for speed, expected in ((1, "THEFT_SUSPECTED"), (3, "DOOR_AJAR")):
        eng = make()
        eng.set_vehicle({"route_status": "En route", "speed_kmh": speed, "authorised_stop": False})
        assert _light_event(eng)["scenario"] == expected, speed


def test_old_databases_are_converted_once():
    store = Store(":memory:")
    seed(store)
    pk = store.list_packages()[0]
    pk.pop("weight_kg"), pk.pop("volume_m3")
    pk.update(weight_lbs=1800, volume_ft3=120)                       # what an old database holds
    store.upsert_package(pk)
    store.kv_set("vehicle", {"speed_mph": 60, "fuel_gal": 62.0, "zone": None})
    migrate_units(store)
    migrate_units(store)                                             # second run changes nothing
    pk = next(p for p in store.list_packages() if p["id"] == pk["id"])
    assert pk["weight_kg"] == 820 and pk["volume_m3"] == 3.4 and "weight_lbs" not in pk
    veh = store.kv_get("vehicle")
    assert veh["speed_kmh"] == 96.6 and veh["fuel_l"] == 234.7 and "speed_mph" not in veh and "fuel_gal" not in veh
