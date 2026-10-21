# ColdTrust: system summary

State of the project on **18 Oct 2026** (deadline 23 Oct). This file describes what is built now; `PLAN.md` holds
the original design and schedule, `firmware/README.md` the step-by-step hardware guide.

ColdTrust watches a refrigerated truck's cargo hold (temperature, light, camera), decides what each event means
for the products inside, alerts the driver and admin on the dashboard and Telegram, and keeps a tamper-evident
record of everything that happened.

---

## 1. How the pieces fit

```
 Cargo hold                           Laptop (backend + dashboard + camera agent)            People
┌──────────────────────┐  Wi-Fi     ┌──────────────────────────────────────────────┐
│ NodeMCU (ESP8266)    │  signed    │ FastAPI backend (python -m backend)          │   Dashboard (browser)
│  DHT11  -> D2        ├──────────► │  rules engine -> verdict -> package status   ├─► siren + voice, live video
│  light  -> A0 / D5   │  JSON      │  Claude explanation (optional)               │
│  LEDs   -> D7 / D1   │            │  GS1 EPCIS records, SQLite                   ├─► Telegram group
└──────────────────────┘            │                                              │   (text + photo)
┌──────────────────────┐   USB      │ camera agent (python -m camera.camera_agent) │
│ Logitech C270        ├──────────► │  MediaPipe pose + hands -> person sightings  │
└──────────────────────┘            └──────────────────────────────────────────────┘
```

Rules decide **whether** something is wrong and change statuses. Claude (when an API key is set) only rewrites the
explanation; it never changes a status.

---

## 2. Hardware arrangement

### Parts

| Part | Job |
|---|---|
| NodeMCU 1.0 (ESP8266, ESP-12E) | Reads sensors, buffers, sends signed data over Wi-Fi |
| DHT11 module | Hold temperature + humidity (±2 °C, whole degrees, 0–50 °C) |
| SN-LIGHT-MOD light module | Hold light level: a lit hold means the door is open |
| Green LED + 220–330 Ω resistor | Heartbeat / data delivered |
| Red LED + 220–330 Ω resistor | Alarm / Wi-Fi down |
| Logitech C270 | Camera, plugged into the **laptop** |
| Breadboard, jumpers, power bank | Prototype build; power bank powers the node inside the box |

**Removed (18 Oct): the buzzer.** The alarm sound now comes from the dashboard, and Telegram messages the driver
and admin. Pin D6 is free.

### Wiring

| Part | Module pin → NodeMCU pin |
|---|---|
| DHT11 | VCC → 3V3 · DATA → **D2** · GND → GND |
| Light module | VCC → **3V3** (never 5V) · GND → GND · AO → **A0** · DO → **D5** |
| Green LED | **D7** → resistor → long leg (+) · short leg (−) → GND |
| Red LED | **D1** → resistor → long leg (+) · short leg (−) → GND |
| Camera | C270 USB → laptop |

- Leave **D3, D4, D8, RX, TX** free (boot and serial pins).
- Run 3V3 and GND to the breadboard rails and feed every module from there.
- Point the LEDs away from the light sensor, or mount them outside the box, or the sensor reads them as "door open".
- Diagram for every part: `firmware/wiring.svg`. Tick-box checklist: `firmware/README.md`.

### What the LEDs mean

| LED | Meaning |
|---|---|
| Short green blink every 5 s | Sensor read OK |
| Double green blink | Data delivered to the backend |
| Solid red | Alarm (door open or temperature out of range) |
| Fast red blink | Wi-Fi down, readings are being buffered |

---

## 3. Firmware logic (`firmware/firmware.ino`)

| What | When |
|---|---|
| Light check | every 0.1 s (the LDR is never read while the green LED is lit) |
| Temperature + humidity read | every 5 s |
| Reading saved to the buffer | every 30 s (120 slots = 1 hour without Wi-Fi) |
| Routine message (all unsent readings) | every **60 s** in `DEMO_MODE`, every 15 min otherwise |
| Event message | **immediately** on a trigger |

**Event triggers**

| Trigger | Rule on the node |
|---|---|
| `LIGHT` | light ≥ 30 % for 2 checks in a row (0.2 s), or the module's DO pin says bright; re-sent every 60 s while lit |
| `TEMP_HIGH` / `TEMP_LOW` | outside 2–8 °C for 2 reads in a row; re-sent every 2 min while out |
| `STEP` | temperature changed ≥ 5 °C within about 60 s |

