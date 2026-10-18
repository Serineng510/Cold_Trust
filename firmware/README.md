# ColdTrust hardware: build and test, one part at a time

Wiring diagram for every part: [wiring.svg](wiring.svg) (open it in a browser).

Test each part on its own first, then flash the combined `firmware.ino` (this folder). It reads
`secrets.h` in this folder: fill in your Wi-Fi and laptop IP; the device key is already there.

## Wiring (NodeMCU 1.0 / ESP-12E)

Power every module from **3V3**, not 5V: the NodeMCU's analog pin (A0) must not see more than 3.3 V.

| Part | Module pin | NodeMCU pin |
|---|---|---|
| DHT11 | VCC / GND / DATA | 3V3 / GND / **D2** |
| Light sensor (SN-LIGHT-MOD) | VCC / GND / AO / DO | 3V3 / GND / **A0** / **D5** |
| Green LED (heartbeat, sent OK) | + via 220–330 Ω resistor, − to GND | **D7** |
| Red LED (alarm, Wi-Fi down) | + via 220–330 Ω resistor, − to GND | **D1** |
| Logitech C270 | USB | the **laptop**, not the NodeMCU |

Do not use **D3, D4, D8** for these parts: they decide how the board boots. **D4** also drives the
on-board blue LED.

Mount the LEDs on the outside of the box, or facing away from the light sensor. The sensor's job is to
notice light, so an LED next to it reads as "door open".

## Wiring check (tick each wire before plugging in USB)

**DHT11**
- [ ] VCC → 3V3
- [ ] DATA → D2
- [ ] GND → GND

**Light sensor (SN-LIGHT-MOD)**
- [ ] VCC → 3V3 (not 5V)
- [ ] GND → GND
- [ ] AO → A0
- [ ] DO → D5

**Green LED**
- [ ] D7 → 220–330 Ω resistor → LED long leg (+)
- [ ] LED short leg (−) → GND

**Red LED**
- [ ] D1 → 220–330 Ω resistor → LED long leg (+)
- [ ] LED short leg (−) → GND

**Board**
- [ ] One wire 3V3 → breadboard + rail, one wire GND → breadboard − rail
- [ ] Nothing on D3, D4, D8, RX, TX
- [ ] LEDs face away from the light sensor (or sit outside the box)

**Camera:** C270 USB → laptop (not the NodeMCU).

**No buzzer:** the alarm sounds on the dashboard (siren + spoken message) and Telegram messages the driver and admin.

After flashing `firmware.ino`, the Serial Monitor prints a self-test: `[PASS]` per part, or `[CHECK]` with the
exact wire to look at. Type `t` + Enter to run it again after fixing a wire.

## Arduino IDE setup (once)

1. File → Preferences → Additional boards manager URLs:
   `http://arduino.esp8266.com/stable/package_esp8266com_index.json`
2. Tools → Board → Boards Manager → install **esp8266** (by ESP8266 Community).
3. Tools → Board → **NodeMCU 1.0 (ESP-12E Module)**; pick the COM port that appears when you plug it in.
   No port? Install the USB driver printed on the board's USB chip (CH340 or CP2102).
4. Tools → Manage Libraries → install **DHT sensor library** (Adafruit; accept "Adafruit Unified Sensor")
   and **ArduinoJson** (Benoit Blanchon, **7.x**).
5. Serial Monitor at **115200** baud.

## Test order

| # | Sketch | What it proves | Pass when |
|---|---|---|---|
| 1 | `tests/01_dht11` | Temperature sensor | A reading every 5 s; warming it in your hand raises it |
| 2 | `tests/02_light` | Light sensor + calibration | Covered: under ~10 %. Torch: over ~50 %. Set `LIGHT_INVERT` if it is backwards |
| 3 | `tests/03_leds` | Status LEDs | Each of the 6 steps happens in order |
| 4 | `tests/04_wifi_time` | Wi-Fi + internet time | Shows an IP and the correct UTC time |
| 5 | `tests/05_hmac` | Signing matches the backend | Both lines say PASS (no network needed) |
| 6 | `tests/06_post` | Real readings reach the dashboard | `HTTP 200` and `accepted: yes`; dashboard gauges move |
| 7 | `camera/cam_test.py` | C270 works | Resolution, fps and a saved snapshot |
| 8 | `camera/camera_agent.py` | Camera evidence on door opening | Torch on the light sensor → frames appear in Incidents |
| 9 | **`firmware.ino`** (everything together) | Whole node | Self-test all PASS; dashboard shows live readings; torch → red LED + dashboard alarm + Telegram |

