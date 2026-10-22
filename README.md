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

## AI agents (Gemini) and the map (open source)

Three role-specific agents explain what the rules decided (they never change the status): **Thermal** (temperature,
freeze risk), **Seal** (door/light, authorised handling, data trust) and **Incident** (suspicious person, theft).
Put `GEMINI_API_KEY` in `cold-chain/.env`, run `.venv\Scripts\python -m backend.gemini_check`, restart the backend.
Each explanation in the audit log is tagged with its agent, e.g. "Gemini · Thermal agent". With no key the rule-based
text is used. Free-tier Gemini may use submitted content to improve Google products, so only reading summaries are
sent by default; camera frames need `COLDTRUST_VISION=on`.

Map: [Leaflet](https://leafletjs.com) (BSD-2, vendored in `dashboard/vendor/leaflet`) with
[OpenStreetMap](https://www.openstreetmap.org/copyright) tiles (© OpenStreetMap contributors, ODbL; needs internet,
falls back to a schematic SVG if Leaflet is missing). The route is real road geometry fetched once from the public
[OSRM](https://project-osrm.org) demo server by `tools/fetch_route.py` and stored in `backend/route_data.py`, so the
running system makes no routing calls. Re-run `.venv\Scripts\python -m tools.fetch_route` after changing stops.
OSM's tile servers and the OSRM demo server are for light use; do not point a large public deployment at them.

## AI safeguards: number check and Telegram follow-up

Every AI answer is checked before it is used: each number it quotes must appear in the data the agent was given
(rounding is fine, new figures are not). An answer with an invented number is thrown away, the rule-based text stays, and
the log shows "AI text rejected: ... not in the data".

Telegram gets **one message per alert**: the AI agent's analysis, with the rules' status line ("[CRITICAL] THERMAL_EXCURSION
(Gemini - Thermal agent)", the explanation, impact, next steps, "Status: UNDER REVIEW (SHP-...)"). The alert waits up to
`COLDTRUST_TELEGRAM_AI_WAIT_S` seconds (default 5) for the AI text. If the AI is slow, off, paused or rejected by the number
check, the rule-based text is sent instead, once. The dashboard log shows both: the rule text first, the AI text when it
arrives. No extra AI call is used. `COLDTRUST_TELEGRAM_AI=0` always sends the rule-based text at once.

## Data trust: the Seal agent's report

Every accepted batch carries a trust report (stored on its log entry and shown on the Overview as "Data trust · Seal agent").
It lists each integrity check with the evidence behind it: signature, sequence number, new readings, time window, light
jitter, door versus temperature, second thermometer and the **humidity response**. Each check is *passed*, *suspicious*,
*failed* or *not run* (and why: "second thermometer not fitted", "simulated"). The overall result is one of **no integrity
problems found**, **suspicious** or **not trusted**.

The humidity check uses humidity as a second witness: with the door open for 5 minutes, temperature and humidity should both
move. Neither moving means frozen or replayed readings (not trusted); humidity moving while the temperature stays flat means
the sensors contradict each other (suspicious); temperature moving alone is only noted, because the DHT11's humidity is
coarse. While mock readings are active the check is skipped and the report says "simulated". The threshold
(`COLDTRUST_HUM_MOVE_PCT`) has to be calibrated on the real node; until `COLDTRUST_HUM_CALIBRATED=1` the report says so.

The report is built by the rules, with no extra AI call. The Thermal and Incident agents receive it with their input ("Consulted:
Seal agent ..." appears under the log entry), and Telegram adds one "Data check" line only when the result is not clean.

## Measured benefit

`python -m tools.eval_detection` feeds the same 10 simulated scenarios to ColdTrust and to a plain threshold alarm (rings the instant
a reading leaves 2-8 C or the door opens) and writes `docs/detection_results.md`. On those scenarios: false alarms 1 vs 0; data attacks
recognised 0 of 3 vs 3 of 3; cargo already held at the first alert in 5 of 5 incidents vs 0; a slow cooling failure alerted 11 minutes
earlier (each product is judged against its own limits). On sudden events both are equally fast. `tests/test_detection.py` fails if any
of these stops being true. They are simulated scenarios, not field data.

## Agent evaluation

`python -m tools.eval_agents` plays 10 simulated scenarios (including a hidden-instruction attack) through the real engine and
asks the agents 3 times each, once for the alert analysis and once for the report wording (60 calls, about 7 minutes, paced for
the free tier). The raw answers are checked for invented numbers, actions outside the allowed list, wrong claims, unfit report
wording, and obeying the injected text. Results are in `docs/agent_eval.md`; the checks themselves are unit-tested
(`tests/test_eval.py`). Latest run: 60 of 60 calls answered, 0 invented numbers, 0 out-of-menu proposals, 0 injections obeyed.
The first run also found two real faults that are now fixed: a runaway reply when the report wording was requested together
with the proposals, and a stray control character that could corrupt a Word report. 60 answers is evidence, not a guarantee.

## Approvals: the agent proposes, an admin decides

After an alert the Incident agent can **propose** follow-up actions, and they wait in the Approvals card on the Overview.
Nothing runs until an admin approves it; a driver sees the queue read-only.

- **Fixed menu.** Four actions exist: quarantine the cargo, divert to the backup cold store, take the vehicle offline, prepare
  the incident report. The AI's answer format only allows these four values.
- **Rules limit the menu.** For each alert the rules work out which of the four make sense (for example nothing to quarantine
  when everything already is) and hand that list to the agent as `allowed_actions`. Anything else it returns is dropped.
  The agent's one-sentence reason goes through the same number check as every other AI text, and is shown, never run.
- **No AI, no problem.** If the AI is off, paused or rejected, the rules queue a plain default proposal instead
  (marked "rules" instead of the agent's name). Proposals ride in the same AI call as the analysis, so they cost nothing extra.
- **Decisions are logged.** `POST /api/approvals/{id}/decide` is admin only and takes the name from the login, not from the
  request. Approving runs the same code as the manual controls (quarantine = the package override, offline = the vehicle action;
  divert sends one Telegram notice to the driver). Every proposal and decision is an entry in the audit log.
- **Housekeeping.** A proposal nobody answers closes after 1 hour; the same one is not raised again for 10 minutes after
  someone answered it. The Telegram alert adds "Awaiting admin approval: ..." when proposals are waiting.
- The automatic quarantine on suspected theft stays as it is: the rules act at once, the queue is for what needs a person.

## Incident report from your Word template

The backend fills `templates/logistics-incident-report.docx` and saves it under `data/reports/` as
`INC-<date>-<alert number>.docx` (a later version makes `-r2`, never overwriting). Admin only: `POST /api/incidents/{id}/report`,
`GET /api/reports/{file}`. Who gets a report, and when, depends on the kind of event:

- **Suspicious person and suspected theft: prepared automatically.** About 15 seconds after the alert (so the camera has
  uploaded its pictures) a draft is made with "ColdTrust system (automatic draft)" as the reporter, at most once per 30
  minutes per kind of alert. A **Download report** button appears next to the alert in the top bar and under Evidence →
  Incident reports (admin only), and a message announces it. **New version in my name** makes another file naming the admin
  who pressed it.
- **Temperature events (too warm, too cold): only after a maintenance review supports it.** A freeze risk or a temperature
  rise on its own is not an incident report. In Maintenance, **Summarise with AI** must have run on enough history, been
  written by the AI (not the rule-based fallback), found the cooling unit "possible weakening", and cover the time of the
  event. Until then the button is replaced by the reason and a link to Maintenance, the API answers 409 with the same reason,
  and the agents do not propose "prepare report". When the review does qualify, "Prepare the incident report" is queued for the
  latest temperature event of each kind that has no report yet. It appears under the review with an **Approve incident report**
  button (and in the Approvals card); nothing is prepared until an admin approves, and the report then quotes the review.
- Other alerts (door opened outside a stop, tampered data) are prepared by pressing **Prepare incident report**, or by
  approving the proposal, and name whoever pressed it.

- **Facts come from the records, not the AI:** time and place (UTC+08:00), vehicle and route, the driver and the admin
  with their contact info (`COLDTRUST_ADMIN_CONTACT`, `COLDTRUST_DRIVER_CONTACT` in `.env`), the cargo and how long it was
  outside its limits, the actions taken (from the audit log), the Seal agent's data check, the GS1 EPCIS event IDs.
- **The reporter is whoever pressed the button**, read from their login, not typed in (an automatic draft names the system instead and says no person has reviewed it).
- **Pictures:** the evidence photos taken within 15 minutes of the alert (up to 4) are embedded in the report, each with its
  time, what was detected, file name and SHA-256. A photo whose file no longer matches the SHA-256 logged at capture is still
  shown but flagged "WARNING: the file differs...".
- **AI wording, only where safe:** when the report is prepared, one small AI call writes the description and the suspected
  cause (kept out of the alert call because together they made the Incident agent's reply run away; see the evaluation). The
  wording is number-checked, limited to a few sentences and checked for talk about its own instructions. If the call fails or
  the text is unfit, a rule-based sentence is used and the report says so.
- **Left to a person:** cargo damage ("not determined: a physical inspection is needed", with the exposure figures), vehicle
  damage, injuries and the signatures. The report says it is an AI-assisted draft that a person must review. A report made
  from mock readings carries a "DEMO DATA" note.
- **Tamper evidence:** the file's SHA-256 is written to the audit log (`REPORT_GENERATED`).
- **Editing the template:** change the wording or layout in Word, keep the `{tags}`, then run
  `python -m tools.check_report_template`. The picture slot is `{%image}` inside `{#photos}...{/photos}`; if you start again
  from your original file, `python -m tools.add_photo_slot` puts it back.

## Login and roles

Give each role a company ID and a password in `.env` (passwords at least 8 characters, IDs and passwords different from each
other) and the dashboard asks for the company ID and the code before it shows anything:

```
COLDTRUST_ADMIN_ID=ADM-1001
COLDTRUST_ADMIN_NAME=Serine Ng
COLDTRUST_ADMIN_CODE=choose-your-own-admin-code
COLDTRUST_DRIVER_ID=DRV-20418
COLDTRUST_DRIVER_NAME=Marcus Lee
COLDTRUST_DRIVER_CODE=choose-your-own-driver-code
```

The ID is not a secret (the driver's ID is the one shown on the dashboard); the code is. `NAME` is optional and is what the
audit log shows next to each action (it defaults to the ID). Typing the ID in lower case or with spaces is fine.

| | Admin | Driver |
|---|---|---|
| Look at everything (dashboard, map, logs, camera, evidence pictures, maintenance ranges) | yes | yes |
| Acknowledge alerts, activate the route | yes | yes |
| Quarantine / release / reassign packages, vehicle offline or maintenance, mock chips, the speed card, AI maintenance summary, EPCIS export, API reference | yes | no (buttons are greyed out and the server answers 403) |

- **Identity comes from the login.** The name next to every action in the audit log is the account's name, company ID and
  role ("Marcus Lee (DRV-20418, driver)"), never what a request claims. Sessions last 12 hours, are signed, HttpOnly and SameSite=Strict, and are cancelled by Sign out.
- **Guessing is limited.** 5 wrong passwords lock that address out for 5 minutes (even for the right password). Each failure is
  logged (`LOGIN_FAILED`, without the code) and the lockout sends a Telegram warning.
- **Devices keep their own key.** The board's signed messages, the camera agent and the simulator use `X-ColdTrust-Key`,
  which allows only their narrow jobs (and never makes anyone an admin).
- **Public on purpose:** the page itself (it shows the login), `/api/health`, the board's `GET /api/demo` poll, signed
  telemetry, and `GET /api/custody/{token}` for a receiving scanner.
- **No accounts set = open access,** exactly as before, with an amber "Open access: no login" chip. Set the passwords before
  sharing the dashboard with anyone.
- **Honest limits:** one account per role (a company ID and code each), not one per person; over plain HTTP the cookie is not encrypted (a
  tunnel link is https and the cookie is then marked Secure); sessions live in the signed cookie, so a restart does not end
  them but changing a code does.

## Maintenance review

The Maintenance tab has a **Maintenance review** card: choose the last 6 h, 24 h or 7 days (longer periods are summarised per
day). `GET /api/maintenance?hours=168` returns the numbers (plain arithmetic). The **Summarise with AI** button
(`POST /api/maintenance/summary`) asks the Thermal agent to put them into words: one free call per press, the same numbers
are answered from a 10-minute cache, and with the AI off, paused or out of free calls you get the rule-based summary.
Use a real (official) database for a weekly view: the demo database is wiped at every start.

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `COLDTRUST_SECRET` | `dev-secret-change-me` | HMAC key shared with the firmware. **Change it.** |
| `COLDTRUST_LLM` | `auto` | `auto` = AI agents run only if an API key is set; `on`; `off` |
| `COLDTRUST_LLM_PROVIDER` | `auto` | `auto` = Gemini when `GEMINI_API_KEY` is set (else Claude if `ANTHROPIC_API_KEY`); `gemini`; `anthropic` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | unset, `gemini-3.5-flash-lite` (15/min, 500/day free) | Key from https://aistudio.google.com/apikey; verify with `python -m backend.gemini_check` |
| `COLDTRUST_VISION` | `off` | `on` lets the Incident agent analyse camera frames (images of people leave the laptop) |
| `COLDTRUST_MODEL` | `claude-opus-5-5` | Model when the provider is `anthropic` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_IDS` | unset | Comma-separated chat ids for driver and admin |
| `COLDTRUST_BUDGET_SCALE` | `1.0` | Multiply excursion budgets (e.g. `0.1` for a short live demo) |
| `COLDTRUST_RATE_C_PER_MIN` / `_STEP_C` / `_LIGHT_PCT` | `0.5` / `5` / `30` | Detection thresholds |
| `COLDTRUST_HANDLING_GRACE_S` | `600` | At an authorised stop a warm hold with the door open is handling for this long; then it is an alert. A cooling hold is never excused |
| `COLDTRUST_AMBIENT_C` | `31` | Outside temperature for the "door open but nothing changed" check |
| `COLDTRUST_DB` / `COLDTRUST_EVIDENCE_DIR` | `data/...` | Storage locations |
| `COLDTRUST_SILENT_AFTER_S` / `COLDTRUST_GPS_STALE_S` | `1800` / `600` | Watchdog thresholds |
| `COLDTRUST_TEMP_CROSSCHECK_C` | `3` | Allowed gap between the two thermometers |
| `COLDTRUST_HUM_MOVE_PCT` / `COLDTRUST_HUM_CALIBRATED` | `3` / `0` | Smallest humidity change (%RH) that counts as "moved" in the humidity check, and whether it has been calibrated on the hardware |
| `COLDTRUST_ADMIN_CONTACT` / `COLDTRUST_DRIVER_CONTACT` | empty | Phone or email printed in the incident report |
| `COLDTRUST_REPORTS_DIR` / `COLDTRUST_REPORT_TEMPLATE` | `data/reports` / `templates/logistics-incident-report.docx` | Where reports are saved, and the template used |
| `COLDTRUST_TZ_OFFSET` | `+08:00` | Local offset written into EPCIS events (demo route is Malaysia) |

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
