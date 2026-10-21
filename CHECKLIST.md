# ColdTrust master checklist (hardware and software)

Updated 19 Oct 2026. Tick the last column as you verify each line. Rule details are in `LOGIC_CHECK.md`.

**Status words**
- **Auto**: covered by the automated tests (`113 passed` on 19 Oct).
- **Seen**: I watched it work live on 19 Oct.
- **You**: only you can check it (board, LEDs, sound, phone, group chat).

Run all automated checks any time: `.venv\Scripts\python -m pytest -q`

---

## A. Hardware (board, sensors, camera)

| # | Check | How | Expected | Status | Done |
|---|---|---|---|---|---|
| A1 | Wiring matches the diagram | `firmware/wiring.svg`; DHT11 D2, light AO A0 / DO D5, green D7, red D1, all VCC 3V3 | Self-test in Serial Monitor: every part `[PASS]` | You | [ ] |
| A2 | **Re-flash the latest firmware** (light-mock fix, 1024 ADC clamp, refused readings dropped) | Arduino IDE, upload `firmware/firmware.ino` | Serial prints the self-test; no compile error | You (needed) | [ ] |
| A3 | Board reaches the backend | Start `demo.bat`; power the board | Dashboard shows **Live**, readings every 30 s; Serial `Sent: ... accepted` | Seen | [ ] |
| A4 | Green LED: short blink every 5 s (reading taken) | Watch 30 s; unplug the DHT11 data wire | Blinks stop, Serial `DHT11 read failed` | You | [ ] |
| A5 | Green LED: double blink after each send | Watch after a routine send (every 60 s in demo) | Double blink; none when the server is stopped | You | [ ] |
| A6 | Red LED: fast blink when Wi-Fi is down | Hotspot off 2 min, then on | Fast red blink; buffered readings arrive afterwards | You | [ ] |
| A7 | Red LED: solid for bright light or temperature outside 2-8 C (board's own alarm) | Torch on the light sensor; ice pack or Hold chip mock to 10 C | Solid red, off when it clears. Not lit by camera-only alerts | You | [ ] |
| A8 | Light sensor direction | Cover it, then torch it | Covered under about 10 %, torch over about 50 % (set `LIGHT_INVERT` if reversed) | You | [ ] |
| A9 | DHT11 accuracy | Compare with a room thermometer | Known issue: reads about 1-2 C low and whole degrees only. Use the mock for demos | You | [ ] |
| A10 | Mock temperature and light follow the dashboard within about 3 s | Hold chip: tick Mock, move slider; Light chip the same | Chip shows MOCK; unticking returns real readings | Seen | [ ] |
| A11 | Light mock near 0 % no longer breaks sending | Mock light 0 % then 1-3 % for 2 minutes | No `HTTP 422`; readings keep arriving (the backend side is tested; the firmware fix needs A2) | You | [ ] |
| A12 | Camera: Logitech on `--source 0`, one agent only | `.\switch.ps1 status` | Camera running (camera 0); video steady, no flicker | Seen | [ ] |
| A13 | Camera detects a person and shows the skeleton | Stand in front of the camera | Cargo camera card: PERSON x1, cues such as "hands raised" | Seen | [ ] |
| A14 | No second program holds the camera | Close NVIDIA Broadcast / OBS / video apps | Agent starts; no "No image" message | You | [ ] |

## B. Alert logic (backend)

| # | Check | How | Expected | Status | Done |
|---|---|---|---|---|---|
| B1 | Out of limits warm / cold | Scenarios `compressor_fail` / `freeze` | THERMAL_EXCURSION / FREEZE_RISK | Auto + Seen | [ ] |
| B2 | Sudden change of 5 C or more in 60 s (4 C is not a step) | Scenario `freeze` | Critical alert | Auto | [ ] |
| B3 | Door ajar: bright outside an authorised stop | Press 90 km/h, then Light chip mock 70 % | DOOR_AJAR | Auto + Seen | [ ] |
| B4 | Stopped on an active route + bright | Press 90 then Stop, then Light mock | THEFT_SUSPECTED | Auto | [ ] |
| B5 | Handling, temperature fine | At the depot, Light mock 70 % | AUTHORISED_HANDLING, no message | Auto + Seen | [ ] |
| B6 | Handling, hold warming for under 10 min | At the depot, Light mock plus Hold mock 11 C | AUTHORISED_HANDLING, no message, packages under review | Auto | [ ] |
| B7 | Same, still warming after 10 min | Wait 10 min | THERMAL_EXCURSION + Telegram | Auto | [ ] |
| B8 | Handling, hold cooling | At the depot, Light mock plus Hold mock below 0 C | FREEZE_RISK at once | Auto | [ ] |
| B9 | Person outside a stop / person with light | Camera + truck away from the depot (+ Light mock) | SUSPICIOUS_PERSON / THEFT_SUSPECTED, packages quarantined | Auto + Seen | [ ] |
| B10 | Person at an authorised stop | Camera at the depot | Logged as handling, no alert | Auto + Seen | [ ] |
| B11 | Spoof / data trust / replay | Scenarios `spoof`, `sensor_disagree`, `replay` | SPOOF_SUSPECTED / DATA_TRUST (admin only) / rejected | Auto | [ ] |
| B12 | Order of checks: theft > spoof > handling > door ajar > thermal > data trust | See `LOGIC_CHECK.md` section 2 | First match wins | Auto | [ ] |
| B13 | **Any speed above 0 activates the route** | Press 50 or 90 km/h from Idle | "On route", left the depot, log line "Route activated" | Auto + Seen | [ ] |
| B14 | Stop (0) keeps the route active; Offline / Maintenance are not overridden | Press 90, Stop; Take Offline then 60 | Stays En route; stays Offline | Auto | [ ] |
| B15 | Authorised stop needs speed under 8 km/h; "standing still" is under 2 km/h | Set 7 and 9 km/h at the depot | Stop only at 7 | Auto | [ ] |
| B16 | Quarantine revokes the custody token | After a theft, open `/api/custody/<token>` | `valid: false` | Auto | [ ] |
| B17 | Node silent / GPS stale watchdog | Power the board off for 30 min | NODE_SILENT alert, then NODE_RESTORED | Auto | [ ] |
| B18 | GS1 EPCIS export verifies | Menu > Export trip; `/api/epcis/verify` | `all_valid: true` | Auto | [ ] |

## C. Dashboard

| # | Check | How | Expected | Status | Done |
|---|---|---|---|---|---|
| C1 | Map: real road route Shah Alam to Johor Bahru, geofences, truck, alert label | Overview and Route tabs | OpenStreetMap tiles, route fits the view, truck moves with position | Seen | [ ] |
| C2 | Map in dark and light theme; behaviour without internet | Toggle theme; unplug internet | Tiles darkened in dark theme. Offline, the route, stops and truck still draw but the map tiles stay blank | Seen (dark theme) / You (offline) | [ ] |
| C3 | Metric units everywhere | Overview, Cargo, speed card | km/h, km left, litres, kg, m3 (13,010 / 20,000 kg, 67.8 / 68 m3) | Seen + Auto | [ ] |
| C4 | Speed card buttons Stop / 50 / 90 km/h | Click each | Gauge and "On route" change | Seen | [ ] |
| C5 | Mock chips and the MOCK banner | Hold and Light chips | "MOCK DATA" banner while mocked, gone when real | Seen | [ ] |
| C6 | Alarm sound and spoken message on an alert | Trigger an alert with the speaker icon on | Siren plus voice, repeats until acknowledged | You | [ ] |
| C7 | Evidence tab: time, location, picture, what was detected | After a person/theft alert | One row per alert with the camera picture and SHA-256 | Seen | [ ] |
| C8 | Audit log tags each AI explanation | Open the agent log | "Gemini - Seal agent" etc. | Seen | [ ] |
| C9 | Delivery card: Telegram, alarm, AI usage | Overview, bottom | "AI agents - Gemini (gemini-3.5-flash-lite)  N/400 today" | Seen | [ ] |
| C10 | Maintenance review card, range 6 h / 24 h / 7 days | Maintenance tab | KPIs, per-day list, indicators; per-day rows only for 24 h and 7 days | Seen + Auto | [ ] |
| C11 | Phone width | Narrow the window | No horizontal scroll | You | [ ] |

## D. AI agents and the free tier

| # | Check | How | Expected | Status | Done |
|---|---|---|---|---|---|
| D1 | Key and model work | `.venv\Scripts\python -m backend.gemini_check` | Three `[PASS]` lines; model `gemini-3.5-flash-lite`, 15/min and 500/day free | Seen | [ ] |
| D2 | Right agent for the right alert | Scenarios `door_ajar`, `compressor_fail`, `theft` | Seal / Thermal / Incident agent tags | Seen + Auto | [ ] |
| D3 | AI never changes a status | Compare status before and after the AI text arrives | Same verdict; only the wording changes | Auto | [ ] |
| D4 | No wrong claims | Read each AI text against the alert numbers | Numbers match; a security quarantine says the temperature is fine | Seen (one wording fix) | [ ] |
| D5 | Free-tier guard: 9 per minute, 400 per day, shared by every program | Press alerts in a burst; run `gemini_check` at the same time | Never more than 9 calls in a minute; counter in `data/gemini_usage.json` | Auto + Seen (race test) | [ ] |
| D6 | After a 429 all programs pause for 10 min | (simulated in tests) | Rule-based text meanwhile | Auto | [ ] |
| D7 | Billing stays off | AI Studio rate-limit page | "Free tier" badge; never click "Set up billing" | You | [ ] |
| D8 | Camera pictures not sent to Gemini | Dashboard shows "text only" | `COLDTRUST_VISION` stays off | Seen | [ ] |
| D9 | Maintenance **Summarise with AI**: one call per press, cached 10 min, shows calls left | Press it twice | Second press says "same numbers, no new AI call" | Seen (1 press) + Auto | [ ] |
| D10 | Fallbacks: no key, paused, too little history, bad answer | Temporarily empty the key; use the 6 h range on a new database | Rule-based summary with a note saying why | Auto | [ ] |
| D12 | Number check: AI text quoting a number not in the data is rejected | Read the log after an alert | Line `ai check: AI text rejected: ...`; rule text kept. Live run 20 Oct: 4 accepted, 0 rejected | Auto + Seen | [ ] |
| D13 | Telegram: ONE message per alert, the AI analysis (rule text only if the AI is late or unavailable) | Trigger an alert on `demo.bat` | One message within about 2-5 s: "[CRITICAL] KIND (Gemini - ... agent)", explanation, next steps, status. The dashboard log shows the rule text first, then the AI text | Auto | [ ] |
| D11 | Repeated status changes or reminders within 60 s keep the rule text | Burst of events | Fewer AI calls than alerts | Auto | [ ] |

## E. Telegram and notifications

| # | Check | How | Expected | Status | Done |
|---|---|---|---|---|---|
| E1 | Alert reaches the group | Trigger an alert on the demo server (Telegram is ON in `demo.bat`) | Message with the AI wording; mocked values tagged `[MOCK DATA: ...]` | Seen | [ ] |
| E2 | Person / door / theft alerts carry the camera photo | Person alert with the camera live | Photo attached | You | [ ] |
| E3 | Repeats: new, escalated, status change, every 10 min | Leave an alert unresolved | One message per reason, reminder at 10 min | Auto | [ ] |
| E4 | Keep demos quiet | Empty `TELEGRAM_BOT_TOKEN` in `.env` | No group messages | You | [ ] |

## F. Operations (running it on the day)

| # | Check | How | Expected | Status | Done |
|---|---|---|---|---|---|
| F1 | Demo start | Double-click `demo.bat` | Demo server + camera windows; mock chips on; database wiped | Seen | [ ] |
| F2 | Official start (real readings only, database kept) | Double-click `official.bat` | Mock refused (HTTP 403); `data/official.db` kept | Seen | [ ] |
| F3 | Switch both ways | Run `demo.bat`, then `official.bat`, then `demo.bat` | Old server stopped first; no port clash | Seen | [ ] |
| F4 | Stop everything | Double-click `stop.bat` | No server, camera or window left | Seen | [ ] |
| F5 | Old databases upgrade to metric on start | Start `official.bat` once | Cargo shows kg and m3, no NaN | Auto (tested on copies) | [ ] |
| F6 | Secrets are not pushed | `git status` before committing | `.env`, `secrets.h`, `data/` never staged | Seen (checked) | [ ] |

## H. Login and roles

| # | Check | How | Expected | Status | Done |
|---|---|---|---|---|---|
| H1 | The company IDs and codes are in `.env` (`COLDTRUST_ADMIN_ID` / `_CODE`, `COLDTRUST_DRIVER_ID` / `_CODE`); run `demo.bat` | Open the dashboard | A sign-in screen asking for Company ID and Access code, nothing visible behind it | Seen (test server) | [ ] |
| H2 | Wrong code, or a wrong ID | Type either wrong | "Wrong company ID or access code" (the same message for both), code cleared, ID kept | Seen + Auto | [ ] |
| H3 | Driver sign-in | Driver company ID + driver code | Chip "Name - Driver", blue banner, Maintenance / Take Offline / override / reassign / speed / mock chips / AI summary greyed out, Activate Route and Acknowledge work | Seen | [ ] |
| H4 | Driver cannot get around the greyed buttons | (tested by calling the API directly) | 403 "admin only" for override, offline, mock, speed, AI summary, EPCIS, API docs | Seen + Auto | [ ] |
| H5 | Admin sign-in | Admin company ID + admin code | Chip "Name - Admin", every control works; the profile menu is just name, Sign out, alarm sound and theme | Seen | [ ] |
| H6 | The audit log shows the signed-in name | Driver acknowledges an alert, admin overrides a package | "Marcus Lee (DRV-20418, driver)" / "Serine Ng (ADM-1001, admin)", even if the request claimed another name | Seen + Auto | [ ] |
| H7 | Lockout | 5 wrong codes | Locked 5 min, `LOGIN_FAILED` entries, a Telegram warning | Auto | [ ] |
| H8 | Sessions: tampered, expired (12 h), signed-out cookies are refused; the live feed needs a session | | 401 | Auto | [ ] |
| H9 | Camera agent, simulator and board still work with login on | Run `demo.bat` with codes set | Camera live, board accepted, `switch.ps1 status` works | Auto (not run live with the board) | [ ] |
| H10 | No codes set = open access chip | Empty the codes | Amber "Open access: no login" | Auto | [ ] |

## G. Still to do before the 23 Oct presentation

| # | Item | Done |
|---|---|---|
| G1 | Measure latency: reading to dashboard, alert to Telegram, with an ice pack and a torch (write the numbers down) | [ ] |
| G2 | Rehearse the 5-minute running order twice; decide if Telegram is on during the talk | [ ] |
| G3 | Record a backup screen video of one good run | [ ] |
| G4 | Slides, including the honest limits (DHT11 accuracy, Wi-Fi not cellular, prototype crypto, sample data) | [ ] |
| G5 | Push the latest code to GitHub (code freeze Thursday 22 Oct) | [ ] |
| G6 | Re-flash the board with the latest firmware (A2) | [ ] |