Note what you change (inverted light sensor, different pins). The combined
firmware needs the same settings.

## secrets.h

Tests 4 and 6 read `secrets.h` in their own folder. Both are already created with the device key from
`cold-chain/.env`; fill in only:

- `WIFI_SSID`, `WIFI_PASS`: a **2.4 GHz** network. The ESP8266 cannot join 5 GHz. A phone hotspot works
  if it is set to 2.4 GHz / "maximise compatibility".
- `SERVER_URL`: `http://<laptop IP>:8000`. Get the IP from `ipconfig` (IPv4 Address) on the **same** Wi-Fi.

`secrets.h` files are git-ignored. `secrets.example.h` is the blank template.

## Running the backend for the hardware (test 6 onwards)

From the `cold-chain` folder:

```bash
.venv\Scripts\python -m backend --host 0.0.0.0 --no-telegram --db data/hardware.db --reset
```

- No `--timelapse`: real hardware uses real time, and the backend checks every payload's timestamp and
  signature.
- `--host 0.0.0.0` lets the board reach the laptop. Allow Python on **private networks** when Windows asks.
  Use this only on your own Wi-Fi or hotspot.
- `--no-telegram` while testing; drop it when you want real alerts in the group.
- Open `http://127.0.0.1:8000` to watch the dashboard.

## Camera (tests 7 and 8)

```bash
.venv\Scripts\python -m camera.cam_test --show
```
```bash
.venv\Scripts\python -m camera.camera_agent --url http://127.0.0.1:8000 --show
```

- If camera 0 is the laptop's built-in webcam, add `--source 1`.
- The first run downloads two small MediaPipe model files (pose 5.8 MB, hands 7.8 MB) into `camera/models/`.
- The agent draws the pose skeleton, face points and hand landmarks, streams that video to the dashboard's
  **Cargo camera** card, and reports people to the backend with simple cues: hands raised, reaching, crouching,
  lingering, hand in view.
- The backend decides what a sighting means: at an authorised stop it is logged as handling; anywhere else it
  raises **SUSPICIOUS PERSON** (driver + admin alerted, Telegram message with the annotated photo, cargo
  under review); with the hold lit at the same time it is **THEFT SUSPECTED** (everything quarantined).
- Unedited frames from before, during and after each incident are stored as hashed evidence (Incidents tab).
- Test without the camera: `--source some_photo.jpg` or a video file. `--once` annotates one frame to
  `camera/annotated.jpg` and exits.
- It reports "a person" and pose cues only, never who. Tell anyone handling the cargo that the hold is recorded.
- On real hardware (no `--timelapse`) the camera's uploads carry the device key from `cold-chain/.env`, so
  nobody else on the Wi-Fi can fake a sighting.

## Mock temperature and light (demo aid)

Start the backend with `--demo`. In the dashboard click the **Hold** chip (temperature) or the **Light** chip: untick
**Mock ...** to use the real sensor, tick it to get a slider that sets the value the node reports (checked every 2 s,
`ALLOW_MOCK = true` in `firmware.ino`). Mock readings are labelled on the dashboard, in the log and in the EPCIS export.
Door-open and theft rules react to mocked light like real light. Set `ALLOW_MOCK = false` for strictly real measurements.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| DHT11 "no data" | DATA not on D2, or no 3V3 |
| Light value never changes | AO not on A0, or module powered from the wrong pin |
| Board resets when Wi-Fi starts | Weak USB port or cable; use a data cable and a different port |
| HTTP -1 / no answer | Backend not started with `--host 0.0.0.0`, wrong laptop IP, firewall, or 5 GHz Wi-Fi |
| `accepted: NO`, reason `BAD_SIGNATURE` | `DEVICE_SECRET` differs from `COLDTRUST_SECRET` in `.env` |
| reason `STALE_TIMESTAMP` | Board time wrong: test 4 must show the correct UTC time |
| reason `REPLAY_SEQUENCE` | Two boards share one `DEVICE_ID`, or the board's clock jumped backwards |
