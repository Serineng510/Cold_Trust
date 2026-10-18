"""Runtime settings, read from environment variables (see README for the full list)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader for cold-chain/.env. Real environment variables always win."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


DEFAULT_SECRET = "dev-secret-change-me"


@dataclass(frozen=True)
class Settings:
    # Device trust
    device_secret: str = os.getenv("COLDTRUST_SECRET", DEFAULT_SECRET)
    strict_time: bool = _bool("COLDTRUST_STRICT_TIME", True)  # off for timelapse simulation
    ts_window_s: float = _float("COLDTRUST_TS_WINDOW_S", 300)

    # Storage
    db_path: str = os.getenv("COLDTRUST_DB", "data/coldtrust.db")
    evidence_dir: str = os.getenv("COLDTRUST_EVIDENCE_DIR", "data/evidence")

    # Detection thresholds (the firmware mirrors these)
    rate_limit_c_per_min: float = _float("COLDTRUST_RATE_C_PER_MIN", 0.5)
    step_change_c: float = _float("COLDTRUST_STEP_C", 5.0)
    step_window_s: float = _float("COLDTRUST_STEP_WINDOW_S", 60)
    rate_window_s: float = _float("COLDTRUST_RATE_WINDOW_S", 240)
    light_bright_pct: float = _float("COLDTRUST_LIGHT_PCT", 30)
    ambient_c: float = _float("COLDTRUST_AMBIENT_C", 31)  # outside air, for contradiction checks
    door_open_contradiction_s: float = _float("COLDTRUST_DOOR_CONTRA_S", 300)
    zero_variance_min_samples: int = int(_float("COLDTRUST_ZV_MIN", 20))

    temp_cross_check_c: float = _float("COLDTRUST_TEMP_CROSSCHECK_C", 3.0)  # max gap between the two thermometers

    # Watchdog (CISA: have a response plan "if temperature does not hold or connectivity is lost")
    silent_after_s: float = _float("COLDTRUST_SILENT_AFTER_S", 1800)  # 2 missed 15-min routine pings
    gps_stale_s: float = _float("COLDTRUST_GPS_STALE_S", 600)

    # EPCIS
    tz_offset: str = os.getenv("COLDTRUST_TZ_OFFSET", "-05:00")  # demo route is Dallas-Memphis (US Central, CDT)

    # Demo helpers
    budget_scale: float = _float("COLDTRUST_BUDGET_SCALE", 1.0)  # <1 shrinks excursion budgets for demos

    # LLM
    llm_mode: str = os.getenv("COLDTRUST_LLM", "auto")  # auto: only if ANTHROPIC_API_KEY is set | on | off
    llm_model: str = os.getenv("COLDTRUST_MODEL", "claude-opus-5-5")

    # Telegram (optional)
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_ids: tuple[str, ...] = tuple(
        c.strip() for c in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()
    )


def load() -> Settings:
    return Settings()
