# ColdTrust ❄️

> An AI-assisted cold-chain guardian that checks its own sensor data before it acts. Built for HackAI Track 02: Secure Logistics & Digital Trust (Direction 2: Cold-chain guardian).

🎥 **Demo video:** [https://youtu.be/b-3K01fc2FE](#)
🤖 **Telegram bot:** Frozen Warden · 📐 **Diagrams:** [architecture](docs/diagrams/architecture.png) · [agent workflow](docs/diagrams/agent_workflow.png)

---

## The Vision

Cold-chain failures can be found only after the cargo has spoiled, and the data that would show what happened can be faked: a sensor can be frozen, a message replayed, a door opened with no record. Interviews with Malaysian cold-chain providers describe trucks that rely on a data logger and manual methods, with temperature swings whenever the reefer door opens (Abu Hassan, Ngah & Ab Talib, 2021).

**ColdTrust verifies its own data first, then decides.** Ordinary rules set each package's status, three AI agents explain the risk and propose actions, and an administrator approves anything disruptive. The AI never changes a status.

## Key Features

- **Trusts nothing by default (Seal trust report):** every reading is HMAC-signed and checked for replays, stale timestamps, frozen light, a door "open" while the temperature never moves, and more. Each check shows as passed, suspicious, failed or *not run*.
- **Rules decide, AI explains:** per-product temperature limits and excursion budgets set the status; the Thermal, Seal and Incident agents (Gemini) explain it in plain language, and every number they quote is checked against the data.
- **Human in the loop:** the Incident agent proposes from a fixed menu (quarantine, divert to the backup cold store, take the vehicle offline, prepare the report). Only an admin approves; the driver is read-only.
- **Evidence and reports:** hashed camera photos, GS1 EPCIS 2.0 event records, and a Word incident report filled from your template (drafted automatically for a suspected person or theft; for a temperature event only after a maintenance review).
- **Measured, not just claimed:** on 10 simulated scenarios against a plain threshold alarm, ColdTrust had 0 false alarms (alarm: 1), recognised 3 of 3 data attacks (alarm: 0), and flagged a failing cooler 11 minutes earlier. See [docs/detection_results.md](docs/detection_results.md). Simulated data, small sample.

## Tech Stack

- **Hardware:** ESP8266 (NodeMCU) + DHT11 + light module, Logitech C270 webcam (people detection only, never identification).
- **Backend:** Python, FastAPI, SQLite.
- **AI:** Gemini API (free tier), MediaPipe.
- **Frontend:** plain JavaScript dashboard, Leaflet / OpenStreetMap.
- **Standards:** GS1 EPCIS 2.0, HMAC-SHA256; safeguards follow CISA's 2020 cold-chain guidance.

##  How to Run Locally

### No hardware? One click (Windows)

1. Install [Python 3.10 or newer](https://www.python.org/downloads/) (tick *Add python.exe to PATH*).
2. Clone the repository:
   ```bash
   git clone https://github.com/Serineng510/Frozen_Warden.git
   cd Frozen_Warden
   ```
3. **Double-click `judge_demo.bat`.** The first run sets up Python (about a minute). It then starts a throwaway server, plays a scenario with the built-in simulator, and opens the dashboard at <http://127.0.0.1:8000>. No login, no Telegram, no API key.

Try another scenario from a terminal in the same folder:

```bash
judge_demo.bat theft      # also: spoof (default) | door_ajar | compressor_fail | authorised_stop | freeze | stopped_theft | replay | sensor_disagree | normal
```

**What to look at (Overview tab):** the **Data trust** card (each integrity check), the **audit log** (what the rules decided and why), and the **Approvals** card (what the agent proposes, for an admin to approve).

> `demo.bat` is different: it is for the real board and camera. On its own, without the hardware sending readings, it shows an empty dashboard. Without hardware, use `judge_demo.bat`.

### Mac / Linux (or by hand)

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-demo.txt          # Windows: .venv\Scripts\python
.venv/bin/python -m backend --timelapse --reset --no-telegram --no-login     # dashboard: http://127.0.0.1:8000
```

Then, in a second terminal, play a scenario (restart the server with `--reset` before the next one):

```bash
.venv/bin/python -m sim.sim spoof --timelapse                     # `.venv/bin/python -m sim.sim --list` shows all ten
```

Port 8000 busy? Add `--port 8001` to the server command (and `--url http://127.0.0.1:8001` to the simulator).

### With the hardware

Wire and flash the board by following [firmware/README.md](firmware/README.md), copy `firmware/secrets.example.h` to `firmware/secrets.h` and fill it in, then double-click `demo.bat` (add `-NoCamera` if no camera is plugged in). Settings are in [REFERENCE.md](REFERENCE.md).

## Optional: turn on the AI agents

Without a key everything still works and the screens show the rules' own text. To see the Gemini agents:

1. Create a free key at <https://aistudio.google.com/apikey>.
2. Copy `.env.example` to `.env` and add one line: `GEMINI_API_KEY=your-key` (`.env` is git-ignored: never commit it).
3. Check it: `.venv/bin/python -m backend.gemini_check` (Windows: `.venv\Scripts\python`).
4. Restart the server.

Free-tier Gemini may use submitted content, so use the demo data only. Claude or another provider: see [REFERENCE.md](REFERENCE.md).

##  More

[REFERENCE.md](REFERENCE.md) (login and roles, AI safeguards, incident reports, all settings) · [PLAN.md](PLAN.md) (design) · [RUNBOOK.md](RUNBOOK.md) (demo-day steps) · [docs/](docs) (evaluation results)

*Abu Hassan, M. F., Ngah, A. H., & Ab Talib, M. S. (2021). The state of cold logistics supply chain in a developing Asian country: a preliminary insights. Operations and Supply Chain Management, 14(4), 467-475.*