**Sending**
- Every message is signed with **HMAC-SHA256** using the device key in `secrets.h` (same value as `COLDTRUST_SECRET`
  in `cold-chain/.env`). Signed text: `device|seq|ts|type|trigger|t:temp:hum:light;...`
- The sequence number starts at the boot time (epoch seconds), so it keeps increasing across reboots.
- Readings carry real UTC time from internet time (NTP); nothing is logged until the clock is synchronised.
- Large backlogs go out oldest first, up to 40 readings per message; an event's own reading goes last.
- If the backend answers (even with a rejection) those readings are not resent; if it cannot be reached they are kept.

**Self-test at power-on** (type `t` + Enter to repeat): DHT11, light AO, light DO, both LEDs, Wi-Fi, internet time
and the backend each print `[PASS]` or `[CHECK]` with the exact wire or setting to look at.

**Settings at the top of the sketch:** `DEMO_MODE`, `LIGHT_INVERT`, `DO_BRIGHT_IS_LOW`, hold limits, light alarm %,
step size. Wi-Fi, server address and device key live in `firmware/secrets.h` (git-ignored).

**Test sketches** (`firmware/tests/`): 01 DHT11, 02 light + calibration, 03 LEDs, 04 Wi-Fi + time, 05 signing check,
06 signed message to the backend.

---

## 4. Backend and agent logic (`backend/`)

### Every device message goes through

1. **Integrity gate:** wrong signature, a sequence number that is not newer, a timestamp more than 5 min off
   (real-hardware mode), or readings older than what is already stored → rejected and logged, nothing stored.
2. **Findings:** light on, temperature outside any package's limits (2 readings in a row), rate of change
   ≥ 0.5 °C/min, a 5 °C step, flat light reading (static spoof), door open with no temperature response,
   two thermometers disagreeing (optional second sensor).
3. **Verdict** from the findings plus context (speed, route, zone, camera):

| Situation | Verdict | Effect |
|---|---|---|
| Light + person seen by the camera, outside an authorised stop | **THEFT SUSPECTED** | all packages quarantined, tokens revoked |
| **Light while stopped (0 km/h) on an active route, outside an authorised stop** (new 18 Oct) | **THEFT SUSPECTED** | same |
| Light while the temperature does not react for 5 min in hot weather | SPOOF SUSPECTED | under review |
| Light at an authorised depot or dock | Authorised handling | logged only |
| Light anywhere else (e.g. driving with the door open) | DOOR AJAR | under review |
| Temperature problem, rising / falling | THERMAL EXCURSION / FREEZE RISK | per-package |
| Static or disagreeing sensor data | DATA TRUST | alert only, no status change |

"Active route" means **Activate Route** was pressed (route status "En route"); doing so also marks the truck as
having left the depot.

4. **Per-package risk (0–100):** time outside its own limits against its excursion budget, peak deviation, rate,
   light exposure, data trust; tamper verdicts set a floor. Status: Normal → Under review → **Quarantined - do not
   accept**. Only a supervisor override (with a reason) lifts a quarantine and re-issues the custody token.
5. **Notifications** go out only when a problem is **new**, **escalates**, **changes a package's status**, or is
   still unresolved after **10 min**; an all-clear resets this.

### Camera sightings (`/api/camera/observation`)

| Where the truck is | Result |
|---|---|
| Authorised depot or dock | Logged as handling |
| Anywhere else | **SUSPICIOUS PERSON**: alert, cargo under review |
| Anywhere else **and** the hold is lit | **THEFT SUSPECTED**: everything quarantined |

The same person is not logged twice unless a new cue appears (reaching, crouching, lingering…).

### Other backend behaviour

- **Watchdog** (real-time mode): alerts when the node stops sending (30 min) or the GPS position goes stale (10 min),
  and again when it recovers.
- **Backup cold store** (Seremban) named in the advice when cooling fails or the node goes silent.
- **GS1 EPCIS 2.0 records** for departure, every reading batch, holds, quarantines and releases, each with a
  tamper-evident hash ID that matches GS1's own sandbox; export from the profile menu or `/api/epcis`.
- **Security:** refuses to start on real hardware with the default device key; camera uploads must carry the device
  key; the backend listens only on the laptop unless started with `--host 0.0.0.0`.
- **Telegram:** text alerts; person, theft and door alerts include the latest annotated camera picture.

---

