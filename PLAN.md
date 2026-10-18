# Cold-Chain Integrity Agent — Evaluation & Build Plan

> Working name: **ColdTrust** (rename freely).
> Status: planning. Nothing in this document is built yet.
> Assumptions are flagged with **[A]**; open questions are collected in section 17.

---

## 0. Decisions and status (updated 2026-10-14)

**Confirmed by the team lead (you):** DHT11 temperature sensor, deadline **23 Oct 2026**, **solo**, Telegram for
driver/admin messages (to try).

### 0.1 What the DHT11 changes

| DHT11 property | Consequence | What the build does about it |
|---|---|---|
| ±2 °C accuracy, 1 °C steps | A "5 °C" reading may be 3–7 °C; thresholds flicker | 2-sample debounce before a package changes status; calibration offset in firmware; **hold seeded at 3 °C** so the +2..+4 °C band (salmon) gives room |
| Range 0–50 °C | Cannot demonstrate deep-freeze (−18 °C) | Demo products are +2..+8 °C goods; freeze scenario drops 4 -> 0 °C |
| Max 1 sample/s | "Within 1 minute" detection is feasible, "instant" is not | 5 s detection samples, regression over 4 min for rate, 60 s window for step |
| Whole-degree output | Zero-variance on temperature is meaningless | Zero-variance applies to the **light** channel only; humidity ignored |
| Slow thermal response | Ice pack takes 1-2 min to show | Rehearse; keep the simulator as backup |

A DS18B20 (about $1, ±0.5 °C, digital) is a drop-in upgrade later; the payload and backend do not change.

### 0.2 Solo schedule (9 days)

| Date | Work | Exit check |
|---|---|---|
| Wed 14 Oct | Backend, rules, engine, simulator, tests | **Done** (21 tests pass; live-verified) |
| Thu 15 Oct | Dashboard v1: top bar, gauges, alert ticker, audit terminal, package grid, browser voice | Scenario plays live on screen |
| Fri 16 Oct | Wire ESP8266 + DHT11 + light module; firmware v1; real device -> backend | Real readings in dashboard |
| Sat 17 Oct | Camera loop: rolling buffer, stills/clip on trigger, evidence panel | Light flash -> image appears |
| Sun 18 Oct | Claude live (needs API key), Telegram bot, driver phone page | Alert reaches a phone with voice |
| Mon 19 Oct | Map + telemetry overlay, temperature summary, police-report draft page | P1 complete |
| Tue 20 Oct | Field tests: ice pack, flashlight, latency numbers, threshold tuning | Measured numbers recorded |
| Wed 21 Oct | UI polish, README, record backup demo video | Backup video exists |
| Thu 22 Oct | Rehearse x3, slides, **code freeze** | Clean run-through |
| Fri 23 Oct | Buffer / demo day | - |

If Thursday's dashboard slips, drop the map to a static route image and keep the audit terminal: the terminal is
what shows the agent reasoning, which is what is being judged.

### 0.3 Build status

| Component | Status |
|---|---|
| Rules engine (signing, replay, rate, step, excursion budget, spoof checks) | Done, unit-tested |
| Orchestrator (cross-signal verdicts, per-package risk, quarantine, token revoke, override) | Done, unit-tested |
| FastAPI service (ingest, state, audit, evidence upload, custody verify, WebSocket) | Done, live-verified |
| Simulator (8 scenarios, realtime and timelapse) | Done |
| Claude explanation + frame analysis | Written; **not yet exercised against the real API** (no key in this environment) |
| Telegram delivery | Written; **not yet exercised** (needs bot token + chat id) |
| Firmware (`firmware/firmware.ino`) | Written 16 Oct: self-test, 1 h buffer, signed routine + event pings, LEDs (buzzer removed 18 Oct: alarm sound is on the dashboard + Telegram). Payload format verified against the backend; **not yet compiled or run on the board** |
| Dashboard (`dashboard/index.html`, `app.js`, `styles.css`) | Done 15 Oct: five views on live data, verified in the browser at desktop and phone width |
| Camera loop (C270) | Not started |
| Map, summary tab, police-report page | Done 15 Oct as part of the dashboard (map is offline-safe SVG, no tile server) |

### 0.4 Hackathon resources: evaluation and what was integrated (2026-10-15)

The organisers pointed to three sources. What each one offers, and what this project took from it:

| Source | What it is | Useful here? |
|---|---|---|
| [GS1 EPCIS & CBV](https://www.gs1.org/standards/epcis) | GS1's standard for sharing supply-chain events ("what, when, where, why"). Version 2.0 adds **sensor data for cold chains**, JSON-LD and a REST API | **Very.** It is the data format a real shipper or receiver would ask for |
| [EPCIS & CBV Implementation Guideline](https://www.gs1.org/standards/epcis-and-cbv-implementation-guideline/current-standardd) | How-to guide for modelling events | **Background only.** The link opens release **1.2 (Feb 2017)**; the current guideline is 2.0 (Mar 2023), listed on the EPCIS page |
| [CISA resources & tools](https://www.cisa.gov/resources-tools/all-resources-tools) | CISA's catalogue of 1,783 items. The catalogue page itself shows nothing cold-chain specific | **High, through three documents found by searching it** (below) |

The three CISA documents that matter (all December 2020, written for COVID-19 vaccine distribution, US-focused, and
now archived; the principles still apply to any temperature-sensitive cargo):

* [Cold Storage Cyber Custodial Care](https://www.cisa.gov/resources-tools/resources/cold-storage-cyber-custodial-care) (cyber checklist)
* [Vaccine Distribution Security Concerns in the Last Mile](https://www.cisa.gov/sites/default/files/publications/CISA%20COVID-19%20Vaccine%20Distribution%20Infographic_508cV3.pdf) (vulnerabilities and mitigations for storage, local delivery and dispensing)
* [Critical Questions & Considerations for Cold Chain, Storage, and Dry Ice Operations](https://www.cisa.gov/resources-tools/resources/critical-questions-considerations-cold-chain-storage-and-dry-ice-operations) (planning questions)

plus the general [Cross-Sector Cybersecurity Performance Goals 2.0](https://www.cisa.gov/cybersecurity-performance-goals-2-0-cpg-2-0) (goal 3.A, "Changing Default Passwords").

#### CISA guidance mapped to ColdTrust

| CISA guidance (source) | ColdTrust feature | Status |
|---|---|---|
| "Avoid the use of default or maintenance passwords" (Custodial Care); CPG 2.0 goal 3.A | Backend **refuses to start** in real-hardware mode with the default or a short device secret | Built, tested |
| "Consider use of an additional, less exposed thermometer as independent verification" (Custodial Care) | Optional second thermometer (`temp2_c`, e.g. a $1 DS18B20). A gap above 3 °C for 3 samples raises `SENSOR_DISAGREEMENT` and an EPCIS `ERROR_CONDITION` | Built in backend and simulator; firmware wiring pending |
| "What response plans have been developed if temperature does not hold or connectivity is lost?" (Critical Questions) | Watchdog: `NODE_SILENT` after 2 missed routine pings, `NODE_RESTORED` when data resumes | Built, tested |
| "Ensure transporter GPS is activated; conduct active surveillance while vehicle is on the road" (Last Mile) | Watchdog: `GPS_STALE` when the position stops updating en route | Built, tested |
| "Identify alternative custodians" (Custodial Care, Critical Questions) | Backup cold store (Texarkana) in the route data; thermal, freeze and silent-node advice names it | Built (demo data) |
| "What capability exists to track and verify cold chain of custody?" (Critical Questions) | Custody tokens revoked on quarantine (existing) + hash-verified EPCIS trip record (new) | Built, tested |
| "Staged transporter cargoes ... susceptible to tampering"; "monitor staged cargo" (Last Mile) | Light and camera rules fire whether moving or parked; only authorised geofences stay quiet | Already built |
| "Develop notification protocols ... who should be contacted in emergencies" (Last Mile) | Telegram group, change-based alerts, 10-min reminders | Built; escalation contact list planned |
| "check temperature control devices for functionality after loading, prior to delivery and every 4 hours" (Last Mile) | Driver check-in prompt on Telegram at those three moments | Planned with two-way Telegram |
| "Ensure that the cold chain assets are not visible on search engines for internet-connected devices" (Custodial Care) | Backend binds to 127.0.0.1 by default; LAN only with `--host 0.0.0.0`; never port-forward | Already built; stated in README |
| Report cold-storage cyber incidents to CISA / FBI (both documents) | Incident report page lists who to report to, configurable per country (CISA and FBI are US channels) | Planned |
| Driver ID verification, insider threat (Last Mile) | Out of scope for this build; the Secure Handoff app in this repo covers driver and recipient verification | Mention in pitch |

#### GS1 EPCIS 2.0 integrated

`backend/epcis.py` (a port of this repo's `epcis.js`) records the trip as standard EPCIS 2.0 ObjectEvents.
Vocabulary was checked against [ref.gs1.org](https://ref.gs1.org/cbv/BizStep) and the official EPCIS 2.0 JSON schema.

| Trip moment | bizStep | disposition | Extra |
|---|---|---|---|
| First telemetry | `departing` | `in_transit` | Dallas Depot GLN |
| Every accepted device ping | `transporting` | `in_transit` | `sensorElementList`: Temperature (min/max/mean, `CEL`), RelativeHumidity (`P1`), light level; `ALARM_CONDITION` when out of limits or bright, `ERROR_CONDITION` when data is suspect |
| Package under review | `holding` | `unknown` | "segregated for further review" |
| Package quarantined | `inspecting` | `non_conformant` | `persistentDisposition: set` |
| Supervisor release | `inspecting` | `conformant` | `persistentDisposition: unset`, supervisor and reason |

* Identifiers are GS1 Digital Links: SSCC per package, GLN per location, GIAI for the truck and sensor node, `geo:`
  read points in transit. All use GS1's reserved demo prefix 952, as real prefixes need GS1 membership.
* Every event carries a CBV 2.0 **Event Hash ID**. The Python port reproduces the GS1 EPCIS Sandbox's hashes for the
  repo's test vectors, so a hash can be checked with GS1's own tool. `GET /api/epcis/verify` recomputes them all and
  catches any record edited after capture.
* Suspected theft is recorded as `non_conformant`, never `stolen`: that disposition is for a human to confirm.
* The uncalibrated LDR is reported as a relative level (`Dimensionless`, `P1`), not as lux.
* Generated trip documents validate against the official schema in the test suite;
  [docs/sample-epcis-theft.jsonld](docs/sample-epcis-theft.jsonld) is an example.

**Pitch line:** "Every alert is also a GS1 EPCIS 2.0 record that any shipper's system can import and verify, and the
safeguards follow CISA's own cold-chain guidance."

**Schedule note:** Thu 15 Oct went to Telegram and this standards work, so dashboard v1 moves to Fri 16 Oct and the
hardware day to Sat 17 Oct. The Fri 23 Oct buffer absorbs the slip.

Sections 1-18 below are the original design; where they differ from 0.1 (sensor limits) or 0.2 (dates), this
section wins.

---

## 1. One-paragraph pitch

Commercial cold-chain loggers record temperature and tell you *after delivery* that something went wrong.
ColdTrust is a low-cost sensor node plus a team of AI agents that **watches temperature and handling events in
real time, explains the risk to product integrity in plain language, alerts the driver and admin by voice and
message within seconds, and automatically assembles tamper-evident evidence** (video clip, sensor trail, hashes)
so that a human can act on it, reject a shipment, or file a report.

The product is not the sensor. It is **sensing → reasoning → action → evidence**.

---

## 2. Evaluation of the idea

### 2.1 Fit to the hackathon brief

| Brief requirement | How the idea covers it | Fit |
|---|---|---|
| A specific trust problem in cold-chain failure | Temperature excursions and unauthorized handling (door opened, theft) with no trustworthy record | Strong |
| Watch temperature and handling events | DS18B20/DHT temperature + light/door-seal sensor + camera | Strong |
| Explain risk to product integrity | Per-product limits, excursion budget, risk score with factors, plain-language explanation | **Strong only if built as the centrepiece** |
| AI agent / multi-agent / agent platform | Thermal, Seal, Incident agents + Orchestrator | Strong if agents truly coordinate |

### 2.2 Scorecard

Scores are my estimate against *typical* hackathon criteria. The organiser's actual rubric may differ.

| Criterion | Score /5 | Why |
|---|---|---|
| Problem relevance | 5 | Cold-chain spoilage and tampering are real, costly and easy to explain |
| Innovation | 3.5 | Sensing + alerts is common. Novelty is cross-signal reasoning, evidence chain and risk explanation |
| Technical feasibility | 4 | Cheap, available parts; main risk is integration time |
| Demo-ability | 4 | Light flash + ice pack + live voice alert is vivid, if rehearsed |
| Scope control | 2.5 | The spec is larger than a hackathon. Needs the P0/P1/P2 cut in section 4 |
| Trust angle | 3 → 4.5 | Becomes strong once data signing, evidence hashing and human approval are added |
| Business/impact story | 4 | Pharma, food, e-commerce grocery, insurance claims |

### 2.3 Strengths

- Physical hardware plus a live dashboard is a better demo than software alone.
- Three clear agent roles that map to real user pain (temperature, door/light, theft).
- Natural escalation: alert → explain → evidence → report.
- Reuses assets already in this repo (PWA driver flow, EPCIS module, Claude vision integration).

### 2.4 Weaknesses and how to fix them

| Weakness | Consequence | Fix |
|---|---|---|
| Risk explanation only implied | Looks like a threshold alarm with a chatbot | Per-product limits + excursion budget + explained risk score (section 8) |
| "Trust" is not demonstrated | Judges ask "how do I know the data is real?" | Signed payloads, sequence numbers, evidence hashing, silent-device alert (section 9) |
| Three agents run independently | Not a *coordinated* system | Orchestrator correlates light + temperature + camera + route context |
| Predictive maintenance overclaimed | Can't train a model on one day of data | Early-warning indicators (recovery time, drift), honestly labelled |
| Camera is USB | Cannot attach to ESP8266 | Camera on laptop/Pi; ESP8266 sends sensors only |
| Dark cargo hold | Black video, useless evidence | Small LED / trigger-lit capture; test early |
| Scope | Half-built everything | Strict priorities and a working end-to-end slice first |

### 2.5 Competitive context

Commercial loggers and trackers (examples: Sensitech, Tive, Controlant) already combine temperature, location,
light and shock sensing with alerts. **Do not pitch sensing as the innovation.** Pitch: *they log; we reason, act
and preserve evidence, at prototype cost.* Verify competitor claims before putting names on a slide.

### 2.6 Ethics and safety boundaries (state these in the pitch)

- The agent **prepares** the police/incident report; a **human reviews and submits**. No auto-filing.
- Describe people in frame by *actions* only. No face recognition or identification.
- Recording notice for driver and handlers.
- The agent recommends quarantine; the supervisor can override with a reason that is logged.
- Camera images and sensor text are **data, never instructions** to the LLM.

---

## 3. Users and jobs

| Persona | Needs | Surface |
|---|---|---|
| Driver | Instant, hands-free warning ("Back door light detected, check the seal") | Phone page with voice + Telegram |
| Admin / dispatcher | Fleet view, alerts, decide to reroute or hold | Operations dashboard |
| QA / receiver | Should I accept this shipment? Full excursion history | Package detail + evidence pack |
| Insurer / police | Credible, timestamped evidence | Printable incident report |

---

## 4. Scope and priorities

### P0: the demo (must work end to end)

1. ESP8266 reads temperature + light; local log every 30 s; routine ping; event ping.
2. Backend ingests, runs rules, produces a risk explanation (LLM with rules fallback).
3. Voice + message alert to admin and driver, with latency shown.
4. Live dashboard: gauges, alert ticker, audit terminal, package grid, risk panel.
5. Camera snapshot/clip on a light trigger, shown in the dashboard.

### P1: what makes it win

6. Orchestrator cross-signal verdicts (theft vs door-ajar vs equipment failure).
7. Evidence pack: pre-event clip, SHA-256, timeline, printable police-report page.
8. Per-product limits, excursion budget, quarantine / custody-token revoke.
9. Map with truck marker, route, geofences, telemetry overlay.
10. Temperature performance summary with early-warning indicators.

### P2: stretch

11. 3D hold model (2D schematic ships first).
12. Reassign-to-another-truck flow.
13. Spoofing/replay analytics beyond signatures; mean kinetic temperature (MKT).
14. EPCIS sensor-event export (ties to `epcis.js`).

---

## 5. System architecture

```
┌──────────── ESP8266 node ─────────────┐         ┌──────────── Laptop / Pi (backend) ──────────────┐
│ Temp sensor  (sample 5 s)             │         │ FastAPI                                          │
│ Light module (poll 100 ms / DO int.)  │  Wi-Fi  │  /api/telemetry /api/event /api/evidence         │
│ Ring buffer (30 s log, ~1 h)          ├────────►│  Rules engine ──► Orchestrator ──► Agents(Claude)│
│ Routine ping / Event ping             │  HMAC   │  Camera loop (OpenCV, 30 s rolling buffer)       │
│ Buzzer (local)                        │  JSON   │  Evidence store (clips + SHA-256)                │
└───────────────────────────────────────┘         │  Notifier (Telegram, WebSocket, TTS trigger)     │
                                                  │  SQLite                                          │
   USB ┌──────────────┐                           └───────────┬──────────────────────────────────────┘
   ───►│ Logitech C270│─────► camera loop                      │ WebSocket + REST
       └──────────────┘                                        ▼
                                                  ┌──────────────────────────┐   ┌─────────────────┐
                                                  │ Web dashboard (admin)    │   │ Driver phone    │
                                                  │ map · cargo · audit · TTS│   │ page · TTS · TG │
                                                  └──────────────────────────┘   └─────────────────┘
```

**Design rules**

1. Deterministic rules decide *whether* something is wrong. The LLM decides *what it means* and *how to say it*.
2. Rules alert immediately; the LLM explanation follows 1-5 s later and updates the same alert.
3. If the LLM is unavailable, the system still alerts, with a template explanation.
4. Every decision, input and notification is written to an append-only audit log.

---

## 6. Hardware plan

### 6.1 Bill of materials

| Part | Role | Notes |
|---|---|---|
| ESP8266 (NodeMCU / Wemos D1 mini) | Sensor node | "ESP6266" in the brief is assumed to mean ESP8266 **[A]** |
| DHT11 (confirmed) | Cargo temperature + humidity | ±2 °C, 1 °C steps, 0-50 °C; see section 0.1. DS18B20 is the optional upgrade |
| SN-LIGHT-MOD | Light / door seal | Has analog `AO` and digital `DO`; threshold set by the on-board pot |
| Logitech C270 | Camera | USB, 720p, fixed focus, **no night vision** |
| Laptop or Raspberry Pi | Backend + camera host | The C270 plugs in here |
| Buzzer (optional) | Local alarm at the node | Reinforces "trigger sound" |
| 4.7 kΩ resistor | DS18B20 pull-up | Between data and 3.3 V |
| Small USB LED / light | Illuminate hold for camera | Test whether needed |
| Power bank | Node power | Simulates battery operation |
| Breadboard, jumpers, enclosure | Prototype | Ice-pack-safe enclosure for the demo |

### 6.2 Pin map (NodeMCU)

| Signal | Pin | Note |
|---|---|---|
| DHT11 data | `D2` (GPIO4) | Module boards have the pull-up built in; bare sensor needs 4.7-10 kΩ to 3.3 V |
| Light module `DO` | `D5` (GPIO14) | Interrupt-capable digital trigger |
| Light module `AO` | `A0` | Raw level for trend/variance; only **one** ADC on ESP8266 |
| Buzzer | `D6` (GPIO12) | Active buzzer, optional |
| Status LED | `D4` (built-in) | Wi-Fi/event indicator |

If the temperature module turns out to be an *analog* thermistor, it conflicts with `A0`. Use the light `DO`
pin only, or swap the temperature sensor.

### 6.3 Known hardware limits (say them out loud in the pitch)

- DHT11-class sensors cannot read deep-freeze and have coarse resolution; DS18B20 is the better choice.
- Small sensors have thermal lag; a 5 °C change takes time to register.
- A real battery tracker would use deep sleep with wake-on-light; the prototype keeps modem sleep so the light
  monitoring stays active.
- Wi-Fi stands in for cellular. The store-and-forward buffer is the part that is realistic.

---

## 7. Timing model

Reconciles the intervals in the brief:

| Tier | Interval | Purpose | Demo mode |
|---|---|---|---|
| Event detection sample | 5 s (temp), 100 ms (light) | React fast without radio use | same |
| Local audit log | **30 s** | Granular memory trail | same |
| Dashboard temp card | 5 min | Brief's "update every 5 min" | 30 s |
| Routine transmission ping | **15 min** (30 min / 1 h configurable) | Low-power bulk upload | 60 s |
| Powered-exception ping | 5-10 min | Plugged-in vehicle | 60 s |
| **Event ping** | **immediate** | Critical triggers | same |

### Event triggers (any one fires an event ping)

| Trigger | Rule |
|---|---|
| Light exposure | `DO` goes active, or `A0` rises above baseline + margin for ≥ 2 consecutive reads |
| Absolute threshold | Temperature outside the product's bounds (e.g. +2 to +8 °C) |
| Rate of change | Slope over the last 60-120 s above ±0.5 °C/min (config) |
| Step change | Delta ≥ 5 °C within 1 minute (brief's "5 °C in one minute") |
| Node health | Sensor read failure, buffer near full, Wi-Fi lost beyond N min |

### Latency budget (measure and display these in the demo)

| Step | Target |
|---|---|
| Trigger → HTTP POST sent | ≤ 500 ms |
| Backend rules + first alert (voice + message) | ≤ 2 s from trigger |
| LLM explanation attached | +2-5 s |
| Evidence clip finalised (10 s post-roll) | ≤ 15 s |

---

## 8. Risk model (the "explain risk to product integrity" core)

### 8.1 Per-product profile (data, not code)

```json
{
  "package_id": "SHP-4574",
  "description": "Vaccine vials (x24)",
  "temp_min_c": 2, "temp_max_c": 8,
  "excursion_budget_min": 30,
  "freeze_sensitive": true,
  "light_sensitive": true,
  "handling_tags": ["Fragile", "Cold chain"],
  "destination": "Memphis, TN",
  "recipient": "TechFlow Inc."
}
```

Budget values are **illustrative**. Real values come from manufacturer labelling.

### 8.2 Computed factors

| Factor | Computation |
|---|---|
| Time out of range | Cumulative minutes outside bounds, from the 30 s log |
| Budget consumed | time out of range ÷ `excursion_budget_min` |
| Peak deviation | Max distance outside bounds, °C |
| Rate of change | Regression slope over the last 2 min |
| Light exposure | Duration and count of bright events |
| Recovery time | Minutes to return in range after a door/light event |
| Data trust | Signature valid, sequence continuous, variance plausible |

### 8.3 Risk score (0-100) and status

```
risk = 50·budget_consumed(capped 1) + 20·peak_deviation_norm + 15·light_sensitive_exposure
       + 10·rate_norm + 5·low_data_trust
```

| Status | Colour | Rule |
|---|---|---|
| NORMAL | Green | risk < 30 and in bounds |
| UNDER REVIEW | Amber | 30 ≤ risk < 70, or short excursion, or authorised door event |
| QUARANTINED - DO NOT ACCEPT | Red | risk ≥ 70, budget exhausted, or confirmed breach with tamper evidence |

Weights are a starting point; tune with the simulator.

### 8.4 Agent output (structured JSON, validated)

```json
{
  "verdict": "UNDER_REVIEW",
  "risk_score": 58,
  "confidence": 0.82,
  "threat_type": "thermal_excursion",
  "affected_packages": ["SHP-4574"],
  "explanation": "Hold reached 11.4 C for 7 min against an 8 C limit; 23% of the excursion budget is used.",
  "product_impact": "Vaccine potency may be reduced if the budget is exhausted.",
  "recommended_actions": ["Check door seal", "Re-check in 10 min", "Flag for QA review on arrival"],
  "evidence_refs": ["evt_0192", "clip_0042"]
}
```

### 8.5 Predictive summary (honest version)

Show **early-warning indicators**, not "AI prediction":

- Recovery time after each door opening (rising trend = weakening cooling).
- Time-in-range % per trip.
- Baseline drift over the trip.
- Steady-state oscillation size (compressor cycling).
- Projected time-to-limit from the current slope, shown as a simple linear estimate with its assumptions.

---

## 9. Trust and integrity layer

| Concern | Control |
|---|---|
| Forged or edited telemetry | HMAC-SHA256 over the payload with a per-device key; reject on mismatch |
| Replay | Monotonic sequence number + timestamp window + batch hash check |
| Static/spoofed sensor | Zero-variance on raw light `A0`; implausible flat temperature relative to context |
| Contradictory data | Door/light event with hot ambient but flat cold reading → flag "possible spoof" |
| Silent/unplugged node | No heartbeat for N min raises an alert |
| Evidence tampering | SHA-256 per clip and per report, stored with timestamps; report lists the hashes |
| Operator abuse | Overrides require a reason and are written to the audit log |

The ESP8266 has limited crypto capacity, so the signature is HMAC with a shared secret, not certificates.
State that as a prototype limitation.

---

## 10. Agent design

### 10.1 Roles

| Agent | Watches | Produces |
|---|---|---|
| **Thermal Agent** | Temperature trend, rate, excursion budget | Risk explanation; maintenance summary |
| **Seal Agent** | Light sensor + route/geofence context | Door-open vs authorised-stop classification |
| **Incident Agent** | Events from the others + camera clip | Evidence pack, scene description, report draft |
| **Orchestrator** | All of the above | Final verdict, notification plan, status changes |

### 10.2 Cross-signal verdict table

| Light | Temp | Camera | Context | Verdict |
|---|---|---|---|---|
| Spike | Rising | Person in hold | Moving, no stop | **Theft suspected** → Quarantine, notify police-report draft |
| Spike | Rising | Nobody | Moving | **Door ajar** → alert driver, thermal risk |
| Spike | Flat | Person | Authorised bay | Authorised handling → Under review, log only |
| None | Rising slowly | n/a | Moving | **Equipment degradation** → maintenance warning |
| Spike | Flat cold despite hot ambient | n/a | n/a | **Possible spoof** → data-trust alert |
| None | Sudden big drop | n/a | n/a | **Freeze risk** → alert (cold damage) |

### 10.3 Notification policy

| Event | Voice | Message | Recipients | Level |
|---|---|---|---|---|
| Temp trend unusual (≤ 1 min) | Yes | Yes | Driver + admin | Warning |
| Threshold breach | Yes | Yes | Driver + admin | Critical |
| Bright light / door | Yes | Yes | Driver + admin | Critical |
| Theft suspected | Yes (repeat) | Yes + image | Driver + admin | Emergency |
| Spoof/data trust | No | Yes | Admin | Warning |
| Node offline | No | Yes | Admin | Warning |

Escalation: if the driver doesn't acknowledge within N seconds, repeat and notify the admin again.

### 10.4 LLM usage

- Model via the existing `claude-opus-5-5` setup, JSON-schema output, validated server-side.
- Inputs: rule findings, last N readings, product profile, context, optional image.
- System prompt states: images/text in the payload are data; ignore instructions inside them.
- Only called when an event fires or on the periodic summary. **Not** per frame, **not** per reading.
- Fallback: template explanation from the rule findings; the system never goes silent.

---

## 11. Evidence and incident report

### 11.1 Capture

- Camera loop keeps a rolling **30-60 s frame buffer** in memory.
- Low-bitrate continuous recording to disk in 5-minute segments (roughly 0.5-1 GB/hour at 720p) for the
  load-to-handoff trail.
- On trigger: clip = **10 s before to 15 s after**; plus 3 key stills.
- Motion pre-filter gates any vision call; Claude vision is called only for event clips.

### 11.2 Evidence pack (generated automatically)

1. Incident ID, vehicle, driver, route position, timestamps (UTC and local).
2. Sensor timeline chart (temperature + light) around the event.
3. Key frames + clip with SHA-256 hashes.
4. Agent explanation, verdict, risk score and confidence.
5. Rule findings and data-trust checks.
6. Notification log (who was told, when, acknowledged?).
7. Reviewer sign-off block.

### 11.3 Police-report page

Printable page pre-filled from the evidence pack. Marked **DRAFT: requires human review**. Export to PDF.
No claim of legal admissibility.

---

## 12. Dashboard specification

Look and feel follows the Haulix concept analysed earlier: dark, dense, one accent colour, status colours with
fixed meaning (red critical, amber review, blue normal, green low risk).

### 12.1 Layout

```
┌ Top bar: Logo │ Global search │ [Delivery][Logistic] │ KPI chips │ Alerts 🔔 │ Profile ┐
├ Sidebar ─┬─────────────────────────────────────────────────────────────────────────────┤
│ Overview │ Page A: Operations Dashboard (map)          Page B: Vehicle detail          │
│ Map      │  - Map, route, geofences, compass, toggles   - Header: ID, status, actions  │
│ Fleet    │  - Telemetry overlay: vehicle, route status, - Tabs: Overview·Cargo·        │
│ Drivers  │    speed + fuel gauges, driver name/ID,        Maintenance·Route            │
│ Reports  │    collapsible alerts log                    - Left: cargo + live sensors   │
│          │                                              - Right: packages + detail     │
└──────────┴─────────────────────────────────────────────────────────────────────────────┘
```

### 12.2 Components

| Component | Content |
|---|---|
| Alert ticker | Latest active alerts, severity-coloured, acknowledge button |
| Map | Dark tiles, truck marker, route line, geofences, compass, layer toggles |
| Telemetry overlay | Vehicle ID, route status, speed + fuel circular gauges, driver name/ID |
| Cargo panel | Volume capacity bar, 2D hold schematic, package grid with status tags |
| Sensor panel | Live temperature gauge + sparkline, light indicator, trend arrows |
| Camera panel | Live feed, "latest evidence" thumbnail, event markers on a timeline |
| Package list | ID, description, priority, weight, volume, tags, customer, destination |
| Package detail | Client, destination, loading order, temperature bounds, risk score, custody status, instructions, View Route / Reassign / Override |
| Audit terminal | Timestamp · ROUTINE/EVENT · parameters · agent reasoning · action taken |
| Summary tab | Time-in-range, recovery time trend, early-warning indicators |

### 12.3 Technical choices

| Concern | Choice |
|---|---|
| Frontend | Plain HTML/CSS/JS (or Preact) served by the backend; no build step |
| Realtime | WebSocket for events, REST for history |
| Map | Leaflet with dark tiles; cached fallback image if venue Wi-Fi fails |
| Gauges/charts | SVG or a small chart lib; pinned versions |
| Voice | Browser `speechSynthesis`; needs an **"Enable alerts" click** to unlock audio |
| Live video | MJPEG endpoint from the backend camera loop |
| Mobile | Driver page is mobile-first; reuse PWA pieces from `public/` |

`app.py` from the original deliverables list becomes the `dashboard/` web app. A thin Streamlit fallback is
possible if the web UI slips.

---

## 13. Repository layout

```
cold-chain/
├─ PLAN.md
├─ README.md                  (setup, wiring, demo script)
├─ requirements.txt
├─ firmware/
│  └─ firmware.ino
├─ backend/
│  ├─ agent.py                (FastAPI entrypoint, endpoints)
│  ├─ rules.py                (deterministic checks, risk factors)
│  ├─ agents.py               (Thermal, Seal, Incident, Orchestrator)
│  ├─ camera.py               (capture loop, rolling buffer, clips)
│  ├─ evidence.py             (hashing, pack, report)
│  ├─ notify.py               (Telegram, WebSocket broadcast)
│  ├─ store.py                (SQLite)
│  └─ schemas.py              (Pydantic models)
├─ dashboard/
│  ├─ index.html
│  ├─ app.js
│  └─ styles.css
├─ sim/
│  └─ sim.py                  (device + GPS + scenario generator)
└─ tests/
```

### Core endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/telemetry` | Routine batch from device |
| POST | `/api/event` | Immediate event ping |
| GET | `/api/state` | Vehicle, packages, latest readings |
| GET | `/api/audit` | Audit log (also streamed via WebSocket) |
| GET | `/api/video` | MJPEG stream |
| GET | `/api/incident/{id}` | Evidence pack |
| GET | `/api/incident/{id}/report` | Printable report |
| POST | `/api/package/{id}/override` | Supervisor override (reason required) |
| WS | `/ws` | Live events to dashboard and driver page |

### Data model (SQLite)

`devices`, `vehicles`, `packages`, `readings`, `events`, `decisions`, `evidence`, `notifications`, `audit_log`.

---

## 14. Simulator, scenarios and demo script

### 14.1 Simulator scenarios

| Scenario | What it injects |
|---|---|
| `normal` | Stable temperature, dark hold, GPS moving |
| `door_ajar` | Light spike while moving, then temperature climbs |
| `theft` | Light spike + person frames from a prepared image/clip |
| `compressor_fail` | Slow temperature rise, longer recovery times |
| `freeze` | Sudden drop below the lower bound |
| `replay` | Repeats an old batch with stale sequence number |
| `spoof` | Flat cold reading while the light sensor shows an opening |
| `authorised_stop` | Light spike inside a loading-bay geofence |

The simulator is also the **safety net**: if the real sensor misbehaves on stage, switch to simulated data
without changing the dashboard.

### 14.2 Four-minute demo script

| Time | Beat | What the judges see |
|---|---|---|
| 0:00 | Problem | One slide: loggers record, nobody explains or acts |
| 0:30 | Live system | Truck moving, gauges live, hold green, audit log scrolling |
| 1:00 | Light event | Flash a phone light at the sensor → voice alert, dashboard turns red, clip captured, latency shown |
| 1:45 | Thermal event | Ice pack on sensor → unusual-pattern alert, risk explanation with budget used |
| 2:30 | Orchestrator | Combined event → "theft suspected" verdict, status `QUARANTINED`, evidence pack + draft police report |
| 3:15 | Trust | Replay attack rejected; show hashes; supervisor override logged |
| 3:45 | Close | Business impact and honest limitations |

---

## 15. Verification plan

| Area | Test | Pass criterion |
|---|---|---|
| Firmware timing | Log timestamps over 10 min | 30 s log interval ±1 s; no blocking calls |
| Event latency | Flash light 20 times | Median alert ≤ 2 s from trigger |
| Rate rule | Controlled heating/cooling | Fires within 60-120 s of ≥ 0.5 °C/min |
| False positives | 30 min of normal operation and an authorised stop | 0 critical alerts on normal runs |
| Replay/spoof | Simulator scenarios | All rejected or flagged |
| Offline resilience | Disconnect Wi-Fi 5 min | Buffered data uploaded on reconnect, no gaps |
| LLM fallback | Remove API key | Template explanations, system still alerts |
| Evidence | Modify a clip byte | Hash check fails |
| Dashboard | Phone and laptop | No horizontal scroll; audio unlock works |

Record these numbers; they go straight into the pitch.

---

## 16. Delivery timeline

Effort is about 40-55 engineer-hours. Deadline unknown **[A]**, so phases are sequential and sized so that
the demo path works after Phase 3.

| Phase | Work | Output | Hours |
|---|---|---|---|
| 0 | Confirm parts, wire sensors, read values over serial | Sensors verified | 2-3 |
| 1 | Backend skeleton, schemas, SQLite, seed data, simulator `normal` | Ingest + state API | 5-6 |
| 2 | Firmware: timers, buffer, event triggers, HTTP POST, signing | Real device → backend | 6-8 |
| 3 | Rules engine + risk score + LLM explanation + fallback | **First end-to-end alert** | 6-8 |
| 4 | Dashboard: top bar, gauges, ticker, audit terminal, package grid | Live UI | 8-10 |
| 5 | Camera loop, rolling buffer, evidence pack, report page | Evidence flow | 6-8 |
| 6 | Orchestrator, notifications (voice + Telegram), driver page | Coordinated agents | 5-6 |
| 7 | Map + telemetry overlay, summary tab, polish | P1 complete | 4-6 |
| 8 | Rehearsal, measurements, README, pitch deck | Submission | 3-4 |

**Two-person split:** one on firmware + hardware + camera; one on backend + agents + dashboard. Agree on the
payload schema in Phase 1 so both sides can work against the simulator.

---

## 17. Risks and open questions

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Hardware fails on stage | Medium | Simulator switch; pre-recorded fallback video |
| Camera useless in the dark | High | LED/lit capture; test in Phase 5 |
| Venue Wi-Fi unreliable | Medium | Phone hotspot; local-only backend; cached map |
| LLM latency/cost | Medium | Event-gated calls; rules alert first; fallback |
| Browser blocks audio | High | Explicit "Enable alerts" step in the demo checklist |
| Scope creep (3D, extra pages) | High | P2 list is frozen until P1 is done |
| Over-claiming | Medium | Limitations slide; honest "early-warning" wording |

### Open questions

1. ~~Temperature module~~ DHT11 (answered)
2. ~~Deadline and team size~~ 23 Oct, solo (answered)
3. ~~Telegram~~ yes, to try (answered)
4. 3D model: skip until P1 is done (recommended), or required?
5. Anthropic API key available, or run rules-only for the demo?
6. Will the backend run on a laptop or a Raspberry Pi?
7. Is there an official judging rubric to align the pitch to?

---

## 18. Pitch narrative

1. **Problem:** cold-chain failures are invisible until the product is rejected; tampering is hard to prove.
2. **Insight:** loggers record; trust requires explanation, action and evidence.
3. **Solution:** sensor node + coordinated agents + evidence chain, at prototype cost.
4. **Demo:** light event → thermal event → verdict → evidence → human approval.
5. **Trust:** signed data, hashed evidence, human in the loop, audit trail.
6. **Impact:** fewer spoiled shipments, faster claims, accountable handoffs.
7. **Honest limits:** DHT-class accuracy, Wi-Fi not cellular, prototype crypto.
8. **Next:** cellular module, certified sensors, EPCIS integration, multi-vehicle fleet.
