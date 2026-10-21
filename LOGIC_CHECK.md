# Logic note: LEDs and alert rules (with the new handling rule)

Purpose: the full logic in one place, plus a checklist to verify it. Tick each line after you have seen it.
Updated 19 Oct 2026 from `firmware/firmware.ino`, `backend/engine.py` and `backend/rules.py`.
Handling is the only rule changed since the earlier note. The 5 C step is unchanged and the red LED is the board's own again.

## Terms
- **Authorised stop**: inside an authorised geofence AND speed under 8 km/h.
- **Route active**: "Activate Route" pressed, OR any speed above 0 was set while the route was idle (status "En route"). Stopping (speed 0) does not end it; Offline and Maintenance are not overridden.
- **Bright**: hold light at or above 30 %.
- **Person**: the camera saw a person in the last 2 minutes.
- **Out of limits**: the hold temperature is outside the own limits of at least one package, for 2 readings in a row
  (hold limits 2 to 8 C are the tightest; salmon 0 to 4, yoghurt 2 to 6, and so on).
- **Warming / cooling**: cooling = a fall of 5 C or more within 60 s, or a reading below a package's minimum.

## 1. LEDs (on the board, unchanged)

**Green**
1. Short blink every 5 s: a temperature reading was taken (sensor OK). No blink = the DHT11 read failed.
2. Double blink: the backend accepted the data. This happens at each send: routine every 60 s in demo (15 min real), and at once on an event. No double blink = not sent or rejected.

**Red**
1. Fast blink: Wi-Fi down. Readings are kept for up to 1 hour and sent when Wi-Fi returns.
2. Solid: the board's OWN alarm, either of these:
   - the hold is bright (2 checks in a row);
   - the temperature is outside 2 to 8 C (2 readings in a row).

   It goes off when the condition clears, and it wins over the fast blink. The board does not know the location, so it is
   also lit at an authorised dock, and it is not lit by camera-only alerts. The siren, spoken message and Telegram
   (repeat every 10 min while unresolved) come from the backend and dashboard.

## 2. Alert rules (backend)

Order of checks, first match wins: **1 theft, 2 spoof, 3 handling, 4 door ajar, 5 thermal, 6 data trust**.

**Temperature**
1. Out of limits for 2 readings: too warm = THERMAL_EXCURSION, too cold = FREEZE_RISK.
2. Sudden change: 5 C or more within 60 s is critical (up = THERMAL_EXCURSION, down = FREEZE_RISK). A slope of 0.5 C per minute or more over 4 min is a THERMAL_EXCURSION warning.

**Door and light (the hold is bright)**
3. Outside an authorised stop, moving or idle, temperature not needed: **DOOR_AJAR**.
4. Outside an authorised stop, route active, speed under 2 km/h: **THEFT_SUSPECTED**.
5. At an authorised stop: **handling rules below**.

**Person (camera)**
6. Not an authorised stop: **SUSPICIOUS_PERSON** (critical, cargo under review).
7. Not an authorised stop and bright: **THEFT_SUSPECTED** (emergency, packages quarantined).
8. Authorised stop: logged as handling, no alert.

**Data trust**
9. Flat light, two thermometers disagreeing, or door open for minutes with no temperature change: DATA_TRUST / SPOOF_SUSPECTED (admin only).

## 3. Handling at an authorised stop (the changed part)

Bright light at an authorised stop. What the backend decides depends on the temperature:

