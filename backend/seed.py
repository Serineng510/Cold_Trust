"""Demo data: one vehicle, ten packages with their own temperature limits and excursion budgets.

Budgets and limits are illustrative, not clinical guidance.
"""
from __future__ import annotations

import secrets
from typing import Any

from .route_data import ROUTE, ROUTE_DISTANCE_KM, ROUTE_DURATION_MIN, ROUTE_SOURCE  # noqa: F401  (road geometry from OSRM)
from .store import Store

VEHICLE: dict[str, Any] = {
    "id": "WXY 9913",
    "model": "Hino 500 Reefer",
    "driver_name": "Marcus Lee",
    "driver_id": "DRV-20418",
    "device_id": "CC-001",
    "route": {"from": "Shah Alam, Selangor", "to": "Johor Bahru, Johor", "distance_km": ROUTE_DISTANCE_KM,
              "duration_min": ROUTE_DURATION_MIN, "source": ROUTE_SOURCE},
    "payload_capacity_kg": 20000,
    "volume_capacity_m3": 68,
    "hold_limits_c": [2.0, 8.0],
}

# id, description, qty, lo, hi, budget_min, priority, weight_kg, volume_m3, tags, client, destination, order
_PACKAGES = [
    ("SHP-8841", "Vaccine vials", 200, 2, 8, 30, "Critical", 820, 3.4, ["Fragile", "Cold chain"], "Mercy Health Network", "Johor Bahru, Johor", 1),
    ("SHP-4574", "Insulin pens", 24, 2, 8, 45, "High", 1900, 9.1, ["Fragile", "Cold chain"], "TechFlow Pharmacy", "Johor Bahru, Johor", 4),
    ("SHP-9856", "Biologic samples", 12, 2, 8, 20, "Critical", 950, 5.1, ["Fragile", "Cold chain"], "Delta Research Lab", "Seremban, Negeri Sembilan", 3),
    ("SHP-8578", "Blood plasma bags", 12, 2, 6, 15, "Critical", 1180, 5.7, ["Cold chain"], "St. Luke's Hospital", "Johor Bahru, Johor", 2),
    ("SHP-2364", "Fresh salmon fillets", 80, 0, 4, 60, "Normal", 1630, 7.4, ["Perishable"], "Harbor Foods", "Melaka, Melaka", 6),
    ("SHP-7841", "Yoghurt crates", 120, 2, 6, 90, "Normal", 2180, 9.6, ["Perishable"], "Green Valley Dairy", "Melaka, Melaka", 7),
    ("SHP-1248", "Fresh berries", 60, 1, 7, 120, "Low", 1090, 7.1, ["Perishable", "Fragile"], "FreshMart", "Johor Bahru, Johor", 5),
    ("SHP-8978", "Cut flowers", 40, 2, 10, 240, "Low", 680, 8.5, ["Fragile"], "Bloom & Co", "Seremban, Negeri Sembilan", 8),
    ("SHP-7801", "Sliced deli meats", 50, 0, 5, 60, "Normal", 1360, 6.2, ["Perishable"], "Metro Deli", "Melaka, Melaka", 9),
    ("SHP-5678", "Ready meals", 90, 0, 5, 90, "High", 1220, 5.7, ["Perishable"], "CityEats", "Johor Bahru, Johor", 10),
]

GEOFENCES = [
    {"name": "Shah Alam Depot", "kind": "depot", "lat": 3.0738, "lon": 101.5183, "radius_m": 1500, "authorised": True},
    {"name": "Johor Bahru Dock 4", "kind": "bay", "lat": 1.4854, "lon": 103.7618, "radius_m": 600, "authorised": True},
    {"name": "Melaka Dock 2", "kind": "bay", "lat": 2.2740, "lon": 102.2890, "radius_m": 600, "authorised": True},
    {"name": "NSE High-Risk Stretch", "kind": "risk", "lat": 2.0136, "lon": 103.0599, "radius_m": 25000, "authorised": False},
    # CISA: "identify alternative custodians" in case refrigeration fails
    {"name": "Seremban Cold Store", "kind": "backup", "lat": 2.7297, "lon": 101.9381, "radius_m": 800,
     "authorised": True},
]

BACKUP_CUSTODIAN = "Seremban Cold Store"

def fence_at(lat: float, lon: float) -> dict[str, Any] | None:
    """The geofence containing this GPS point (smallest first, so a dock inside a big zone wins)."""
    import math
    hits = []
    for g in GEOFENCES:
        p1, p2 = math.radians(lat), math.radians(g["lat"])
        dp, dl = p2 - p1, math.radians(g["lon"] - lon)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        if 6_371_000 * 2 * math.asin(math.sqrt(a)) <= g["radius_m"]:
            hits.append(g)
    return min(hits, key=lambda g: g["radius_m"]) if hits else None


def location_gln(name: str) -> str:
    """Stable demo GLN per geofence (GS1 test prefix 952...)."""
    from .epcis import gln
    idx = next(i for i, g in enumerate(GEOFENCES) if g["name"] == name)
    return gln(idx + 1)


def migrate_units(store: Store) -> None:
    """Databases made before the metric change hold pounds, cubic feet, mph and gallons: convert them once."""
    for pk in store.list_packages():
        if "weight_lbs" in pk:
            pk["weight_kg"] = round(pk.pop("weight_lbs") * 0.45359237 / 10) * 10
            pk["volume_m3"] = round(pk.pop("volume_ft3") * 0.0283168, 1)
            store.upsert_package(pk)
    veh = store.kv_get("vehicle")
    if veh and ("speed_mph" in veh or "fuel_gal" in veh):
        if "speed_mph" in veh:
            veh["speed_kmh"] = round(veh.pop("speed_mph") * 1.609344, 1)
        if "fuel_gal" in veh:
            veh["fuel_l"] = round(veh.pop("fuel_gal") * 3.78541, 1)
        store.kv_set("vehicle", veh)


def seed(store: Store, force: bool = False) -> None:
    migrate_units(store)
    if store.list_packages() and not force:
        return
    for (pid, desc, qty, lo, hi, budget, prio, wt, vol, tags, client, dest, order) in _PACKAGES:
        store.upsert_package({
            "id": pid, "description": desc, "quantity": qty,
            "temp_min_c": lo, "temp_max_c": hi, "excursion_budget_min": budget,
            "priority": prio, "weight_kg": wt, "volume_m3": vol, "tags": tags,
            "client": client, "destination": dest, "loading_order": order,
            "recipient": client,
            "status": "NORMAL", "risk_score": 0, "excursion_min": 0.0,
            "custody_token": secrets.token_hex(8), "token_valid": True,
            "instructions": "Keep within limits. Do not accept if QUARANTINED.",
        })
    store.kv_set("vehicle", {"speed_kmh": 0, "fuel_l": 235.0, "route_status": "Idle", "zone": "Shah Alam Depot",
                             "authorised_stop": True, "lat": 3.0738, "lon": 101.5183, "heading": "SE"})
