"""SQLite persistence. One connection guarded by a lock is plenty for a single-vehicle prototype."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

from .rules import Sample

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings(
  device_id TEXT NOT NULL, t REAL NOT NULL, temp_c REAL NOT NULL, hum REAL, light_raw REAL,
  ping_type TEXT, PRIMARY KEY(device_id, t));
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS packages(id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS epcis(n INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, doc TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            try:  # databases created before the second thermometer existed
                self._db.execute("ALTER TABLE readings ADD COLUMN temp2_c REAL")
            except sqlite3.OperationalError:
                pass

    # ------------------------------------------------------------ readings
    def add_readings(self, device_id: str, samples: list[Sample], ping_type: str) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO readings(device_id,t,temp_c,hum,light_raw,ping_type,temp2_c) "
                "VALUES(?,?,?,?,?,?,?)",
                [(device_id, s.t, s.temp_c, s.hum, s.light_raw, ping_type, s.temp2_c) for s in samples])

    def recent_readings(self, device_id: str, window_s: float, until: float) -> list[Sample]:
        with self._lock:
            rows = self._db.execute(
                "SELECT t,temp_c,hum,light_raw,temp2_c FROM readings WHERE device_id=? AND t>=? AND t<=? ORDER BY t",
                (device_id, until - window_s, until)).fetchall()
        return [Sample(r["t"], r["temp_c"], r["hum"], r["light_raw"], r["temp2_c"]) for r in rows]

    def last_sample_t(self, device_id: str) -> float | None:
        with self._lock:
            r = self._db.execute("SELECT MAX(t) m FROM readings WHERE device_id=?", (device_id,)).fetchone()
        return r["m"]

    # ------------------------------------------------------------ audit log
    def add_audit(self, ts: float, doc: dict[str, Any]) -> int:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO audit(ts, doc) VALUES(?,?)", (ts, json.dumps(doc)))
            return int(cur.lastrowid)

    def update_audit(self, audit_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock, self._db:
            r = self._db.execute("SELECT doc FROM audit WHERE id=?", (audit_id,)).fetchone()
            if not r:
                return None
            doc = {**json.loads(r["doc"]), **patch}
            self._db.execute("UPDATE audit SET doc=? WHERE id=?", (json.dumps(doc), audit_id))
            return doc

    def audit_tail(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, ts, doc FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], **json.loads(r["doc"])} for r in reversed(rows)]

    def audit_get(self, audit_id: int) -> dict[str, Any] | None:
        with self._lock:
            r = self._db.execute("SELECT id, ts, doc FROM audit WHERE id=?", (audit_id,)).fetchone()
        return {"id": r["id"], "ts": r["ts"], **json.loads(r["doc"])} if r else None

    # ------------------------------------------------------------ packages
    def upsert_package(self, pkg: dict[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO packages VALUES(?,?)", (pkg["id"], json.dumps(pkg)))

    def list_packages(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT doc FROM packages ORDER BY id").fetchall()
        return [json.loads(r["doc"]) for r in rows]

    def get_package(self, pkg_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._db.execute("SELECT doc FROM packages WHERE id=?", (pkg_id,)).fetchone()
        return json.loads(r["doc"]) if r else None

    def package_by_token(self, token: str) -> dict[str, Any] | None:
        return next((p for p in self.list_packages() if p.get("custody_token") == token), None)

    # ------------------------------------------------------------ EPCIS events (append-only)
    def add_epcis(self, event: dict[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO epcis(event_id, doc) VALUES(?,?)", (event["eventID"], json.dumps(event)))

    def list_epcis(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT doc FROM epcis ORDER BY n").fetchall()
        return [json.loads(r["doc"]) for r in rows]

    # ------------------------------------------------------------ kv
    def kv_get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            r = self._db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(r["v"]) if r else default

    def kv_set(self, key: str, value: Any) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, json.dumps(value)))
