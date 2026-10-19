# Logic check note (LED and alert rules)

Purpose: a checklist to verify that the board and dashboard behave as written below. Tick each line after you have seen it.
Written 19 Oct 2026 from `firmware/firmware.ino`, `backend/engine.py` and `backend/rules.py`.
Three items are still open decisions (marked **[Decision n]**); the note describes what is built today.

## Terms
- **Authorised stop**: inside an authorised geofence AND speed under 5 mph.
- **Route active**: "Activate Route" pressed (status "En route").
- **Bright**: hold light at or above 30 %.
- **Person**: the camera saw a person in the last 2 minutes.

## A. LEDs (check on the real board)

| # | Behaviour | How to check | Seen |
|---|---|---|---|
| G1 | Green: short blink every 5 s = a temperature reading was taken (sensor OK). No blink = DHT11 read failed | Watch the green LED for 30 s. Unplug the DHT11 data wire: the blinks stop and Serial says "DHT11 read failed" | [ ] |
| G2 | Green: double blink = backend accepted the data. Happens at each send: routine every 60 s (demo) / 15 min (real), and at once on an event | Watch after a routine send. Serial prints `Sent: ... accepted` | [ ] |
| G3 | No double blink when data is not sent or is rejected | Stop the server: no double blink; Serial prints `Send failed` | [ ] |
| R1 | Red fast blink = Wi-Fi down, readings buffered up to 1 h, resent on return | Switch the hotspot off for 2 min, then on: red blinks fast, then the dashboard shows the buffered readings | [ ] |
| R2 | Red solid = the board sees bright light (2 checks in a row) OR temperature outside 2-8 C (2 readings in a row). Goes off when it clears. Wins over the fast blink | Torch on the light sensor; or Hold chip mock to 10 C | [ ] |
| R3 | Red is NOT lit by the camera alone and does not know the location (also lit at an authorised dock) **[Decision 3]** | Camera person with the hold dark: red stays off | [ ] |
| R4 | Alarm sound, voice and Telegram come from the dashboard/backend; Telegram repeats every 10 min while unresolved | Trigger an alert; wait for the reminder (or check `notify_reason: reminder` in the audit log) | [ ] |

## B. Alert rules (backend)

Order of checks, first match wins: 1 theft, 2 spoof, 3 handling, 4 door ajar, 5 thermal, 6 data trust.

| # | Situation | Expected result | Simulator scenario (no board needed) | Seen |
|---|---|---|---|---|
| T1 | Temperature outside a package's own limits for 2 readings. Too warm / too cold | THERMAL_EXCURSION / FREEZE_RISK. Note: limits are per package (e.g. salmon 0-4, yoghurt 2-6), so 6.1 C already alerts | `compressor_fail` (warm), `freeze` (cold) | [ ] |
| T2 | Change of 5 C or more within 60 s **[Decision 1: 4 C?]** | Critical. Up = THERMAL_EXCURSION, down = FREEZE_RISK | `freeze` | [ ] |
| T3 | Slope of 0.5 C/min or more over 4 min | THERMAL_EXCURSION warning (a 2 to 4 C jump trips this) | `compressor_fail` | [ ] |
| D1 | Bright, outside an authorised stop, moving or idle (temperature not needed) | DOOR_AJAR | `door_ajar` | [ ] |
| D2 | Bright, outside a stop, route active, speed under 1 mph | THEFT_SUSPECTED | `stopped_theft` | [ ] |
| D3 | Bright at an authorised stop, temperature fine | AUTHORISED_HANDLING (logged, no alert) | `authorised_stop` | [ ] |
| D4 | Bright at an authorised stop, temperature out of range **[Decision 2]** | The temperature alert (THERMAL_EXCURSION / FREEZE_RISK), not handling | (mock both on the board at the depot) | [ ] |
| P1 | Person, not an authorised stop | SUSPICIOUS_PERSON (critical, cargo under review) | needs the camera | [ ] |
| P2 | Person + bright, not an authorised stop | THEFT_SUSPECTED (emergency, packages quarantined) | `theft` | [ ] |
| P3 | Person at an authorised stop | Logged as handling, no alert | needs the camera | [ ] |
| X1 | Flat light, two thermometers disagree, door open with no temperature change | DATA_TRUST / SPOOF_SUSPECTED | `spoof`, `sensor_disagree` | [ ] |
| X2 | Captured message resent | Rejected (replay) | `replay` | [ ] |

## C. How to run the checks

**Automated (all rules, 72 tests):**
```
.venv\Scripts\python -m pytest -q
```

**Simulator on a safe server (Telegram off, no board):**
```
.venv\Scripts\python -m backend --timelapse --reset --no-telegram --port 8010 --db data/dev.db --demo
.venv\Scripts\python -m sim.sim door_ajar --url http://127.0.0.1:8010 --timelapse
```
Run `--reset` (restart the server) between scenarios. Open http://127.0.0.1:8010 and look at the audit log and Evidence tab.

**On the real board (demo server):** `demo.bat`, then use the Hold / Light chips. Careful: this server has Telegram on, so a real
alert sends a message to the group (tagged MOCK DATA). To put the vehicle outside an authorised stop, post a position:
```
curl -X POST http://127.0.0.1:8000/api/vehicle -H "Content-Type: application/json" -d "{\"lat\":2.5,\"lon\":102.1,\"speed_mph\":0}"
```
(The depot is lat 3.0738, lon 101.5183; that position is inside it and counts as an authorised stop.)

## D. Open decisions (answer, then the code is changed to match)
1. **Step size**: keep 5 C, or 4 C so a 2 to 6 C jump counts (more false alarms: the DHT11 is only accurate to +/-2 C).
2. **Authorised stop with a bad temperature**: keep the temperature alert, or log as handling with a grace period.
3. **Red LED**: board's own light/temperature only, or also follow the backend alarm (camera, theft).
