"""Who may get an incident report, and when: security events at once, temperature events only after a maintenance review."""
import asyncio
import importlib
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from backend import maintenance
from sim.sim import SECRET, build_payload

ROOT = Path(__file__).resolve().parent.parent
ADMIN_ID, ADMIN_NAME, ADMIN = "ADM-1001", "Serine Ng", "admin-code-1234"
DRIVER_ID, DRIVER_NAME, DRIVER = "DRV-20418", "Marcus Lee", "driver-code-5678"
KEY = {"X-ColdTrust-Key": SECRET}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
GOOD = {"source": "ai", "by": "Gemini · Thermal agent", "hours": 6, "enough_data": True, "cooling_unit": "possible weakening",
        "urgency": "service soon", "assessment": "Recovery after door openings is getting slower.", "from": 100.0, "to": 200.0}


# ---- the rule on its own
@pytest.mark.parametrize("review, ts, ok, why", [
    (GOOD, 150, True, ""),
    (None, 150, False, "No maintenance review"),
    ({**GOOD, "source": "rules"}, 150, False, "written by rules"),
    ({**GOOD, "enough_data": False}, 150, False, "enough sensor history"),
    ({**GOOD, "cooling_unit": "no sign of weakening"}, 150, False, "no sign of weakening"),
    ({**GOOD, "cooling_unit": "not enough data"}, 150, False, "not enough data"),
    (GOOD, 99, False, "does not cover"),
    (GOOD, 201, False, "does not cover"),
    ({**GOOD, "from": None}, 150, False, "does not cover"),
])
def test_report_gate(review, ts, ok, why):
    allowed, reason = maintenance.report_gate(review, ts)
    assert allowed is ok and why in reason


# ---- through the API (login on)
@pytest.fixture()
def app(tmp_path, monkeypatch):
    for k, v in {"COLDTRUST_DB": str(tmp_path / "t.db"), "COLDTRUST_EVIDENCE_DIR": str(tmp_path / "ev"),
                 "COLDTRUST_REPORTS_DIR": str(tmp_path / "reports"), "COLDTRUST_STRICT_TIME": "0", "COLDTRUST_SECRET": SECRET,
                 "COLDTRUST_LLM": "off", "TELEGRAM_BOT_TOKEN": "", "COLDTRUST_DEMO": "1",
                 "COLDTRUST_ADMIN_ID": ADMIN_ID, "COLDTRUST_ADMIN_NAME": ADMIN_NAME, "COLDTRUST_ADMIN_CODE": ADMIN,
                 "COLDTRUST_ADMIN_CONTACT": "serine@example.com", "COLDTRUST_DRIVER_ID": DRIVER_ID,
                 "COLDTRUST_DRIVER_NAME": DRIVER_NAME, "COLDTRUST_DRIVER_CODE": DRIVER,
                 "COLDTRUST_DRIVER_CONTACT": "+60 12-345 6789"}.items():
        monkeypatch.setenv(k, v)
    import backend.agent as a
    import backend.config as c
    importlib.reload(c)
    return importlib.reload(a)


def admin_client(app):
    c = TestClient(app.app)
    assert c.post("/api/login", json={"company_id": ADMIN_ID, "code": ADMIN}).status_code == 200
    return c


T0 = 1_800_000_000


def warm_alert(client, history=0):
    """`history` quiet readings, then a minute of warm ones: one THERMAL_EXCURSION alert. Returns (entry, time of last reading)."""
    quiet = [{"t": T0 + i * 5, "temp_c": 3, "hum": 50, "light": 10 + i % 3} for i in range(1, history + 1)]
    base = T0 + history * 5
    warm = [{"t": base + i * 5, "temp_c": 9, "hum": 50, "light": 10 + i % 3} for i in range(1, 13)]
    seq = 0
    if quiet:
        seq += 1
        client.post("/api/telemetry", json=build_payload(seq, quiet[-1]["t"], quiet))
    r = client.post("/api/telemetry", json=build_payload(seq + 1, warm[-1]["t"], warm)).json()
    assert r["entry"]["scenario"] == "THERMAL_EXCURSION", r["entry"]["scenario"]
    return r["entry"], warm[-1]["t"]


def person_alert(client):
    client.post("/api/vehicle", json={"speed_kmh": 60, "authorised_stop": False, "route_status": "En route"})
    e = client.post("/api/camera/observation", headers=KEY,
                    json={"person_present": True, "persons": 1, "description": "A person is in the cargo hold"}).json()["entry"]
    assert e["kind"] == "SUSPICIOUS_PERSON"
    return e


