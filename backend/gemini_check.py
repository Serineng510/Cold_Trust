"""Check the Gemini setup: python -m backend.gemini_check

Reads GEMINI_API_KEY from cold-chain/.env (or the environment), lists the models your key can use, and runs one
tiny request through each of the three agents. The key itself is never printed.
"""
from __future__ import annotations

import logging
import os
import sys

import httpx

from . import config
from .llm import AGENTS, FREE_TIER, GEMINI_BASE, Explainer


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="       %(message)s")
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        print("[FAIL] GEMINI_API_KEY is not set.")
        print("       1. Open https://aistudio.google.com/apikey and create a key.")
        print("       2. Add this line to cold-chain/.env:   GEMINI_API_KEY=your-key")
        return 1
    print(f"[ok] key found ({len(key)} characters)")

    cfg = config.load()
    try:
        r = httpx.get(f"{GEMINI_BASE}/models", headers={"x-goog-api-key": key}, params={"pageSize": 200}, timeout=20)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(f"[FAIL] Google rejected the key (HTTP {exc.response.status_code}). Check it was copied in full.")
        return 1
    except httpx.HTTPError as exc:
        print(f"[FAIL] Could not reach Google: {exc.__class__.__name__}")
        return 1
    names = sorted(m["name"].removeprefix("models/") for m in r.json().get("models", [])
                   if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m["name"])
    print(f"[ok] key accepted; {len(names)} Gemini models support generateContent, e.g. " + ", ".join(names[:6]))
    if cfg.gemini_model not in names:
        print(f"[CHECK] GEMINI_MODEL={cfg.gemini_model} is not in that list. Set GEMINI_MODEL in .env to one of them.")

    ex = Explainer(cfg)
    if not ex.enabled:
        print(f"[FAIL] AI agents are off (COLDTRUST_LLM={cfg.llm_mode}, COLDTRUST_LLM_PROVIDER={cfg.llm_provider}).")
        return 1
    print(f"[ok] provider {ex.provider_label}, model {ex.model}")
    free = FREE_TIER.get(ex.model)
    if free:
        rpm, rpd = ex._caps()
        print(f"[ok] free tier for {ex.model}: {free[0]}/min, {free[1]}/day; this app stays under {rpm}/min and {rpd}/day")
    else:
        print(f"[CHECK] {ex.model} is not in the free-tier table (backend/llm.py FREE_TIER); assuming the strictest limit (5/min, 20/day). "
              "Check your AI Studio rate-limit page: a model showing 0 / 0 / 0 has no free tier.")
    print("        Billing must stay OFF for the project (the rate-limit page shows 'Free tier' and a 'Set up billing' button).")
    sample = {"scenario": "check", "severity": "warning", "findings": ["Temperature 6.1 C, limit 2-4 C for 12 min"],
              "status_decided_by_rules": "Under review", "products": []}
    kinds = {"thermal": "THERMAL_EXCURSION", "seal": "DOOR_AJAR", "incident": "SUSPICIOUS_PERSON"}
    bad = 0
    for agent in AGENTS:
        res = ex.explain(sample, kinds[agent])
        if res:
            print(f"[PASS] {AGENTS[agent]['label']}: {res['explanation'][:90]}")
        else:
            bad += 1
            print(f"[FAIL] {AGENTS[agent]['label']}: no usable answer (see log above; quota or model name?)")
    print("\nCamera frames to Gemini:", "ON" if ex.vision else "off (set COLDTRUST_VISION=on to allow; see README)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
