"""python -m backend [--timelapse] [--reset] [--port 8000]"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# Work from anywhere: resolve the project root, make `backend` importable, keep data paths relative to it.
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path[:] = [str(ROOT)] + [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m backend")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--timelapse", action="store_true",
                    help="trust the device clock so simulated scenarios can run instantly (disables the timestamp-window check)")
    ap.add_argument("--reset", action="store_true", help="delete the local database and evidence before starting")
    ap.add_argument("--no-telegram", action="store_true", help="never send Telegram messages (testing)")
    ap.add_argument("--demo", action="store_true",
                    help="enable the dashboard's simulated-temperature controls (for demos; readings stay labelled)")
    ap.add_argument("--db", help="database file (default data/coldtrust.db); give each running server its own")
    a = ap.parse_args()

    if a.timelapse:
        os.environ["COLDTRUST_STRICT_TIME"] = "0"
    if a.demo:
        os.environ["COLDTRUST_DEMO"] = "1"
    if a.no_telegram:  # an empty value wins over cold-chain/.env
        os.environ["TELEGRAM_BOT_TOKEN"] = ""
    if a.db:
        os.environ["COLDTRUST_DB"] = a.db
        os.environ.setdefault("COLDTRUST_EVIDENCE_DIR", str(Path(a.db).with_suffix("")) + "-evidence")
    if a.reset:  # must happen before the app module opens the database
        db = Path(os.getenv("COLDTRUST_DB", "data/coldtrust.db"))
        for f in (db, Path(str(db) + "-journal")):
            try:
                f.unlink(missing_ok=True)
            except PermissionError:
                sys.exit(f"Cannot reset {f}: another ColdTrust server is using it. Stop that server first, "
                         "or start this one with its own --db file.")
        shutil.rmtree(os.getenv("COLDTRUST_EVIDENCE_DIR", "data/evidence"), ignore_errors=True)

    import uvicorn
    uvicorn.run("backend.agent:app", host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