def paragraphs(blob):
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml"))
    return ["".join(t.text or "" for t in p.iter(W + "t")) for p in root.iter(W + "p")]


def test_a_temperature_event_gets_no_report_without_a_supporting_review(app):
    admin = admin_client(app)
    entry, _ = warm_alert(admin)
    r = admin.post(f"/api/incidents/{entry['id']}/report", json={})
    assert r.status_code == 409 and "No maintenance review" in r.json()["detail"]
    gate = admin.get("/api/state").json()["report_gates"][str(entry["id"])]
    assert gate["ok"] is False and "Summarise with AI" in gate["reason"]
    assert not list(Path(app.cfg.reports_dir).glob("*.docx"))                      # nothing was written


@pytest.mark.parametrize("change", [{"cooling_unit": "no sign of weakening"}, {"source": "rules"}, {"enough_data": False},
                                    {"from": 1.0, "to": 2.0}])
def test_a_review_that_does_not_support_it_still_blocks_the_report(app, change):
    admin = admin_client(app)
    entry, last = warm_alert(admin)
    app.store.kv_set("maintenance_review", {**GOOD, "from": last - 100, "to": last + 100, **change})
    assert admin.post(f"/api/incidents/{entry['id']}/report", json={}).status_code == 409


def test_a_supporting_review_unlocks_the_report_and_is_quoted_in_it(app):
    admin = admin_client(app)
    entry, last = warm_alert(admin)
    app.store.kv_set("maintenance_review", {**GOOD, "from": last - 100, "to": last + 100})
    r = admin.post(f"/api/incidents/{entry['id']}/report", json={})
    assert r.status_code == 200, r.text
    text = "\n".join(paragraphs(admin.get(f"/api/reports/{r.json()['file']}").content))
    assert "Maintenance review (Gemini · Thermal agent, last 6 h): cooling unit - possible weakening" in text
    assert "Type of Incident: Temperature excursion (cargo too warm)" in text
    assert admin.get("/api/state").json()["report_gates"][str(entry["id"])]["ok"] is True


def test_the_agents_do_not_offer_a_report_for_an_unsupported_temperature_event(app):
    admin = admin_client(app)
    entry, last = warm_alert(admin)
    asyncio.run(app._propose(entry, None))
    assert {i["action"] for i in admin.get("/api/approvals").json()} == {"DIVERT"}         # the rules' default, with no report
    assert "PREPARE_REPORT" not in app.approvals.allowed(entry, app.store.list_packages(), {}, app._report_gate(entry)[0])
    # once a review supports it, a fresh alert is offered the report too
    app.store.kv_set("maintenance_review", {**GOOD, "from": last - 100, "to": last + 100})
    later = {**entry, "id": entry["id"] + 1000, "ts": last}
    asyncio.run(app._propose(later, None))
    assert "PREPARE_REPORT" in {i["action"] for i in admin.get("/api/approvals").json()}


def test_approving_prepare_report_for_a_gated_event_is_refused_cleanly(app):
    admin = admin_client(app)
    entry, _ = warm_alert(admin)
    item = app.approval_queue.add(entry, [{"action": "PREPARE_REPORT", "reason": "x"}], "rules", app.engine.now())[0]
    r = admin.post(f"/api/approvals/{item['id']}/decide", json={"approve": True})
    assert r.status_code == 200 and "report not prepared" in r.json()["entry"]["actions"][0]


def test_the_maintenance_summary_remembers_the_latest_review(app, monkeypatch):
    admin = admin_client(app)
    entry, last = warm_alert(admin, history=100)
    r = admin.post("/api/maintenance/summary", json={"hours": 6}).json()
    assert r["source"] == "rules"                                                   # the AI is off in this test
    stored = app.store.kv_get("maintenance_review")
    assert stored["source"] == "rules" and stored["from"] <= entry["ts"] <= stored["to"] and stored["enough_data"]
    assert admin.post(f"/api/incidents/{entry['id']}/report", json={}).status_code == 409        # rule-based is not enough
    # now an AI review that finds a weakening unit
    monkeypatch.setattr(type(app.explainer), "enabled", property(lambda self: True))
    monkeypatch.setattr(app.explainer, "maintenance_checked", lambda rep: ({
        "assessment": "Recovery is slower.", "cooling_unit": "possible weakening", "urgency": "service soon",
        "evidence": [], "recommended_actions": [], "explained_by": "Gemini · Thermal agent"}, []))
    monkeypatch.setattr(app.explainer, "report_text", lambda ctx: None)
    app._maint_cache.clear()
    assert admin.post("/api/maintenance/summary", json={"hours": 6}).json()["source"] == "ai"
    assert app.store.kv_get("maintenance_review")["cooling_unit"] == "possible weakening"
    assert admin.post(f"/api/incidents/{entry['id']}/report", json={}).status_code == 200


