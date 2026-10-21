"""Incident report: the template filler, the data behind it, and who may prepare it."""
import asyncio
import hashlib
import importlib
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from backend import docfill, report
from sim.sim import SECRET

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "templates" / "logistics-incident-report.docx").read_bytes()
ADMIN_ID, ADMIN_NAME, ADMIN = "ADM-1001", "Serine Ng", "admin-code-1234"
DRIVER_ID, DRIVER_NAME, DRIVER = "DRV-20418", "Marcus Lee", "driver-code-5678"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def parts(docx: bytes) -> dict[str, bytes]:
    z = zipfile.ZipFile(io.BytesIO(docx))
    return {n: z.read(n) for n in z.namelist()}


def paragraphs(docx: bytes) -> list[str]:
    """Every paragraph's text, read the way Word would show it (a <w:br/> is a line break)."""
    root = ET.fromstring(parts(docx)["word/document.xml"])          # also proves the XML is well formed
    out = []
    for p in root.iter(W + "p"):
        out.append("".join("\n" if el.tag == W + "br" else (el.text or "") for el in p.iter() if el.tag in (W + "t", W + "br")))
    return out


def jpeg(w=640, h=480, colour=(40, 90, 160)) -> bytes:
    PIL = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    PIL.new("RGB", (w, h), colour).save(buf, "JPEG")
    return buf.getvalue()


DATA = {
    "reportNumber": "INC-20261021-7", "incidentDate": "21 October 2026", "incidentTime": "13:05:09 (UTC+08:00)",
    "incidentLocation": "Shah Alam Depot (GPS 3.0738, 101.5183)",
    "personnel": [{"name": "Marcus Lee (DRV-20418)", "role": "Driver", "contactInfo": "+60 12-345 6789"},
                  {"name": "Serine Ng (ADM-1001)", "role": "Administrator", "contactInfo": "serine@example.com"}],
    "incidentDescription": "A person <was> seen & recorded.\nSecond line.", "incidentType": "Unauthorised person",
    "cargoDetails": "10 packages", "cargoDamageStatus": "Not determined", "hasCargoDamage": True,
    "cargoDamageDetails": "No excursion.", "vehicleDamageStatus": "Not assessed", "hasVehicleDamage": False,
    "vehicleDamageDetails": "", "injuriesStatus": "Not assessed", "hasInjuries": False, "injured": [],
    "incidentCause": "Suspected.", "immediateActions": "13:05:10  Cargo placed under review.\n13:06:00  Admin notified.",
    "reporterName": "Serine Ng (ADM-1001)", "reporterPosition": "Administrator", "reporterContact": "serine@example.com",
    "additionalNotes": "Note one.\nNote two.", "hasPhotoEvidence": False, "photos": [],
    "signatures": [{"name": "Marcus Lee (DRV-20418)", "role": "Driver", "signature": "", "date": ""},
                   {"name": "Serine Ng (ADM-1001)", "role": "Administrator", "signature": "", "date": ""}],
}


# ---- the filler on the real template
def test_every_tag_in_the_template_is_one_the_report_provides():
    assert docfill.tags_in(TEMPLATE) - {"image"} <= set(DATA) | {"name", "role", "contactInfo", "injuryDescription",
                                                                "signature", "date", "photoDescription", "photos"}


def test_filling_the_real_template_leaves_no_tags_and_valid_xml():
    res = docfill.fill(TEMPLATE, DATA)
    text = "\n".join(paragraphs(res.docx))
    assert not re.search(r"\{[#^/%]?\w+\}", text) and res.missing == set()
    for expected in ("Report Number: INC-20261021-7", "Date of Incident: 21 October 2026", "Type of Incident: Unauthorised person",
                     "Name: Serine Ng (ADM-1001)", "Position: Administrator", "Contact: serine@example.com"):
        assert expected in text


