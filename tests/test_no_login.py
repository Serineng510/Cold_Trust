"""The judge demo must never ask anyone to sign in, even when the machine's settings define passwords."""
import importlib
import sys

import pytest

LOGIN_KEYS = ("ADMIN_ID", "ADMIN_NAME", "ADMIN_CODE", "DRIVER_ID", "DRIVER_NAME", "DRIVER_CODE")


def start_server(monkeypatch, tmp_path, *flags):
    """Run backend.__main__.main() with the given flags, without really starting uvicorn."""
    import uvicorn
    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append((a, k)))
    for key, value in zip(LOGIN_KEYS, ("ADM-1001", "Serine Ng", "admin-pass-1234", "DRV-20418", "Marcus Lee", "driver-pass-5678")):
        monkeypatch.setenv(f"COLDTRUST_{key}", value)             # the settings a developer's own .env would give
    for key in ("COLDTRUST_DB", "COLDTRUST_EVIDENCE_DIR", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(key, "")
    monkeypatch.setattr(sys, "argv", ["backend", "--db", str(tmp_path / "t.db"), *flags])
    from backend import __main__ as entry
    entry.main()
    assert started, "the server was not started"


def login_enabled():
    import backend.auth as auth
    import backend.config as config
    importlib.reload(config)
    return auth.Auth.from_config(config.load()).enabled


def test_with_the_passwords_set_the_login_is_on_by_default(monkeypatch, tmp_path):
    start_server(monkeypatch, tmp_path)
    assert login_enabled() is True


def test_no_login_flag_opens_the_dashboard_even_when_passwords_are_set(monkeypatch, tmp_path):
    start_server(monkeypatch, tmp_path, "--no-login")
    assert login_enabled() is False


def test_the_judge_script_always_passes_no_login_and_never_telegram():
    from pathlib import Path
    script = (Path(__file__).resolve().parent.parent / "judge_demo.ps1").read_text(encoding="utf-8")
    line = next(ln for ln in script.splitlines() if "-m backend" in ln and "--port" in ln)
    assert "--no-login" in line and "--no-telegram" in line and "--timelapse" in line and "--reset" in line
    assert "judge_demo.db" in line                                 # its own database, never the hardware or official one
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    assert "--no-telegram --no-login" in readme                    # the manual commands in the README agree
