"""Demo data: one vehicle, ten packages with their own temperature limits and excursion budgets.

Budgets and limits are illustrative, not clinical guidance.
"""
from __future__ import annotations

import secrets
from typing import Any

from .store import Store

VEHICLE: dict[str, Any] = {
    "id": "TX-9913-HX",
    "model": "Kenworth T680",
    "driver_name": "Marcus Lee",
    "driver_id": "DRV-20418",
    "device_id": "CC-001",
    "route": {"from": "Dallas, TX", "to": "Memphis, TN", "distance_mi": 282.1},
    "payload_capacity_lbs": 44000,
    "volume_capacity_ft3": 2400,
    "hold_limits_c": [2.0, 8.0],
}

# id, description, qty, lo, hi, budget_min, priority, weight_lbs, volume_ft3, tags, client, destination, order
_PACKAGES = [
    ("SHP-8841", "Vaccine vials", 200, 2, 8, 30, "Critical", 1800, 120, ["Fragile", "Cold chain"], "Mercy Health Network", "Memphis, TN", 1),
    ("SHP-4574", "Insulin pens", 24, 2, 8, 45, "High", 4200, 320, ["Fragile", "Cold chain"], "TechFlow Pharmacy", "Memphis, TN", 4),
    ("SHP-9856", "Biologic samples", 12, 2, 8, 20, "Critical", 2100, 180, ["Fragile", "Cold chain"], "Delta Research Lab", "Jackson, MS", 3),
    ("SHP-8578", "Blood plasma bags", 12, 2, 6, 15, "Critical", 2600, 200, ["Cold chain"], "St. Luke's Hospital", "Memphis, TN", 2),
    ("SHP-2364", "Fresh salmon fillets", 80, 0, 4, 60, "Normal", 3600, 260, ["Perishable"], "Harbor Foods", "Little Rock, AR", 6),
    ("SHP-7841", "Yoghurt crates", 120, 2, 6, 90, "Normal", 4800, 340, ["Perishable"], "Green Valley Dairy", "Little Rock, AR", 7),
    ("SHP-1248", "Fresh berries", 60, 1, 7, 120, "Low", 2400, 250, ["Perishable", "Fragile"], "FreshMart", "Memphis, TN", 5),
    ("SHP-8978", "Cut flowers", 40, 2, 10, 240, "Low", 1500, 300, ["Fragile"], "Bloom & Co", "Jackson, MS", 8),
    ("SHP-7801", "Sliced deli meats", 50, 0, 5, 60, "Normal", 3000, 220, ["Perishable"], "Metro Deli", "Little Rock, AR", 9),
    ("SHP-5678", "Ready meals", 90, 0, 5, 90, "High", 2700, 200, ["Perishable"], "CityEats", "Memphis, TN", 10),
]

GEOFENCES = [
    {"name": "Dallas Depot", "kind": "depot", "lat": 32.7767, "lon": -96.7970, "radius_m": 1500, "authorised": True},
    {"name": "Memphis Dock 4", "kind": "bay", "lat": 35.1495, "lon": -90.0490, "radius_m": 600, "authorised": True},
    {"name": "Little Rock Dock 2", "kind": "bay", "lat": 34.7465, "lon": -92.2896, "radius_m": 600, "authorised": True},
    {"name": "I-30 High-Risk Stretch", "kind": "risk", "lat": 33.6, "lon": -94.4, "radius_m": 25000, "authorised": False},
    # CISA: "identify alternative custodians" in case refrigeration fails
    {"name": "Texarkana Cold Store", "kind": "backup", "lat": 33.4251, "lon": -94.0477, "radius_m": 800,
     "authorised": True},
]
BACKUP_CUSTODIAN = "Texarkana Cold Store"

# Planned route, Dallas Depot -> Memphis Dock 4 (lat, lon). The map draws it; the simulator drives along it.
ROUTE = [
    (32.7767, -96.7970),   # Dallas Depot
    (33.0500, -96.1000),
    (33.3800, -95.4000),
    (33.6000, -94.4000),   # I-30 high-risk stretch
    (33.4251, -94.0477),   # Texarkana (backup cold store)
    (34.0000, -93.3000),
    (34.7465, -92.2896),   # Little Rock Dock 2
    (35.0500, -91.2000),
    (35.1495, -90.0490),   # Memphis Dock 4
]


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


def seed(store: Store, force: bool = False) -> None:
    if store.list_packages() and not force:
        return
    for (pid, desc, qty, lo, hi, budget, prio, wt, vol, tags, client, dest, order) in _PACKAGES:
        store.upsert_package({
            "id": pid, "description": desc, "quantity": qty,
            "temp_min_c": lo, "temp_max_c": hi, "excursion_budget_min": budget,
            "priority": prio, "weight_lbs": wt, "volume_ft3": vol, "tags": tags,
            "client": client, "destination": dest, "loading_order": order,
            "recipient": client,
            "status": "NORMAL", "risk_score": 0, "excursion_min": 0.0,
            "custody_token": secrets.token_hex(8), "token_valid": True,
            "instructions": "Keep within limits. Do not accept if QUARANTINED.",
        })
    store.kv_set("vehicle", {"speed_mph": 0, "fuel_gal": 62.0, "route_status": "Idle", "zone": "Dallas Depot",
                             "authorised_stop": True, "lat": 32.7767, "lon": -96.797, "heading": "NE"})