def test_table_rows_repeat_once_per_person():
    res = docfill.fill(TEMPLATE, DATA)
    root = ET.fromstring(parts(res.docx)["word/document.xml"])
    rows = [["".join(t.text or "" for t in c.iter(W + "t")) for c in tr.findall(W + "tc")] for tr in root.iter(W + "tr")]
    assert ["Name", "Role", "Contact Info"] in rows
    assert ["Marcus Lee (DRV-20418)", "Driver", "+60 12-345 6789"] in rows
    assert ["Serine Ng (ADM-1001)", "Administrator", "serine@example.com"] in rows
    assert ["Serine Ng (ADM-1001)", "Administrator", "", ""] in rows                # the signature table, blank for a human


def test_sections_follow_their_flags():
    text = "\n".join(paragraphs(docfill.fill(TEMPLATE, DATA).docx))
    assert "Description of Cargo Damage:" in text and "No excursion." in text         # shown
    assert "Description of Vehicle Damage:" not in text and "Injured Parties:" not in text   # hidden
    assert "No photo evidence was submitted." in text                                 # the inverted section
    shown = docfill.fill(TEMPLATE, {**DATA, "hasVehicleDamage": True, "vehicleDamageDetails": "Dented door",
                                    "hasInjuries": True, "injured": [{"name": "A. Person", "injuryDescription": "Bruise"}]})
    text = "\n".join(paragraphs(shown.docx))
    assert "Dented door" in text and "A. Person" in text and "Bruise" in text


def test_line_breaks_and_special_characters_are_safe():
    text = "\n".join(paragraphs(docfill.fill(TEMPLATE, DATA).docx))
    assert "A person <was> seen & recorded.\nSecond line." in text
    assert "13:05:10  Cargo placed under review.\n13:06:00  Admin notified." in text
    evil = docfill.fill(TEMPLATE, {**DATA, "reportNumber": "</w:t></w:p><w:p>injected"})
    assert "Report Number: </w:t></w:p><w:p>injected" in "\n".join(paragraphs(evil.docx))   # shown as text, never as markup


def test_data_the_template_does_not_get_is_reported_not_crashed():
    res = docfill.fill(TEMPLATE, {k: v for k, v in DATA.items() if k != "incidentCause"})
    assert res.missing == {"incidentCause"} and "Cause of Incident" in "\n".join(paragraphs(res.docx))


# ---- tags that Word split across runs, and other shapes of template
def make_docx(body: str) -> bytes:
    ns = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="x"><Default Extension="xml" ContentType="application/xml"/>'
                                          '<Override PartName="/a" ContentType="b"/></Types>')
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {ns}><w:body>{body}<w:sectPr/></w:body></w:document>')
        z.writestr("word/_rels/document.xml.rels", "<Relationships></Relationships>")
    return out.getvalue()


def run(t: str, bold=False) -> str:
    return f'<w:r>{"<w:rPr><w:b/></w:rPr>" if bold else ""}<w:t xml:space="preserve">{t}</w:t></w:r>'


def test_a_tag_split_across_runs_is_put_back_together():
    docx = make_docx(f"<w:p>{run('Hello {na', True)}{run('me} and {#items}{la')}{run('bel}{/items}')}</w:p>")
    res = docfill.fill(docx, {"name": "Ana", "items": [{"label": "A"}, {"label": "B"}]})
    assert paragraphs(res.docx)[0] == "Hello Ana and AB"


def test_nested_sections_and_empty_lists():
    body = "".join(f"<w:p>{run(t)}</w:p>" for t in ("{#a}", "[{n}]", "{#b}", "<{m}>", "{/b}", "{/a}", "end"))
    out = paragraphs(docfill.fill(make_docx(body), {"a": [{"n": 1, "b": [{"m": "x"}, {"m": "y"}]}, {"n": 2, "b": []}]}).docx)
    assert out[:4] == ["[1]", "<x>", "<y>", "[2]"] and out[-1] == "end" and len(out) == 5
    assert paragraphs(docfill.fill(make_docx(body), {"a": []}).docx) == ["end"]