| Temperature at that moment | Result | Message / siren | Packages |
|---|---|---|---|
| Fine | AUTHORISED_HANDLING | none | unchanged |
| Warming, out of limits, for under 10 min | AUTHORISED_HANDLING (note says "an alert follows if it lasts") | none | marked UNDER REVIEW, budget keeps counting |
| Warming, out of limits, for 10 min or more | THERMAL_EXCURSION | yes (Telegram, siren, voice) | UNDER REVIEW or QUARANTINED by risk and budget |
| Cooling (fell 5 C or more, or below a package's minimum) | FREEZE_RISK at once | yes | as above |
| Flat temperature for minutes with the door open | SPOOF_SUSPECTED (spoof is checked before handling) | admin only | review |

Why: an open dock door lets warm air in, so a short rise is expected. An open door never cools the hold, so a falling
temperature is a real problem. Nothing is hidden: the packages are still tracked, only the message is held back.
The 10 minutes is `COLDTRUST_HANDLING_GRACE_S` (default 600). It is counted from when the hold first went out of limits.

## 4. Checklist: LEDs (real board)

| # | Behaviour | How to check | Seen |
|---|---|---|---|
| G1 | Short green blink every 5 s | Watch 30 s. Unplug the DHT11 data wire: blinks stop, Serial says "DHT11 read failed" | [ ] |
| G2 | Double green blink after a send | Watch after a routine send. Serial: `Sent: ... accepted` | [ ] |
| G3 | No double blink when the server is stopped or rejects | Stop the server: Serial `Send failed` | [ ] |
| R1 | Fast red blink when Wi-Fi is down; buffered readings arrive later | Hotspot off 2 min, then on | [ ] |
| R2 | Solid red for bright light or temperature outside 2-8 C; off when clear | Torch on the sensor; Hold chip mock to 10 C | [ ] |
| R3 | Red stays off for a camera-only alert | Person on camera, hold dark: no red LED (the dashboard still alerts) | [ ] |

## 5. Checklist: alert rules (simulator needs no board)

| # | Situation | Expected result | Scenario | Seen |
|---|---|---|---|---|
| T1 | Out of limits, warm / cold | THERMAL_EXCURSION / FREEZE_RISK | `compressor_fail` / `freeze` | [ ] |
| T2 | Change of 5 C or more in 60 s (a 4 C jump is NOT a step) | Critical alert | `freeze` | [ ] |
| T3 | Slope 0.5 C/min or more over 4 min | THERMAL_EXCURSION warning | `compressor_fail` | [ ] |
| D1 | Bright, not an authorised stop | DOOR_AJAR | `door_ajar` | [ ] |
| D2 | Bright, not a stop, route active, speed under 2 km/h | THEFT_SUSPECTED | `stopped_theft` | [ ] |
| D3 | Bright at an authorised stop, temperature fine | AUTHORISED_HANDLING | `authorised_stop` | [ ] |
| H1 | Bright at a stop, hold warming for under 10 min | AUTHORISED_HANDLING, no message, packages under review | (board: mock temp 11 and light at the depot) | [ ] |
| H2 | Same, still warming after 10 min | THERMAL_EXCURSION + Telegram | (wait 10 min) | [ ] |
| H3 | Bright at a stop, hold cooling | FREEZE_RISK at once | (mock temp below 0 and light at the depot) | [ ] |
| P1 | Person, not an authorised stop | SUSPICIOUS_PERSON | needs the camera | [ ] |
| P2 | Person + bright, not an authorised stop | THEFT_SUSPECTED | `theft` | [ ] |
| P3 | Person at an authorised stop | handling, no alert | needs the camera | [ ] |
| X1 | Flat light / disagreeing thermometers / door open, flat temperature | DATA_TRUST / SPOOF_SUSPECTED | `spoof`, `sensor_disagree` | [ ] |
| X2 | Captured message resent | Rejected (replay) | `replay` | [ ] |

## 6. How to run the checks

Automated (all rules): `.venv\Scripts\python -m pytest -q`

Simulator on a safe server (Telegram off, no board):
```
.venv\Scripts\python -m backend --timelapse --reset --no-telegram --port 8010 --db data/dev.db --demo
.venv\Scripts\python -m sim.sim door_ajar --url http://127.0.0.1:8010 --timelapse
```
Restart the server (`--reset`) between scenarios.

On the real board (demo server, Telegram ON): `demo.bat`, then the Hold and Light chips. A real alert messages the group.
To put the truck outside a stop:
```
curl -X POST http://127.0.0.1:8000/api/vehicle -H "Content-Type: application/json" -d "{\"lat\":2.5,\"lon\":102.1,\"speed_kmh\":0}"
```
(The depot is lat 3.0738, lon 101.5183; that position counts as an authorised stop.)