# ---- security events: the report is prepared at once
def test_which_alerts_trigger_an_automatic_report(app):
    wants = app._wants_auto_report
    assert wants({"scenario": "SUSPICIOUS_PERSON", "notify_reason": "new"})
    assert wants({"scenario": "THEFT_SUSPECTED", "notify_reason": "escalated"})
    assert not wants({"scenario": "THEFT_SUSPECTED", "notify_reason": "reminder"})        # not one per reminder
    assert not wants({"scenario": "THEFT_SUSPECTED", "notify_reason": "status_change"})
    for other in ("THERMAL_EXCURSION", "FREEZE_RISK", "DOOR_AJAR", "SPOOF_SUSPECTED", "DATA_TRUST"):
        assert not wants({"scenario": other, "notify_reason": "new"})


def test_a_person_alert_is_dispatched_with_an_automatic_report(app, monkeypatch):
    seen = []

    async def stub(entry):
        seen.append(entry["scenario"])
    monkeypatch.setattr(app, "_auto_report", stub)

    async def go():
        app._dispatch({"scenario": "SUSPICIOUS_PERSON", "notify_reason": "new", "kind": "SUSPICIOUS_PERSON", "id": 1,
                       "severity": "warning", "reasoning": "x", "actions": [], "verdict_label": "x"})
        app._dispatch({"scenario": "THERMAL_EXCURSION", "notify_reason": "new", "kind": "THERMAL_EXCURSION", "id": 2,
                       "severity": "warning", "reasoning": "x", "actions": [], "verdict_label": "x"})
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert seen == ["SUSPICIOUS_PERSON"]


def test_the_automatic_report_is_ready_to_download_and_not_repeated(app, monkeypatch):
    monkeypatch.setattr(app, "AUTO_REPORT_DELAY_S", 0)
    admin = admin_client(app)
    entry = person_alert(admin)
    asyncio.run(app._auto_report(entry))
    asyncio.run(app._auto_report(entry))                                                # a reminder must not make a second one
    made = [e for e in admin.get("/api/audit?limit=500").json() if e["kind"] == "REPORT_GENERATED"]
    assert len(made) == 1 and made[0]["params"]["automatic"] is True and made[0]["params"]["incident"] == entry["id"]
    assert made[0]["params"]["by"] == "ColdTrust system (automatic draft)"
    blob = admin.get(f"/api/reports/{made[0]['params']['file']}").content                 # the download button's target
    text = "\n".join(paragraphs(blob))
    assert "Name: ColdTrust system (automatic draft)" in text and "not yet reviewed by a person" in text
    assert "Prepared automatically when the alert was raised. An admin has not reviewed it yet." in text
    assert f"{DRIVER_NAME} ({DRIVER_ID})" in text and f"{ADMIN_NAME} ({ADMIN_ID})" in text and "serine@example.com" in text
    assert not re.search(r"\{[#^/%]?\w+\}", text)


def test_an_admin_can_still_make_a_version_in_their_own_name(app, monkeypatch):
    monkeypatch.setattr(app, "AUTO_REPORT_DELAY_S", 0)
    admin = admin_client(app)
    entry = person_alert(admin)
    asyncio.run(app._auto_report(entry))
    r = admin.post(f"/api/incidents/{entry['id']}/report", json={}).json()
    assert re.search(r"-r\d+\.docx$", r["file"])                       # a new version: the automatic one is never overwritten
    assert f"Name: {ADMIN_NAME} ({ADMIN_ID})" in "\n".join(paragraphs(admin.get(f"/api/reports/{r['file']}").content))


def test_a_driver_cannot_download_a_report(app, monkeypatch):
    monkeypatch.setattr(app, "AUTO_REPORT_DELAY_S", 0)
    admin = admin_client(app)
    asyncio.run(app._auto_report(person_alert(admin)))
    name = next(e for e in admin.get("/api/audit?limit=500").json() if e["kind"] == "REPORT_GENERATED")["params"]["file"]
    driver = TestClient(app.app)
    driver.post("/api/login", json={"company_id": DRIVER_ID, "code": DRIVER})
    assert driver.get(f"/api/reports/{name}").status_code == 403