def test_an_unclosed_section_is_an_error():
    with pytest.raises(ValueError, match="never closed"):
        docfill.fill(make_docx(f"<w:p>{run('{#a}')}</w:p><w:p>{run('x')}</w:p>"), {"a": True})


# ---- pictures
def test_pictures_are_embedded_with_their_captions():
    pic1, pic2 = jpeg(640, 480), jpeg(1280, 720, (200, 50, 50))
    data = {**DATA, "hasPhotoEvidence": True, "photos": [
        {"image": docfill.Image(pic1, "a.jpg", "frame one"), "photoDescription": "13:05:00 · person · a.jpg"},
        {"image": docfill.Image(pic2, "b.jpg", "frame two"), "photoDescription": "13:05:30 · person · b.jpg"}]}
    res = docfill.fill(TEMPLATE, data)
    p = parts(res.docx)
    assert res.images == 2 and p["word/media/evidence1.jpeg"] == pic1 and p["word/media/evidence2.jpeg"] == pic2
    xml = p["word/document.xml"].decode()
    assert xml.count("<w:drawing>") == 2 and 'r:embed="rIdImg1"' in xml and 'r:embed="rIdImg2"' in xml
    rels = p["word/_rels/document.xml.rels"].decode()
    assert 'Id="rIdImg1"' in rels and 'Target="media/evidence1.jpeg"' in rels
    assert 'Extension="jpeg"' in p["[Content_Types].xml"].decode()
    text = paragraphs(res.docx)
    assert text.index("13:05:00 · person · a.jpg") < text.index("13:05:30 · person · b.jpg")
    assert "No photo evidence was submitted." not in text
    ET.fromstring(p["word/document.xml"])                                              # still well formed
    cx = [int(m) for m in re.findall(r'<wp:extent cx="(\d+)"', xml)]
    assert max(cx) <= int(docfill.MAX_IMAGE_W_IN * docfill.EMU_PER_INCH) + 1            # fits the page width


def test_image_size_reads_png_and_jpeg_headers():
    PIL = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    PIL.new("RGB", (30, 20)).save(buf, "PNG")
    assert docfill.image_size(buf.getvalue()) == (30, 20) and docfill.image_size(jpeg(64, 48)) == (64, 48)
    with pytest.raises(ValueError):
        docfill.image_size(b"not an image")


# ---- the report for a real alert, through the API
@pytest.fixture()
def app(tmp_path, monkeypatch):
    for k, v in {"COLDTRUST_DB": str(tmp_path / "t.db"), "COLDTRUST_EVIDENCE_DIR": str(tmp_path / "ev"),
                 "COLDTRUST_REPORTS_DIR": str(tmp_path / "reports"),
                 "COLDTRUST_STRICT_TIME": "0", "COLDTRUST_SECRET": SECRET, "COLDTRUST_LLM": "off", "TELEGRAM_BOT_TOKEN": "",
                 "COLDTRUST_DEMO": "1",
                 "COLDTRUST_ADMIN_ID": ADMIN_ID, "COLDTRUST_ADMIN_NAME": ADMIN_NAME, "COLDTRUST_ADMIN_CODE": ADMIN,
                 "COLDTRUST_ADMIN_CONTACT": "serine@example.com",
                 "COLDTRUST_DRIVER_ID": DRIVER_ID, "COLDTRUST_DRIVER_NAME": DRIVER_NAME, "COLDTRUST_DRIVER_CODE": DRIVER,
                 "COLDTRUST_DRIVER_CONTACT": "+60 12-345 6789"}.items():
        monkeypatch.setenv(k, v)
    import backend.agent as a
    import backend.config as c
    importlib.reload(c)
    return importlib.reload(a)


