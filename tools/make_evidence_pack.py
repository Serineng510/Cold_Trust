"""Build docs/evidence/ColdTrust_Evidence_Pack.pdf for the HackAI "06 Supporting evidence" upload.

    python tools/make_evidence_pack.py

Reads the files already in docs/ (results, diagrams, screenshots in docs/evidence/) and prints one HTML page set to PDF
with Edge (headless). Put two real photos in docs/evidence/ (hardware_overall.jpg: hardware plus the dashboard on the laptop;
hardware_only.jpg: the boards and sensors close up; .jpeg or .png also work) and run it again: until then page 5 shows clearly marked placeholders. Every figure comes from a file in docs/, not from this script.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DOCS = ROOT / "docs"
EV = DOCS / "evidence"
OUT = EV / "ColdTrust_Evidence_Pack.pdf"
EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]

from backend import epcis  # noqa: E402


def esc(s: object) -> str:
    return html.escape(str(s))


def mmss(seconds: int | None) -> str:
    return "-" if seconds is None else f"{seconds // 60}:{seconds % 60:02d}"


def img(path: Path, cls: str = "") -> str:
    return f'<img class="{cls}" src="{path.as_uri()}">'


def page(title: str, body: str, note: str = "") -> str:
    foot = f'<div class="note">{note}</div>' if note else ""
    return f'<section><h2>{esc(title)}</h2>{body}{foot}</section>'


def table(head: list[str], rows: list[list[object]], cls: str = "") -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><tr>{th}</tr>{tr}</table>'


def detection() -> tuple[dict, list[dict]]:
    d = json.loads((DOCS / "detection_results.json").read_text(encoding="utf-8"))
    return d["summary"], d["scenarios"]


def test_facts() -> tuple[str, list[list[str]]]:
    text = (DOCS / "test_results.txt").read_text(encoding="utf-8")
    result = re.search(r"RESULT: (.+)", text).group(1)
    run = re.search(r"Run: (.+?)\s{2,}", text).group(1)
    rows = [m.groups() for m in re.finditer(r"^\s*(\d+)\s+(test_\w+\.py)\s+(.+)$", text, re.M)]
    return f"{result} (run {run})", [list(r) for r in rows]


def epcis_excerpt() -> tuple[str, str]:
    doc = json.loads((DOCS / "sample-epcis-theft.jsonld").read_text(encoding="utf-8"))
    events = doc["epcisBody"]["eventList"]
    ctx = doc["@context"]
    verdicts = epcis.verify_events(events, ctx)
    ok = f"{sum(v['valid'] for v in verdicts)} of {len(verdicts)} event hashes recomputed and matched"
    shown = dict(events[-1])
    shown["epcList"] = shown["epcList"][:2] + [f"... {len(events[-1]['epcList']) - 2} more package SSCCs"]
    return json.dumps(shown, indent=2), ok


def photo_slot(stem: str, title: str, hint: str) -> str:
    """One captioned photo, or a clearly marked placeholder while the file is missing."""
    for ext in (".jpg", ".jpeg", ".png"):
        if (EV / (stem + ext)).exists():
            return f'<figure>{img(EV / (stem + ext), "photo")}<figcaption><b>{esc(title)}</b></figcaption></figure>'
    return (f'<figure><div class="placeholder"><b>PHOTO PLACEHOLDER: {esc(title)}</b><br>{hint}<br><br>Save it as '
            f'<code>docs/evidence/{stem}.jpg</code>, then run <code>python tools/make_evidence_pack.py</code> again.</div>'
            f'<figcaption><b>{esc(title)}</b></figcaption></figure>')


def build_html() -> str:
    s, scen = detection()
    tests_line, test_rows = test_facts()
    epcis_json, epcis_ok = epcis_excerpt()
    agent_eval = (DOCS / "agent_eval.md").read_text(encoding="utf-8")
    assert "30 usable answers out of 30 calls" in agent_eval, "docs/agent_eval.md changed: update page 7 of the pack"

    pages: list[str] = []

    pages.append(f'''<section class="cover"><div class="kicker">HackAI · Track 2 Secure Logistics &amp; Digital Trust · Direction 2 Cold-chain guardian · Team Bibilabu</div>
<h1>ColdTrust</h1><p class="lead">An AI-assisted cold-chain guardian that checks its own sensor data before it acts.</p>
<p>ColdTrust is a working prototype that monitors a refrigerated truck's cargo hold, checks that its own sensor data can be trusted, and only then decides, explains and acts. Rules set each package's status, three AI agents explain the risk and propose actions, and an administrator approves anything disruptive.</p>
<h3>Supporting evidence pack for the Technical Proposal by Team Bibilabu</h3>
{table(["Page", "What it shows"], [
        ["2", "Problem, users and solution, as in the Technical Proposal, with where each is evidenced"],
        ["3", "System architecture"],
        ["4", "The three AI agents and where the rules and the human stay in charge"],
        ["5", "The hardware prototype"],
        ["6", "Measured result: ColdTrust against a plain threshold alarm"],
        ["7", "Evaluation of the AI agents (60 live calls)"],
        ["8", "Dashboard: a faked-data attack is caught (screenshots)"],
        ["9", "Dashboard: suspected theft, automatic report and evidence (screenshots)"],
        ["10", "Sample incident report produced by the system (Word file from the team's template)"],
        ["11", "GS1 EPCIS 2.0 event record, with hash check"],
        ["12", "Automated tests, safeguards and limits"]], "toc")}
<p class="warn"><b>Read this first.</b> Every scenario, number and picture in this pack comes from the built-in simulator or is a labelled sample
frame, not from a real delivery. The simulator, the rules and the baseline were written by the same person, so the results show what the system
does on known cases, not how often real incidents happen. Run it yourself: <code>judge_demo.bat</code> (README, no hardware, no sign-in).</p></section>''')

    pages.append(page("Problem, users and solution", f'''<div class="two"><div>
<h3>Problem</h3>
<p>Temperature-sensitive products, including pharmaceuticals, vaccines and perishable goods, need reliable monitoring and timely responses to changes throughout the cold chain.</p>
<p>Research on cold-chain logistics in a developing Asian country found <b>temperature fluctuations</b>, <b>reliance on data loggers and manual monitoring</b>, and <b>insufficient expertise</b> as challenges
(Abu Hassan, Ngah &amp; Ab Talib, 2021). These methods can cause <b>unnecessary alarms</b> and <b>delayed responses to cooling-system failures</b>, and cannot explain the cause of an alert.</p>
<p>The Cybersecurity and Infrastructure Security Agency (CISA, 2020) highlights <b>vehicle hijacking and cargo tampering</b> as security risks and the need to <b>protect monitoring data against manipulation</b>.</p></div>
<div><h3>Target users</h3>
<p><b>Administrator:</b> monitors shipments, reviews risks, approves actions, manages incidents and generates reports.</p>
<p><b>Driver:</b> receives operational alerts and sees shipment information through a read-only interface.</p>
<h3>Solution in one line</h3>
<p>Sensor-data verification, product-specific risk assessment, AI-assisted incident analysis and human-approved responses. <b>Rules decide the status, the AI only explains and proposes, and a person approves anything disruptive.</b>
Every number in AI text is checked against the data before it is shown.</p></div></div>
{table(["Challenge in the proposal", "How ColdTrust responds", "Evidence in this pack"], [
        ["Temperature fluctuations, unnecessary alarms, delayed response to cooling failures", "Each product is judged against its own limits and excursion budget; the Thermal agent reviews the trend, and a maintenance report follows only when enough relevant readings exist", "p6: 0 false alarms against 1; first alert 11 min earlier on a slow cooler failure"],
        ["Alerts that cannot be explained; insufficient expertise", "Three Gemini agents explain each alert in plain language and propose actions from a fixed menu; rule-based text is the fallback", "p7 (60 live calls, 0 invented numbers); p8"],
        ["Vehicle hijacking and cargo tampering", "Camera finds a person in the hold without identifying anyone; the rules quarantine the cargo for admin review; a draft incident report with hashed evidence is prepared automatically", "p9, p10"],
        ["Monitoring data manipulated", "HMAC signature, sequence, freshness and replay checks, then the Seal trust report (passed / suspicious / failed / not run)", "p6: 3 of 3 data attacks recognised; p8"],
        ["Traceability across the supply chain", "Events are written as GS1 EPCIS 2.0 records with cryptographic hashes", "p11"]], "small")}''',
                      "Problem text follows the submitted Technical Proposal. Reference: Abu Hassan, M. F., Ngah, A. H., &amp; Ab Talib, M. S. (2021). The state of cold logistics supply chain in a developing Asian country: a preliminary insights. Operations and Supply Chain Management, 14(4), 467-475. CISA (2020), cold-chain security guidance."))

    pages.append(page("System architecture", img(DOCS / "diagrams" / "architecture.png", "wide-img"),
                      "Source: docs/diagrams/architecture.svg (generated by tools/make_diagrams.py)."))
    pages.append(page("The three agents, the rules and the human", img(DOCS / "diagrams" / "agent_workflow.png", "wide-img"),
                      "Source: docs/diagrams/agent_workflow.svg. <b>Thermal agent:</b> explains temperature deviations and possible cooling-system failure; a maintenance report is generated only when enough relevant readings exist. <b>Seal agent:</b> analyses data-integrity issues (invalid signatures, replays, contradictory readings). <b>Incident agent:</b> evaluates thermal and security risk, recommends actions from a fixed menu, and drafts an incident report automatically for theft or a suspicious person. The agents cannot change a package status or run a disruptive action; the administrator approves, edits or rejects."))

    pages.append(page("Hardware prototype", f'''<div class="two photos">
{photo_slot("hardware_overall", "Overall setup: hardware and software", "One frame showing the ESP8266 board, DHT11, light module and C270 webcam <b>and</b> the laptop with the ColdTrust dashboard open on the same readings.")}
{photo_slot("hardware_only", "Hardware only", "Close-up of the ESP8266 NodeMCU, DHT11, light module, wiring and USB cable, plus the Logitech C270 webcam.")}</div>
{table(["Part", "Role"], [["ESP8266 NodeMCU", "Reads the sensors every 5 s and sends readings in batches with an HMAC for backend verification (Wi-Fi)"],
                         ["DHT11", "Cargo temperature and humidity"],
                         ["Light module", "Light intensity inside the hold; a change approximates a door-opening event"],
                         ["Green and red LEDs", "Green: heartbeat, reading sent. Red: alarm or Wi-Fi down"],
                         ["Logitech C270 webcam", "MediaPipe person detection in the cargo compartment, without identifying individuals; photos stored with cryptographic hashes"]], "small")}''',
                      "Firmware and wiring: firmware/README.md. The device key is in firmware/secrets.h (git-ignored). The DHT11 reads whole degrees only; the second thermometer is not fitted and GPS is simulated, which the dashboard labels."))

    cases = {"door_ajar": "Door opens while moving, temperature climbs", "theft": "Door opens, person seen, outside an authorised stop",
             "authorised_stop": "Door opens at an authorised dock (should NOT alarm)", "compressor_fail": "Slow temperature rise over 45 virtual min",
             "freeze": "Sudden drop to 0 °C", "replay": "Attacker resends a captured message", "spoof": "Door open 7 min yet temperature perfectly flat",
             "sensor_disagree": "Second thermometer disagrees with the DHT11", "stopped_theft": "Truck stops mid-route, door opens", "normal": "Six quiet minutes"}
    rows = [[sc["scenario"], cases.get(sc["scenario"], ""), mmss(sc["baseline_first_s"]), mmss(sc["cold_first_s"]), sc["cold_kind"] or "-",
             f'{sc["held_at_first_alert"]} of {sc["packages"]}'] for sc in scen]
    earlier = next(iter(s["earlier"].values()))
    slow = next(sc for sc in scen if sc["scenario"] == "compressor_fail")
    pages.append(page("Measured result: ColdTrust against a plain threshold alarm", f'''
{table(["Measure", "Plain threshold alarm", "ColdTrust"], [
        ["False alarms (normal driving and a dock stop)", s["false_alarms_baseline"], s["false_alarms_coldtrust"]],
        [f'Data attacks recognised (replay, flat spoof, contradicting sensor)', f'0 of {s["attacks_total"]}', f'{s["attacks_caught_coldtrust"]} of {s["attacks_total"]}'],
        ["Incidents where cargo was already held at the first alert", f'{s["held_baseline"]} of {s["incidents"]}', f'{s["held_coldtrust"]} of {s["incidents"]}'],
        ["Slow cooler failure: first alert", mmss(slow["baseline_first_s"]), f'{mmss(slow["cold_first_s"])} ({earlier // 60} min earlier)']], "wide")}
<h3>Per scenario (minutes:seconds from the start of the simulated run)</h3>
{table(["Scenario", "What happens", "Alarm", "ColdTrust alert", "ColdTrust says", "Held at first alert"], rows, "small")}''',
                      "Source: docs/detection_results.md / .json, made by tools/eval_detection.py. 10 simulated scenarios; the baseline is a deliberately fast, favourable threshold alarm "
                      "(instant, no delay). On a door event both know at the same moment: the difference is that ColdTrust holds the cargo and names the situation."))

    pages.append(page("Evaluation of the AI agents (60 live calls)", f'''
<p>Model <code>gemini-3.5-flash-lite</code> (free tier), 10 simulated scenarios × 3 runs, played through the real engine. Figures are the model's <b>raw</b> answers, checked before any safeguard touched them.</p>
{table(["Check", "Raw answers affected", "Safeguard"], [
        ["Usable answers", "60 of 60 (30 alert analyses + 30 report wordings)", "If a call fails the app shows the rule-based text"],
        ["Invented numbers (a figure not in the input)", "0 of 60", "Number check drops the whole answer"],
        ["Action outside the allowed menu", "0 of 9 Incident answers", "approvals.clean; the schema allows only 4 actions"],
        ["Obeyed an instruction hidden in the data (prompt injection)", "0 of 3 injection runs", "Status and actions never come from AI text"],
        ["Report wording stated cargo damage or certain theft", "0 of 30", "Prompt forbids it; the report says damage is not assessed"],
        ["Latency per call", "median 1.6 s, slowest 7.1 s", "-"]], "wide")}
<h3>What the evaluation found and fixed</h3>
<ul><li>Asking the Incident agent for report wording and proposals in one call made 4 of 9 replies run away; the wording is now a separate call.</li>
<li>A stray control character (NUL) appeared in 1 of 30 answers; it would corrupt a Word file, so text is cleaned before use. Both fixes have tests.</li></ul>''',
                      "Source: docs/agent_eval.md and docs/agent_eval_runs.json (tools/eval_agents.py). 30 + 30 answers are evidence, not a statistical guarantee; the checks are automatic and narrow and do not score how well an explanation is written."))

    pages.append(page("Dashboard: a faked-data attack is caught", f'''
<div class="two shots"><div style="flex:0 0 40%">{img(EV / "shot_data_trust.png", "dt")}<p class="cap">Data trust card (Seal agent): signature, sequence and freshness pass, but the door was open 5 min in 31 °C air and the temperature never moved, so the data is <b>Not trusted</b>.</p></div>
<div>{img(EV / "shot_approvals.png", "ap")}<p class="cap">Approvals card: the system proposes, an admin decides. Nothing is quarantined by the AI.</p>
{img(EV / "shot_audit_log.png", "al")}<p class="cap">Audit log: the rules' verdict, the Seal agent's explanation and each proposal, with who made it.</p></div></div>''',
                      "Scenario <code>spoof</code> from the simulator, run on the judge demo server (no sign-in). Reproduce: <code>judge_demo.bat spoof</code>."))

    pages.append(page("Dashboard: suspected theft, automatic report, evidence", f'''
{img(EV / "shot_theft_overview.png", "half")}{img(EV / "shot_theft_evidence.png", "half")}
<p class="cap">Left: THEFT SUSPECTED on the map with the cargo quarantined, the Gemini Incident agent's proposals awaiting approval, and a <b>Download report</b> button already in the alert bar.
Right: the Evidence tab lists the automatically prepared reports and the stored pictures (SHA-256 fingerprinted).</p>''',
                      "Scenario <code>theft</code> from the simulator; the two pictures are labelled <b>SAMPLE FRAME - not a real capture</b> and were posted to the evidence endpoint to show the report embedding them."))

    pages.append(page("Sample incident report produced by the system", f'''
<div class="three">{img(EV / "sample_report_p1.png")}{img(EV / "sample_report_p2.png")}{img(EV / "sample_report_p3.png")}</div>''',
                      "File: docs/evidence/SAMPLE-incident-report-theft.docx. Filled automatically about 15 s after the alert, from the team's own Word template. It is marked "
                      "'Automatic report, not yet reviewed by a person'; the system does not claim cargo damage or certain theft, and a person must review and sign. Pictures are sample frames."))

    pages.append(page("GS1 EPCIS 2.0 event record", f'''
<div class="two"><div><p>Every status change is also written as a GS1 EPCIS 2.0 <b>ObjectEvent</b> (JSON-LD): packages as SSCC Digital Links, the vehicle as GIAI, places as GLN, the event ID as a
CBV 2.0 hash of the event content, and ColdTrust's own fields under the <code>ct:</code> extension. Theft is recorded as <b>non_conformant</b> with the cause <code>THEFT_SUSPECTED</code>, never as "stolen".</p>
<p class="ok"><b>Check:</b> {esc(epcis_ok)} (<code>backend/epcis.py verify_events</code>, same as <code>/api/epcis/verify</code>). Any edit after capture breaks the hash.</p>
<p>Endpoints: <code>/api/epcis</code> (document), <code>/api/epcis/events</code> (query), <code>/api/epcis/verify</code>. The prefix 9521234 is a demo company prefix, not a registered GS1 prefix.</p></div>
<div><pre>{esc(epcis_json)}</pre></div></div>''',
                      "Excerpt: the last of 7 events in docs/sample-epcis-theft.jsonld (departing, transporting x3, holding, transporting, inspecting). Full file included in the repository."))

    pages.append(page("Automated tests, safeguards and limits", f'''
<div class="two"><div><p class="ok"><b>{esc(tests_line)}</b></p>
{table(["Tests", "File", "What it checks"], test_rows, "small")}
<p>Not covered: the physical board (USB, Wi-Fi, sensors), checked by hand. Source: docs/test_results.txt.</p></div>
<div><h3>Safeguards</h3><ul>
<li>The verification gateway checks the HMAC-SHA256 signature, sequence number, freshness and replays before any reading is processed.</li>
<li>Eight Seal checks, each shown as passed, suspicious, failed or <i>not run</i>, never silently skipped.</li>
<li>Rules set status; AI text is number-checked and cannot set a status; the approval menu is fixed and limited by the rules.</li>
<li>Admin approves, driver is read-only; sign-in with lockout (the judge demo turns it off on purpose).</li>
<li>Camera finds people but never identifies them; photos are hashed.</li>
<li>If AI output is unavailable or invalid, the rules' own explanation is shown instead.</li>
<li>Secrets (<code>.env</code>, <code>secrets.h</code>) and local data are git-ignored.</li>
<li>Design follows CISA's December 2020 cold-chain guidance.</li></ul>
<h3>Limits, stated plainly</h3><ul>
<li>Results are simulated and small; no field trial yet.</li>
<li>Time saved in reviewing an incident is not measured.</li>
<li>DHT11 is coarse (whole degrees); second thermometer not fitted, GPS simulated, humidity threshold not yet calibrated on the hardware.</li>
<li>Tested on Windows; the free Gemini tier may use submitted content, so only demo data is sent.</li></ul></div></div>'''))

    css = '''
@page { size: A4 landscape; margin: 12mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", Arial, sans-serif; color: #1c2a1f; font-size: 11pt; margin: 0; }
section { page-break-after: always; break-after: page; }
section:last-child { page-break-after: auto; }
h1 { font-size: 34pt; margin: 2mm 0 1mm; color: #2f3d12; } h2 { font-size: 19pt; margin: 0 0 4mm; color: #2f3d12; border-bottom: 2px solid #c9d3a8; padding-bottom: 2mm; }
h3 { font-size: 12.5pt; margin: 4mm 0 2mm; } p { margin: 2mm 0; line-height: 1.4; } .lead { font-size: 13pt; margin-bottom: 2mm; } .cover h3 { margin: 2mm 0; } table.toc { font-size: 9.5pt; } table.toc th, table.toc td { padding: .8mm 2mm; }
.kicker { font-size: 10pt; letter-spacing: .05em; text-transform: uppercase; color: #5b6b3a; }
table { border-collapse: collapse; width: 100%; margin: 2mm 0; } th, td { border: 1px solid #d5dbc3; padding: 1.6mm 2.4mm; text-align: left; vertical-align: top; }
th { background: #eef2df; } table.small { font-size: 8.6pt; } table.small th, table.small td { padding: 1mm 1.8mm; } table.wide { font-size: 10pt; } table.toc { width: 70%; }
img.wide-img { width: 100%; max-height: 150mm; object-fit: contain; display: block; margin: 0 auto; }
.two { display: flex; gap: 8mm; } .two > div { flex: 1; min-width: 0; } .three { display: flex; gap: 4mm; } .three img { width: 32%; border: 1px solid #ccc; }
.shots img { display: block; border: 1px solid #d5dbc3; border-radius: 3mm; max-width: 100%; width: auto; } .shots .dt { max-height: 134mm; } .shots .ap { max-height: 58mm; } .shots .al { max-height: 58mm; } img.half { width: 49%; vertical-align: top; border: 1px solid #d5dbc3; }
.cap { font-size: 9pt; color: #4b5a45; margin: 1.5mm 0 3mm; } .note { font-size: 8.8pt; color: #5b6b5f; margin-top: 3mm; }
.warn { background: #fff4d6; border: 1px solid #e3c36a; padding: 2.5mm; margin-top: 3mm; font-size: 9.5pt; } .ok { background: #e7f3e4; border: 1px solid #a9d3a1; padding: 2mm 3mm; }
.placeholder { border: 3px dashed #c0392b; background: #fff1ee; color: #7b241c; padding: 6mm; height: 82mm; display: flex; flex-direction: column; justify-content: center; text-align: center; font-size: 10pt; }
figure { margin: 0; } .photos figure { flex: 1; min-width: 0; } figcaption { font-size: 10pt; margin-top: 1.5mm; text-align: center; }
img.photo { max-width: 100%; max-height: 98mm; display: block; margin: 0 auto; object-fit: contain; border: 1px solid #ccc; }
pre { background: #1c2a1f; color: #e6efd2; padding: 3mm; font-size: 7.6pt; line-height: 1.3; white-space: pre-wrap; word-break: break-all; border-radius: 2mm; }
code { background: #eef2df; padding: 0 1mm; font-size: 9pt; } ul { margin: 1mm 0; padding-left: 5mm; } li { margin: 1mm 0; line-height: 1.35; }
'''
    return f'<!doctype html><html><head><meta charset="utf-8"><title>ColdTrust evidence pack</title><style>{css}</style></head><body>{"".join(pages)}</body></html>'


def main() -> None:
    EV.mkdir(parents=True, exist_ok=True)
    tmp = EV / "_pack.html"
    tmp.write_text(build_html(), encoding="utf-8")
    edge = next((e for e in EDGE if Path(e).exists()), None)
    if not edge:
        sys.exit(f"Edge not found. Open {tmp} in a browser and print it to PDF (A4 landscape, no headers).")
    subprocess.run([edge, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={OUT}", tmp.as_uri()],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tmp.unlink(missing_ok=True)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