## 5. Dashboard (`dashboard/`, served at the backend's address)

**Top bar:** search (packages, clients, destinations), Delivery / Logistics toggle, live hold temperature, light,
cleared count and connection, **alarm sound** switch (speaker icon), notifications bell, profile menu (your name,
settings read from the backend, theme, EPCIS export, API docs).

**Alert ticker** under the top bar, each alert with **Acknowledge**. An alert stays active while its problem is
current; theft stays until someone acknowledges it.

**Vehicle header:** status, **Maintenance**, **Take Offline**, **Activate Route**.

| Tab | Contents |
|---|---|
| **Overview** | Map (route, geofences, truck), telemetry strip (speed and fuel gauges, driver, progress, alerts), **Vehicle speed card** (new: gauge + Stop / 50 / 90 km/h demo buttons + the stopped-on-route rule), cargo climate gauges, **live camera** with the pose overlay, alert delivery status, agent reasoning and audit log |
| **Cargo** | Weight / volume / cleared bars, hold grid by loading order, package list and detail: limits, excursion budget, risk, custody token, simulated receiving scan, reassign to another truck, supervisor override |
| **Maintenance** | **Maintenance review** card on top: pick 6 h / 24 h / 7 days (shown per day), time in range, peak, door openings, average recovery and its trend, early-warning list, and a **Summarise with AI** button (Thermal agent, one free call per press, cached 10 min, rule-based fallback). Below it: the 6-hour charts, time to limit, recovery after each door opening |
| **Route** | Larger map, stops and zones marked passed / here / ahead |
| **Evidence** (new 18 Oct) | Simple list: **picture, time, location, what was detected**; click a picture for full size |

**Alarm sound (new 18 Oct):** every alert plays a siren followed by the spoken message; theft repeats every 20 s
(up to 5 times) until acknowledged. Browsers allow sound only after the first click on the page; a banner asks for
it if an alert arrives earlier. The setting is remembered.

Updates arrive live over a WebSocket; the page refreshes every 10 s if the connection drops.

---

## 6. Camera (`camera/`)

- `cam_test.py`: checks the C270 (resolution, frame rate, snapshot).
- `camera_agent.py`: MediaPipe pose + hand landmarks (OpenCV HOG as a fallback), draws the green skeleton, face
  points, hand lines and a label box (red **SUSPICIOUS PERSON** / **THEFT SUSPECTED** while an alert is active).
  Streams about 5 frames per second to the dashboard, reports sightings and cues to the backend, and saves **one
  annotated picture per incident** to the Evidence list (with time and location).
- Reports "a person" and pose cues only, never who. First run downloads two model files (5.8 MB + 7.8 MB).
- Test without the camera: `--source photo.jpg`.

---

## 7. Change log

