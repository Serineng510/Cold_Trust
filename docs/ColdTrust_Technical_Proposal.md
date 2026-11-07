# ColdTrust: Technical Proposal

Track 2 Secure Logistics & Digital Trust · Direction 2 Cold-chain guardian · Team Bibilabu

ColdTrust is a working prototype that monitors a refrigerated truck's cargo hold, checks that its own sensor data can be trusted, and only then decides, explains and acts. Rules set each package's status, three Gemini AI agents explain the risk and propose actions, and an administrator approves anything disruptive.

## 1. Problem and target users

Temperature-sensitive products, including pharmaceuticals, vaccines and perishable goods, need reliable monitoring and timely responses to environmental changes throughout the cold chain. Research on cold-chain logistics in a developing Asian country found temperature fluctuations, reliance on data loggers and manual monitoring, and insufficient expertise as challenges (Abu Hassan, Ngah & Ab Talib, 2021). These methods can cause unnecessary alarms and delayed responses to cooling-system failures, and cannot explain the cause of an alert. The Cybersecurity and Infrastructure Security Agency (CISA, 2020) highlights vehicle hijacking and cargo tampering as security risks and the need to protect monitoring data against manipulation.

ColdTrust serves two users. The **administrator** monitors shipments, reviews risks, approves actions, manages incidents and generates reports. The **driver** receives operational alerts and sees shipment information through a read-only interface.

## 2. Proposed solution

ColdTrust joins a hardware node to a software pipeline with four separate responsibilities: the verification gateway establishes data trust, the rules engine sets package status, AI agents explain and recommend, and the administrator keeps final authority.

- **Hardware.** An ESP8266 sensor node measures temperature, humidity and light inside the cargo compartment; a change in light approximates a door opening. Readings are sent in batches with a Hash-based Message Authentication Code (HMAC). A USB camera with MediaPipe detects the presence of people without identifying anyone.
- **Verification gateway.** Before any reading is used, the backend checks the HMAC signature, sequence number, freshness and replay. A Seal trust report lists each check as passed, suspicious, failed or not run.
- **Rules engine.** Accepted readings are judged against each package's own storage limits and permitted excursion budget, which sets its status and risk score. Suspected theft quarantines the cargo at once, subject to administrator review, and revokes each package's custody token, so a receiving scanner that checks the token is told not to accept the package.
- **People and records.** A role-based dashboard serves the administrator and the driver. The Telegram bot Frozen Warden sends alerts. Event records are structured to GS1 EPCIS 2.0 for traceability, and incident reports carry cryptographic hashes of the photo evidence.

## 3. Agentic AI workflow and human oversight

Three Gemini agents analyse events, one call per alert. The **Thermal agent** explains temperature deviations and possible cooling-system failure; a maintenance report is produced only when the review period holds at least 60 logged readings, which is 30 minutes at the node's 30-second logging rate. The **Seal agent** analyses data-integrity problems such as invalid signatures, replays and contradictory readings. The **Incident agent** evaluates thermal and security risk, recommends actions from a fixed menu (quarantine, divert to the backup cold store, take the vehicle offline, prepare a report), and supplies the wording of a draft incident report, which is produced automatically for suspected theft or a suspicious person and marked as not yet reviewed by a person.

The agents cannot change a package status or run a disruptive action. Rules decide status; the administrator approves or rejects every proposal and reviews, edits and signs every report before it is final, and the driver is read-only. Every number in AI text is checked against the data it was given, and if AI output is unavailable or invalid the rules' own explanation is shown instead. A temperature event becomes an incident report only after a maintenance review finds the cooling unit may be weakening.

## 4. Tools and data

| Layer | Tools |
| --- | --- |
| Hardware | ESP8266 NodeMCU, DHT11, light module, Logitech C270 webcam |
| Backend | Python, FastAPI, SQLite; MediaPipe person detection runs locally |
| AI | Gemini API, free tier (gemini-3.5-flash-lite) |
| Interfaces | Browser dashboard (Leaflet, OpenStreetMap), Telegram bot Frozen Warden, Word incident report from the team's template |
| Standards | HMAC-SHA256, GS1 EPCIS 2.0 format (JSON-LD), CISA 2020 cold-chain guidance |

**Data.** Live temperature, humidity and light readings every 5 seconds; camera person-detection results and SHA-256-hashed photos; built-in sample shipment data (10 packages with their own limits, vehicle WXY 9913, a Shah Alam to Johor Bahru route); and ten simulated scenarios for testing. No faces are identified. Only reading summaries go to Gemini, and demo data only, because the free tier may use submitted content.

## 5. Safeguards

- **Data integrity:** signed batches, sequence numbers, freshness and replay checks, and eight Seal checks (signature, sequence, freshness, time window, light jitter, door against temperature, second thermometer, humidity response) that show *not run* rather than skip silently, as the second-thermometer check does on a node without that sensor; a door open for 5 minutes with a flat temperature is flagged as not trusted.
- **AI containment:** AI cannot set a status; numbers it quotes are verified; its proposals come from a fixed menu limited by the rules; text planted in data did not steer it (0 of 3 injection tests).
- **Access and accountability:** administrator-only approvals, read-only driver, sign-in with lockout, and an audit log of every action and notification.
- **Evidence and privacy:** photos stored with SHA-256 hashes; people are detected, never identified; secrets and local data are kept out of the repository.

## 6. Limitations

- Results come from ten simulated scenarios written by the same author, compared with a deliberately fast threshold alarm; this is a small sample, not a field trial.
- The DHT11 reads whole degrees and light only approximates a door opening; a second thermometer is not fitted, GPS is simulated and the humidity threshold is not yet calibrated on the hardware.
- Gemini runs on a free tier with request limits, and the time saved when reviewing an incident has not been measured.
- Tested on Windows only. The EPCIS 2.0 records use a demo company prefix; they are not GS1-certified and full compliance is not claimed.

## 7. Implementation and results

The prototype is built and runs end to end: sensor node, verification gateway, rules engine, three agents, dashboard, Telegram alerts, EPCIS export and Word reports, with 312 automated tests. Anyone can reproduce the demo without hardware by running `judge_demo.bat` from github.com/Serineng510/Cold_Trust.

| Measure (10 simulated scenarios) | Plain threshold alarm | ColdTrust |
| --- | --- | --- |
| False alarms | 1 | 0 |
| Data attacks recognised | 0 of 3 | 3 of 3 |
| Cargo held at the first alert | 0 of 5 | 5 of 5 |
| Slow cooler failure, first alert | 19:00 | 8:00 (11 minutes earlier) |

The AI agents gave a usable answer in 60 of 60 live calls (30 alert analyses and 30 report wordings) with no invented numbers (median 1.6 seconds); none of 9 Incident answers proposed an action outside the fixed menu, and 0 of 3 hidden-instruction tests were obeyed.

**Next steps:** a field trial on a real refrigerated vehicle, a calibrated second thermometer and a reed-switch door sensor, GPS, and a private model endpoint in place of the free tier.
