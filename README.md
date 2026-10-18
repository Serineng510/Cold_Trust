# Frozen_Warden

**ColdTrust**: cold-chain integrity agent (prototype).

ESP8266 + DHT11 + light module send signed telemetry to a FastAPI backend. Deterministic rules decide *whether*
something is wrong; Claude explains *what it means for the product*. Status changes (quarantine, custody-token
revocation) are made by code, never by the model. Full design: [PLAN.md](PLAN.md).

## Run

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m backend --timelapse --reset          # http://127.0.0.1:8000  (docs at /docs)
.venv/Scripts/python -m sim.sim --list
.venv/Scripts/python -m sim.sim theft --timelapse
.venv/Scripts/python -m pytest -q
```

Open **http://127.0.0.1:8000** for the operations dashboard (Overview, Cargo, Maintenance, Route, Incidents).
It updates live over a WebSocket; click the speaker icon once to allow spoken alerts. Add `--no-telegram` while
testing so simulator runs do not message your Telegram group.

* `--timelapse` trusts the device clock so a 45-minute scenario plays instantly. Do **not** use it with real
  hardware: it disables the timestamp-window replay check.
* Use `--reset` between scenarios for a clean demo; scenarios run back to back share excursion history.
* To test a real-time run, start the server without `--timelapse` and run the simulator without it too (~3-9 min per scenario).

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `COLDTRUST_SECRET` | `dev-secret-change-me` | HMAC key shared with the firmware. **Change it.** |
| `COLDTRUST_LLM` | `auto` | `auto` = use Claude only if `ANTHROPIC_API_KEY` is set; `on`; `off` |
| `COLDTRUST_MODEL` | `claude-opus-5-5` | Model for explanations and frame analysis |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_IDS` | unset | Comma-separated chat ids for driver and admin |
| `COLDTRUST_BUDGET_SCALE` | `1.0` | Multiply excursion budgets (e.g. `0.1` for a short live demo) |
| `COLDTRUST_RATE_C_PER_MIN` / `_STEP_C` / `_LIGHT_PCT` | `0.5` / `5` / `30` | Detection thresholds |
| `COLDTRUST_AMBIENT_C` | `31` | Outside temperature for the "door open but nothing changed" check |
| `COLDTRUST_DB` / `COLDTRUST_EVIDENCE_DIR` | `data/...` | Storage locations |
| `COLDTRUST_SILENT_AFTER_S` / `COLDTRUST_GPS_STALE_S` | `1800` / `600` | Watchdog thresholds |
| `COLDTRUST_TEMP_CROSSCHECK_C` | `3` | Allowed gap between the two thermometers |
| `COLDTRUST_TZ_OFFSET` | `-05:00` | Local offset written into EPCIS events (demo route is US Central) |

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/telemetry`, `/api/event` | Signed batch from the device (routine or emergency) |
| POST | `/api/vehicle` | Speed, zone, authorised-stop flag (from GPS / simulator) |
| POST | `/api/camera/observation` | Person-in-frame signal from the camera loop |
| POST | `/api/evidence` | Base64 JPEG frame; stored, SHA-256 hashed, optionally analysed by Claude |
| GET | `/api/state`, `/api/audit`, `/api/readings` | Dashboard data |
| POST | `/api/package/{id}/override` | Supervisor override (reason required, logged) |
| GET | `/api/custody/{token}` | Receiving-scanner check; revoked after quarantine |
| GET | `/api/epcis` | Whole trip as a GS1 EPCIS 2.0 JSON-LD document (`application/ld+json`) |
| GET | `/api/epcis/events` | EPCIS query subset: `EQ_bizStep`, `EQ_disposition`, `MATCH_epc`, `GE_eventTime`, `LT_eventTime`, `eventID` |
| GET | `/api/epcis/verify` | Recomputes every CBV 2.0 Event Hash ID; lists any record edited after capture |
| POST | `/api/audit/{id}/ack` | Acknowledge an alert (name recorded) |
| POST | `/api/audit/{id}/approve-report` | A person approves the agent's draft incident report (filing stays manual) |
| POST | `/api/package/{id}/reassign` | Request a transfer to another truck (reason required) |
| POST | `/api/vehicle/action` | `maintenance`, `offline` or `activate` |
| WS | `/ws` | Live `audit` / `audit_update` / `vehicle` events |

## Standards and safeguards

* **GS1 EPCIS 2.0:** every trip moment is recorded as a standard event with a tamper-evident hash. See PLAN.md
  section 0.4 and [docs/sample-epcis-theft.jsonld](docs/sample-epcis-theft.jsonld).
* **No default secrets (CISA Secure by Design / CPG 2.0 goal 3.A):** without `--timelapse` the backend refuses to start
  while `COLDTRUST_SECRET` is the default or shorter than 16 characters. Generate one, put it in `.env` and in the
  firmware:

  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
* **Silence is an alarm:** the watchdog reports a silent sensor node or a stale GPS position (real-time mode only).
* **Independent thermometer:** send `temp2_c` from a second sensor and the agent cross-checks the two.
* **Not internet-facing:** the backend listens on 127.0.0.1 unless you pass `--host 0.0.0.0`. Use that only on a
  trusted LAN and never port-forward it.

## Signing format (firmware must match)

`HMAC-SHA256(secret, "<device>|<seq>|<ts>|<ROUTINE|EVENT>|<trigger or ->|<t>:<temp .1f>:<hum .0f>:<light>[:<temp2 .1f>];...")`
(the `:<temp2>` part only when a second thermometer is fitted),
hex lowercase. `seq` must increase on every payload; samples must be newer than anything already stored.

## Known limits

* DHT11: ±2 °C, whole degrees, 0-50 °C. See PLAN.md section 0.1.
* HMAC with a shared secret, no TLS on the device: fine for a LAN prototype, not for production.
* Claude and Telegram paths are implemented but need credentials to exercise.