def signed_in(app, role):
    c = TestClient(app.app)
    cid, code = (ADMIN_ID, ADMIN) if role == "admin" else (DRIVER_ID, DRIVER)
    assert c.post("/api/login", json={"company_id": cid, "code": code}).status_code == 200
    return c


KEY = {"X-ColdTrust-Key": SECRET}


def incident(admin, with_picture=True):
    admin.post("/api/vehicle", json={"speed_kmh": 60, "authorised_stop": False, "route_status": "En route"})
    if with_picture:
        import base64
        r = admin.post("/api/evidence", headers=KEY, json={"jpeg_base64": base64.b64encode(jpeg()).decode(), "trigger": "person",
                                                           "device_id": "camera"})
        assert r.status_code == 200, r.text
    e = admin.post("/api/camera/observation", headers=KEY,
                   json={"person_present": True, "persons": 1, "description": "A person is in the cargo hold"}).json()["entry"]
    assert e["kind"] == "SUSPICIOUS_PERSON"
    return e


def docx_of(client, name):
    r = client.get(f"/api/reports/{name}")
    assert r.status_code == 200
    return r.content


def test_the_report_names_whoever_pressed_the_button(app):
    admin = signed_in(app, "admin")
    e = incident(admin)
    r = admin.post(f"/api/incidents/{e['id']}/report", json={"by": "someone else"})
    assert r.status_code == 200, r.text
    info = r.json()
    assert re.fullmatch(r"INC-\d{8}-\d+", info["report_number"]) and info["file"] == info["report_number"] + ".docx"
    blob = docx_of(admin, info["file"])
    assert hashlib.sha256(blob).hexdigest() == info["sha256"]
    text = "\n".join(paragraphs(blob))
    assert f"Name: {ADMIN_NAME} ({ADMIN_ID})" in text and "Position: Administrator" in text
    assert "Contact: serine@example.com" in text and "someone else" not in text         # identity is from the session
    assert not re.search(r"\{[#^/%]?\w+\}", text)
    assert info["missing"] == []


def test_personnel_table_has_the_driver_and_the_admin_with_contacts(app):
    admin = signed_in(app, "admin")
    blob = docx_of(admin, admin.post(f"/api/incidents/{incident(admin)['id']}/report", json={}).json()["file"])
    root = ET.fromstring(parts(blob)["word/document.xml"])
    rows = [["".join(t.text or "" for t in c.iter(W + "t")) for c in tr.findall(W + "tc")] for tr in root.iter(W + "tr")]
    assert [f"{DRIVER_NAME} ({DRIVER_ID})", "Driver", "+60 12-345 6789"] in rows
    assert [f"{ADMIN_NAME} ({ADMIN_ID})", "Administrator", "serine@example.com"] in rows


def test_the_captured_picture_is_embedded_with_its_hash(app):
    admin = signed_in(app, "admin")
    e = incident(admin)
    log = admin.get("/api/audit?limit=500").json()
    ev = next(x for x in log if x["kind"] == "EVIDENCE_CAPTURED")
    info = admin.post(f"/api/incidents/{e['id']}/report", json={}).json()
    assert info["pictures"] == 1
    p = parts(docx_of(admin, info["file"]))
    assert p["word/media/evidence1.jpeg"] == (Path(app.cfg.evidence_dir) / ev["params"]["file"]).read_bytes()
    text = "\n".join(paragraphs(docx_of(admin, info["file"])))
    assert ev["params"]["file"] in text and ev["params"]["sha256"][:16] in text and "WARNING" not in text


def test_a_picture_changed_after_capture_is_flagged(app):
    admin = signed_in(app, "admin")
    e = incident(admin)
    ev = next(x for x in admin.get("/api/audit?limit=500").json() if x["kind"] == "EVIDENCE_CAPTURED")
    (Path(app.cfg.evidence_dir) / ev["params"]["file"]).write_bytes(jpeg(640, 480, (255, 0, 0)))      # someone swapped the file
    text = "\n".join(paragraphs(docx_of(admin, admin.post(f"/api/incidents/{e['id']}/report", json={}).json()["file"])))
    assert "WARNING: the file differs from the SHA-256 logged when it was captured" in text


