# The three agents: what each one specifically does

Source: PLAN.md section 10 (Thermal, Seal, Incident + Orchestrator), checked against the code of 19 Oct 2026.
Principle for all three: **rules decide, the AI explains.** Status, severity and quarantine are always decided by
deterministic rules (`backend/engine.py`, `rules.py`). Gemini (or Claude) only writes the plain-language explanation.
Nothing an agent writes can change a status.

## 1. Thermal Agent: "Is the cargo getting too warm or too cold?"

| | |
|---|---|
| Watches | Hold temperature readings (every 5 s, logged 30 s), trend, step change, each package's own limits and excursion budget, ambient temperature, second thermometer if fitted |
| Rules it owns | Outside a package's limits for 2 readings; slope >= 0.5 C/min over 4 min; change >= 5 C in 60 s. Falling or below minimum = FREEZE_RISK, otherwise THERMAL_EXCURSION. Minutes outside limits are added up per package against its budget |
| Alerts it explains | THERMAL_EXCURSION, FREEZE_RISK |
| Maintenance review | The **Summarise with AI** button on the Maintenance tab: the Thermal agent turns the computed indicators (per day, up to 7 days) into a written review of the cooling unit. It sees only the summary, never raw readings; one call per press, cached 10 minutes; rule-based text if the AI is off or paused |
| Writes (AI) | What happened (2-3 sentences), what it means for each affected product using its own limits and budget used, up to 4 actions (check the cooling unit, re-check in 10 min, divert to the backup cold store by name) |
| Hands on | "Thermal problem, severity, affected packages" to the Orchestrator. If a door event happened just before, that explains a rise |
| Never | Judges doors, people or theft. Never decides quarantine itself (budget used up or risk >= 70 does, by rule) |

## 2. Seal Agent: "Was the hold opened, was that allowed, and can we trust the data?"

| | |
|---|---|
| Watches | Hold light (>= 30 % = door open), how long it stayed bright, vehicle zone, speed and route status (authorised stop = inside an authorised geofence and under 8 km/h), whether temperature responded to the opening, signature/replay results, silent node, stale GPS |
| Rules it owns | Light at an authorised stop = AUTHORISED_HANDLING if the temperature is fine, or the hold is only warming for under 10 minutes (then THERMAL_EXCURSION); a cooling hold is FREEZE_RISK at once. Light elsewhere = DOOR_AJAR. Light while stopped on an active route = THEFT_SUSPECTED. Trust checks: door open for minutes with no temperature change, perfectly flat light, two thermometers disagreeing = SPOOF_SUSPECTED / DATA_TRUST. Rejected messages (bad signature, replay) and a silent node |
| Alerts it explains | DOOR_AJAR, AUTHORISED_HANDLING, SPOOF_SUSPECTED, DATA_TRUST, rejected data, node silent, GPS stale |
| Writes (AI) | Where and when the hold was opened, whether that was expected at this place, and whether the sensor data can be believed |
| Hands on | Light-on plus context to the Incident Agent (so the camera takes a picture). "Data not trusted" to everyone, so a temperature reading is not taken at face value |
| Never | Identifies people or decides theft on its own (a person is the Incident Agent's input) |

## 3. Incident Agent: "Is someone there who should not be, and what is the proof?"

| | |
|---|---|
| Watches | Camera observations (person present, pose cues such as hands raised, reaching, lingering), alerts from the other two agents, vehicle position, speed and zone |
| Rules it owns | Person at an authorised stop = logged as handling. Person elsewhere = SUSPICIOUS_PERSON (critical, cargo under review). Person with the hold lit, outside a stop = THEFT_SUSPECTED (emergency, everything quarantined) |
| Alerts it explains | SUSPICIOUS_PERSON, THEFT_SUSPECTED |
| Evidence it builds | Time, location, picture for each alert, stored with a SHA-256 fingerprint; photo sent in the Telegram alert. Shown in the Evidence tab (time, location, picture, what was detected) |
| Writes (AI) | A factual account: what was seen, where, what the driver and dispatcher should do. Describes actions only, never names or describes a person. Optional: looks at a camera frame to confirm "person" and "door open" (needs `COLDTRUST_VISION=on`, because the picture leaves the laptop) |
| Hands on | Verdict inputs to the Orchestrator; the evidence picture to Telegram and the dashboard |
| Never | Identifies or accuses anyone. Never files a report by itself; a human reviews |

## Safeguards on every agent answer
- **Number check**: each number in the answer must be in the agent's input (rounding allowed). Otherwise the answer is
  discarded, the rule text is kept, and the log says which number was rejected. Applies to alert explanations and the
  maintenance review.
- **One Telegram message per alert**: the agent's analysis (plus the rules' status line), or the rule-based text if the AI is late (over 5 s), off, paused or rejected. The dashboard log keeps both. No extra AI call.

## Orchestrator (not one of the three, but they only work together through it)
Combines the three agents' findings in a fixed order (first match wins): theft, spoof, handling, door ajar, thermal, data trust.
Then it sets each package's status, picks the notification (Telegram when new, escalated, status changed, or every 10 min
as a reminder), triggers the dashboard siren and voice, and writes the audit log.

## How they work together (examples)
| Situation | Seal | Thermal | Incident | Result |
|---|---|---|---|---|
| Door opens at the depot, temperature fine | Authorised stop | quiet | person = handling | AUTHORISED_HANDLING, logged only |
| Door opens on the highway, nobody seen | Door ajar | warm trend | quiet | DOOR_AJAR, driver warned |
| Door opens on the highway, person seen | Light on | warming | Person, not authorised | THEFT_SUSPECTED, quarantine, photo to Telegram |
| Light on at 0 km/h on an active route | Stopped on route | - | takes picture | THEFT_SUSPECTED |
| Door "open" 7 min, temperature perfectly flat | Spoof suspected | discount reading | - | SPOOF_SUSPECTED, admin only |
| Slow warm-up, no door event | quiet | Rising | - | THERMAL_EXCURSION, maintenance advice |

## Built today versus planned
| | Today | Planned (not built) |
|---|---|---|
| Rules and verdicts | All in `engine.py` / `rules.py`, one code path | Split into `agents.py`: three classes, each with its own `check()`, plus the Orchestrator |
| AI role | Each alert is routed to its agent's prompt (Thermal / Seal / Incident) and written up. Each agent is shown all findings, so Thermal can see a door event | Agents ask each other targeted questions; a periodic trip summary |
| Incident vision | Only with `COLDTRUST_VISION=on` | Clip (10 s before, 15 s after) and a printable draft report page |
| Live Gemini | Not yet run with a real key (`python -m backend.gemini_check`) | - |

The split into `agents.py` is only worth doing if you want the code to match the pitch ("three agents that coordinate");
it does not change any behaviour. About half a day, with the 72 tests as a safety net.