# ---- the login says "Password"
def test_the_login_page_says_password():
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert '<label>Password<input id="loginCode"' in html and 'type="password"' in html
    assert "access code" not in (html + js).lower()


def test_wrong_login_says_password(app):
    r = TestClient(app.app).post("/api/login", json={"company_id": ADMIN_ID, "code": "not-the-password"})
    assert r.status_code == 401 and r.json()["detail"] == "wrong company ID or password"


# ---- the maintenance review offers the report for approval
def ai_review(app, monkeypatch, unit="possible weakening"):
    monkeypatch.setattr(type(app.explainer), "enabled", property(lambda self: True))
    monkeypatch.setattr(app.explainer, "maintenance_checked", lambda rep: ({
        "assessment": "Recovery is slower.", "cooling_unit": unit, "urgency": "service soon",
        "evidence": [], "recommended_actions": [], "explained_by": "Gemini · Thermal agent"}, []))
    monkeypatch.setattr(app.explainer, "report_text", lambda ctx: None)
    app._maint_cache.clear()


def test_a_review_that_finds_a_failing_unit_offers_the_report_for_approval(app, monkeypatch):
    admin = admin_client(app)
    entry, _ = warm_alert(admin, history=100)
    ai_review(app, monkeypatch)
    offers = admin.post("/api/maintenance/summary", json={"hours": 6}).json()["report_offers"]
    assert [(o["action"], o["audit_id"], o["source"], o["status"]) for o in offers] == [
        ("PREPARE_REPORT", entry["id"], "Maintenance review", "pending")]
    assert not list(Path(app.cfg.reports_dir).glob("*.docx")) if Path(app.cfg.reports_dir).exists() else True   # offered, not made
    # pressing the review again does not queue it twice
    app._maint_cache.clear()
    assert len(admin.post("/api/maintenance/summary", json={"hours": 6}).json()["report_offers"]) == 1
    assert sum(1 for i in admin.get("/api/approvals").json() if i["action"] == "PREPARE_REPORT") == 1
    # the admin approves it: the report is made in their name, quoting the review
    r = admin.post(f"/api/approvals/{offers[0]['id']}/decide", json={"approve": True})
    assert r.status_code == 200 and "saved as INC-" in r.json()["entry"]["actions"][0]
    made = next(e for e in admin.get("/api/audit?limit=500").json() if e["kind"] == "REPORT_GENERATED")
    assert made["params"]["incident"] == entry["id"] and made["params"]["by"].startswith(ADMIN_NAME)
    text = "\n".join(paragraphs(admin.get(f"/api/reports/{made['params']['file']}").content))
    assert "Maintenance review (Gemini · Thermal agent" in text and f"Name: {ADMIN_NAME} ({ADMIN_ID})" in text
    # once it has a report, a later review no longer offers it
    app._maint_cache.clear()
    assert admin.post("/api/maintenance/summary", json={"hours": 6}).json()["report_offers"] == []


@pytest.mark.parametrize("unit", ["no sign of weakening", "not enough data"])
def test_a_review_that_does_not_find_a_failing_unit_offers_nothing(app, monkeypatch, unit):
    admin = admin_client(app)
    warm_alert(admin, history=100)
    ai_review(app, monkeypatch, unit)
    assert admin.post("/api/maintenance/summary", json={"hours": 6}).json()["report_offers"] == []
    assert "PREPARE_REPORT" not in {i["action"] for i in admin.get("/api/approvals").json()}


def test_a_rule_based_review_offers_nothing(app):
    admin = admin_client(app)
    warm_alert(admin, history=100)
    out = admin.post("/api/maintenance/summary", json={"hours": 6}).json()
    assert out["source"] == "rules" and out["report_offers"] == []


def test_only_an_admin_can_approve_the_offered_report(app, monkeypatch):
    admin = admin_client(app)
    warm_alert(admin, history=100)
    ai_review(app, monkeypatch)
    offer = admin.post("/api/maintenance/summary", json={"hours": 6}).json()["report_offers"][0]
    driver = TestClient(app.app)
    driver.post("/api/login", json={"company_id": DRIVER_ID, "code": DRIVER})
    assert driver.post(f"/api/approvals/{offer['id']}/decide", json={"approve": True}).status_code == 403
    assert driver.post("/api/maintenance/summary", json={"hours": 6}).status_code == 403   # nor can a driver run the AI review


def test_the_maintenance_tab_shows_the_approve_button():
    js = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert "This review supports an incident report" in js and "Approve incident report" in js and "reportOffers(ai)" in js