def test_without_pictures_the_template_says_so(app):
    admin = signed_in(app, "admin")
    info = admin.post(f"/api/incidents/{incident(admin, with_picture=False)['id']}/report", json={}).json()
    assert info["pictures"] == 0
    assert "No photo evidence was submitted." in "\n".join(paragraphs(docx_of(admin, info["file"])))


def test_what_the_system_cannot_know_is_left_to_a_person(app):
    admin = signed_in(app, "admin")
    text = "\n".join(paragraphs(docx_of(admin, admin.post(f"/api/incidents/{incident(admin)['id']}/report", json={}).json()["file"])))
    assert "Not determined: the system cannot see the cargo" in text
    assert "Was there vehicle damage? Not assessed by the system" in text and "Were there injuries? Not assessed" in text
    assert "Description of Vehicle Damage:" not in text and "Injured Parties:" not in text
    assert "AI-assisted draft: a person must review it" in text
    assert re.search(r"GS1 EPCIS event hash IDs linked to this alert: [0-9a-f]{16}…", text)
    assert "rule-based text: no AI wording" in text                                      # the AI is off in this test
    assert "Suspected" not in text.split("Cause of Incident")[0]
    sig = text.split("Signatures")[1]
    assert ADMIN_NAME in sig and DRIVER_NAME in sig                                       # rows to sign, blank signature cells


def test_ai_wording_is_asked_for_only_when_the_report_is_prepared(app, monkeypatch):
    admin = signed_in(app, "admin")
    e = incident(admin)
    asked = []
    monkeypatch.setattr(type(app.explainer), "enabled", property(lambda self: True))
    monkeypatch.setattr(app.explainer, "report_text", lambda ctx: asked.append(ctx["scenario"]) or {
        "incident_description": "A person entered the hold at the stop.", "suspected_cause": None, "by": "Gemini · Incident agent"})
    assert asked == []                                                                    # nothing at alert time
    text = "\n".join(paragraphs(docx_of(admin, admin.post(f"/api/incidents/{e['id']}/report", json={}).json()["file"])))
    assert asked == ["SUSPICIOUS_PERSON"]
    assert "A person entered the hold at the stop." in text
    assert "A person was seen in the cargo hold outside an authorised stop." in text      # the cause stayed rule-based
    assert "description wording was drafted by Gemini · Incident agent" in text and "the cause is rule-based text" in text


def test_an_ai_failure_leaves_the_report_on_rule_text(app, monkeypatch):
    admin = signed_in(app, "admin")
    e = incident(admin)
    monkeypatch.setattr(type(app.explainer), "enabled", property(lambda self: True))
    monkeypatch.setattr(app.explainer, "report_text", lambda ctx: None)
    r = admin.post(f"/api/incidents/{e['id']}/report", json={})
    assert r.status_code == 200
    assert "rule-based text: no AI wording" in "\n".join(paragraphs(docx_of(admin, r.json()["file"])))


def test_demo_data_is_marked(app):
    admin = signed_in(app, "admin")
    e = incident(admin)
    app.store.update_audit(e["id"], {"params": {**e["params"], "mocked": ["temp", "light"]}})
    text = "\n".join(paragraphs(docx_of(admin, admin.post(f"/api/incidents/{e['id']}/report", json={}).json()["file"])))
    assert "DEMO DATA: this alert was raised from mock temp + light readings" in text