| Date | Change |
|---|---|
| 14 Oct | Backend, rules engine, per-package risk, simulator, tests |
| 15 Oct | Telegram alerts (change-based, not every ping); GS1 EPCIS records; CISA safeguards (no default key, watchdog, second thermometer, backup cold store); dashboard on live data |
| 16 Oct | Hardware test sketches, combined firmware with self-test, wiring diagram; camera person detection with pose overlay, live video on the dashboard, suspicious-person and theft rules, Telegram photo alerts |
| 18 Oct | **Evidence** is now a simple list (time, location, picture); report form removed |
| 18 Oct | **Vehicle speed card** and the **stopped-on-route theft rule**; Activate Route marks departure |
| 18 Oct | **Buzzer removed** from hardware, firmware, diagram and guides; **dashboard siren + voice** on every alert, Telegram in real time |
| 18 Oct | **Mock temperature** (the demo board's DHT11 cannot show room temperature): Hold chip → checkbox + slider; unticked uses the sensor; always labelled as mock |

---

## 8. How to run

All commands from the `cold-chain` folder.

| Purpose | Command |
|---|---|
| Real hardware | `.venv\Scripts\python -m backend --host 0.0.0.0 --db data/hardware.db --reset` |
| Same, without Telegram while testing | add `--no-telegram` |
| Simulator demo (instant) | `.venv\Scripts\python -m backend --timelapse --reset --no-telegram`, then `.venv\Scripts\python -m sim.sim stopped_theft --timelapse` |
| Camera | `.venv\Scripts\python -m camera.camera_agent --url http://127.0.0.1:8000 --show` |
| Telegram setup | `.venv\Scripts\python -m backend.telegram_setup chats` / `test` |
| Tests | `.venv\Scripts\python -m pytest -q` |

Simulator scenarios: normal, door_ajar, theft, authorised_stop, compressor_fail, freeze, replay, spoof,
sensor_disagree, stopped_theft.

Laptop address on the current Wi-Fi: `http://192.168.100.7:8000`. It changes on another network (e.g. a phone
hotspot); check `ipconfig`. The board can only reach it if Windows allows Python on that network (Private network +
firewall "Allow an app").

---

## 8b. Mock temperature and light (demo aid)

The demo board's DHT11 only reads 1–2 °C, and you may want to trigger the door rules without a torch. The dashboard can
therefore **mock the temperature and the light**, and it is never hidden:

- Start the backend with **`--demo`** (off by default).
- Click the **Hold** chip (temperature) or the **Light** chip in the top bar. Each opens a **Mock ...** checkbox:
  - **Unticked:** the node reports its real sensor.
  - **Ticked:** a slider appears (temperature 0–40 °C, light 0–100 %) and the node reports the slider value within about 2 s.
- The node polls `/api/demo` every 2 s (`ALLOW_MOCK = true` in `firmware.ino`) and sends immediately when a setting changes.
  Mocked light carries a tiny jitter so it never looks like a frozen sensor.
- Mock readings are flagged `demo` + `demo_sensors`: a banner says which sensor is mocked (and when it is waiting for the
  node), a **mock** tag appears on that chip, the log shows `MOCK_TEMP` / `MOCK_LIGHT`, EPCIS carries `ct:simulated`.
- Mocked light drives the real rules: e.g. 70 % light while stopped on an active route = THEFT SUSPECTED.
- Set `ALLOW_MOCK = false` and run without `--demo` for strictly real measurements.

## 9. Status and known limits

| Item | Status |
|---|---|
| Backend, rules, dashboard, camera agent | Working; 70 automated tests pass |
| Firmware message format | Accepted by the backend in real-hardware mode with the real key |
| Firmware on the board | **Not yet confirmed**: needs ArduinoJson 7 and the Adafruit DHT library installed, then a full run |
| Board → laptop connection | Blocked until the Wi-Fi is set to Private and Python is allowed (see section 8) |
| Telegram photo alerts | Built and tested offline; not yet sent to the group |
| AI agents (Thermal, Seal, Incident) | Built on the Gemini API (Claude optional); mocked-HTTP tests pass; **not yet run against live Gemini**: add `GEMINI_API_KEY` and run `python -m backend.gemini_check` |
| Map | Leaflet + OpenStreetMap tiles with an OSRM road route; verified in the browser |

- DHT11 is ±2 °C with whole-degree readings; on a desk at room temperature the 2–8 °C alarm will fire. Use a cooler
  box with ice packs for tests and the demo.
- The node has no GPS, so speed is set from the dashboard for the demo.
- A fully dark hold shows the camera nothing until the door opens.

---

## 10. File map

| Path | What |
|---|---|
| `backend/agent.py` | API, WebSocket, camera stream, Telegram delivery |
| `backend/engine.py` | Integrity gate, verdicts, package risk and status, watchdog, camera sightings, EPCIS hooks |
| `backend/rules.py` | Pure detection and trust checks, signing |
| `backend/epcis.py` | GS1 EPCIS 2.0 records and hash IDs |
| `backend/notify.py`, `telegram_setup.py` | Telegram messages and photos; setup helper |
| `backend/seed.py` | Truck, packages, route, geofences, backup cold store |
| `backend/llm.py`, `gemini_check.py` | The three AI agents (Gemini or Claude), frame analysis, key check, shared free-tier limit guard |
| `backend/maintenance.py` | Maintenance review: recovery after door openings, per-day summary, early-warning indicators, rule-based fallback |
| `tools/fetch_route.py`, `backend/route_data.py` | OSRM road route for the map (fetched once) |
| `dashboard/` | `index.html`, `app.js`, `styles.css` (`mockup.html` = the original design mockup) |
| `camera/` | `cam_test.py`, `camera_agent.py`, `pose.py` |
| `firmware/` | `firmware.ino`, `secrets.example.h`, `wiring.svg`, `README.md`, `tests/01–06` |
| `sim/sim.py` | Simulator and demo scenarios |
| `tests/` | Automated tests |
| `PLAN.md`, `README.md`, `docs/` | Design and evaluation, setup, sample EPCIS export |