def test_the_hash_is_in_the_audit_log_and_a_second_report_is_a_new_file(app):
    admin = signed_in(app, "admin")
    e = incident(admin)
    first = admin.post(f"/api/incidents/{e['id']}/report", json={}).json()
    second = admin.post(f"/api/incidents/{e['id']}/report", json={}).json()
    assert second["file"] == first["report_number"] + "-r2.docx" and second["revision"] == 2
    logged = [x for x in admin.get("/api/audit?limit=500").json() if x["kind"] == "REPORT_GENERATED"]
    assert [x["params"]["sha256"] for x in logged] == [first["sha256"], second["sha256"]]
    assert logged[0]["params"]["by"] == f"{ADMIN_NAME} ({ADMIN_ID}, admin)" and logged[0]["params"]["incident"] == e["id"]
    assert (Path(app.cfg.reports_dir) / first["file"]).is_file()


def test_only_an_admin_can_prepare_or_download_a_report(app):
    admin, driver = signed_in(app, "admin"), signed_in(app, "driver")
    e = incident(admin)
    name = admin.post(f"/api/incidents/{e['id']}/report", json={}).json()["file"]
    assert driver.post(f"/api/incidents/{e['id']}/report", json={}).status_code == 403
    assert driver.get(f"/api/reports/{name}").status_code == 403
    assert TestClient(app.app).get(f"/api/reports/{name}").status_code == 401
    assert admin.get("/api/reports/..%2F..%2F.env").status_code == 404 and admin.get("/api/reports/INC-1.docx").status_code == 404


def test_only_real_alerts_can_be_reported(app):
    admin = signed_in(app, "admin")
    incident(admin)
    assert admin.post("/api/incidents/99999/report", json={}).status_code == 404
    login_entry = next(x for x in admin.get("/api/audit?limit=500").json() if x["kind"] == "LOGIN_OK")
    assert admin.post(f"/api/incidents/{login_entry['id']}/report", json={}).status_code == 422
    photo = next(x for x in admin.get("/api/audit?limit=500").json() if x["kind"] == "EVIDENCE_CAPTURED")
    assert admin.post(f"/api/incidents/{photo['id']}/report", json={}).status_code == 422


def test_a_missing_template_is_reported_not_crashed(app, monkeypatch):
    admin = signed_in(app, "admin")
    e = incident(admin)
    def gone(cfg):
        raise FileNotFoundError("templates/does-not-exist.docx")
    monkeypatch.setattr(report, "template_bytes", gone)
    r = admin.post(f"/api/incidents/{e['id']}/report", json={})
    assert r.status_code == 500 and "template" in r.json()["detail"]


def test_approving_prepare_report_makes_the_approver_the_reporter(app):
    admin = signed_in(app, "admin")
    e = incident(admin)
    asyncio.run(app._propose(e, None))
    wid = next(i for i in admin.get("/api/approvals").json() if i["action"] == "PREPARE_REPORT")["id"]
    r = admin.post(f"/api/approvals/{wid}/decide", json={"approve": True})
    assert r.status_code == 200 and "saved as INC-" in r.json()["entry"]["actions"][0]
    logged = [x for x in admin.get("/api/audit?limit=500").json() if x["kind"] == "REPORT_GENERATED"]
    assert len(logged) == 1 and logged[0]["params"]["by"].startswith(ADMIN_NAME)


def test_the_template_checker_passes_on_the_shipped_template():
    from tools import check_report_template
    assert check_report_template.problems(TEMPLATE) == []
    assert check_report_template.problems(docfill_without(TEMPLATE, "{reportNumber}")) != []


def docfill_without(template: bytes, tag: str) -> bytes:
    p = parts(template)
    p["word/document.xml"] = p["word/document.xml"].replace(tag.encode(), b"")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n, b in p.items():
            z.writestr(n, b)
    return out.getvalue()


def test_characters_that_are_illegal_in_xml_cannot_corrupt_the_report():
    res = docfill.fill(TEMPLATE, {**DATA, "incidentDescription": "Dropped to 0.0\x00C\x0b and ￾ stayed.\nNext line."})
    text = "\n".join(paragraphs(res.docx))                      # paragraphs() parses the XML: it would raise if it were broken
    assert "Dropped to 0.0C and  stayed.\nNext line." in text
