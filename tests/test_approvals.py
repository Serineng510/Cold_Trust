"""Approvals: the agent proposes from a fixed menu, only an admin decides, and nothing runs without that decision."""
import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

from backend import approvals, llm
from backend.seed import seed
from backend.store import Store
from sim.sim import SECRET

ADMIN_ID, ADMIN_NAME, ADMIN = "ADM-1001", "Serine Ng", "admin-code-1234"
DRIVER_ID, DRIVER_NAME, DRIVER = "DRV-20418", "Marcus Lee", "driver-code-5678"
NOW = 1_800_000_000.0


# ---- the menu and the rules that limit it
def pk(status="NORMAL"):
    return {"id": "SHP-1", "status": status}


def test_the_menu_is_fixed():
    assert approvals.ACTIONS == ("QUARANTINE", "DIVERT", "OFFLINE", "PREPARE_REPORT")
    assert set(llm.PROPOSAL_ACTIONS) == set(approvals.ACTIONS)         # the AI's schema offers exactly the same four
    enum = llm.INCIDENT_SCHEMA["properties"]["proposals"]["items"]["properties"]["action"]["enum"]
    assert enum == list(approvals.ACTIONS)
    assert "proposals" not in llm.INCIDENT_SCHEMA["required"]           # an answer without proposals is still used


@pytest.mark.parametrize("scenario, packages, vehicle, expected", [
    ("THEFT_SUSPECTED", [pk("QUARANTINED")], {}, ["OFFLINE", "PREPARE_REPORT"]),          # already quarantined by the rules
    ("SUSPICIOUS_PERSON", [pk("UNDER_REVIEW")], {}, ["QUARANTINE", "OFFLINE", "PREPARE_REPORT"]),
    ("THERMAL_EXCURSION", [pk()], {}, ["QUARANTINE", "DIVERT", "OFFLINE", "PREPARE_REPORT"]),
    ("THERMAL_EXCURSION", [pk()], {"route_status": "Offline"}, ["QUARANTINE", "DIVERT", "PREPARE_REPORT"]),
    ("DOOR_AJAR", [pk()], {}, ["QUARANTINE", "PREPARE_REPORT"]),
    ("AUTHORISED_HANDLING", [pk()], {}, []),                                              # nothing to approve
    ("NORMAL", [pk()], {}, []),
])
def test_rules_decide_which_actions_are_allowed(scenario, packages, vehicle, expected):
    assert approvals.allowed({"scenario": scenario}, packages, vehicle) == expected


def test_only_allowed_menu_actions_survive_cleaning():
    menu = ["QUARANTINE", "PREPARE_REPORT"]
    raw = [{"action": "QUARANTINE", "reason": "  Person   in the hold. "},
           {"action": "DELETE_EVERYTHING", "reason": "x"},                  # not on the menu
           {"action": "OFFLINE", "reason": "not allowed right now"},        # on the menu, but the rules did not allow it
           {"action": "QUARANTINE", "reason": "duplicate"},
           {"action": "PREPARE_REPORT", "reason": ""},                      # no reason
           "rm -rf /", None, {"reason": "no action"}]
    assert approvals.clean(raw, menu) == [{"action": "QUARANTINE", "reason": "Person in the hold."}]
    assert approvals.clean("not a list", menu) == []
    assert len(approvals.clean([{"action": "QUARANTINE", "reason": "r" * 900}], menu)[0]["reason"]) == approvals.MAX_REASON


def test_rule_proposals_are_limited_to_the_allowed_menu():
    out = approvals.rule_proposals({"scenario": "THEFT_SUSPECTED"}, ["PREPARE_REPORT"])
    assert [p["action"] for p in out] == ["PREPARE_REPORT"]


# ---- the queue
@pytest.fixture()
def queue():
    store = Store(":memory:")
    seed(store)
    return approvals.Queue(store)


ENTRY = {"id": 7, "scenario": "SUSPICIOUS_PERSON", "kind": "SUSPICIOUS_PERSON"}
PROPS = [{"action": "QUARANTINE", "reason": "A person is in the hold."}, {"action": "PREPARE_REPORT", "reason": "Record it."}]


def test_a_proposal_waits_until_someone_decides(queue):
    added = queue.add(ENTRY, PROPS, "rules", NOW)
    assert [i["status"] for i in added] == ["pending", "pending"] and added[0]["expires"] == NOW + approvals.EXPIRY_S
    assert len(queue.pending(NOW + 5)) == 2 and len(queue.for_audit(7, NOW + 5)) == 2
    done = queue.decide(added[0]["id"], True, "Serine Ng (ADM-1001, admin)", NOW + 60)
    assert done["status"] == "approved" and done["decided_by"].startswith("Serine Ng")
    assert queue.decide(added[0]["id"], True, "anyone", NOW + 61) is None        # cannot be decided twice
    assert queue.decide(999, True, "anyone", NOW + 61) is None


def test_the_same_proposal_is_not_raised_twice(queue):
    quarantine = [PROPS[0]]
    queue.add(ENTRY, quarantine, "rules", NOW)
    assert queue.add({**ENTRY, "id": 8}, quarantine, "rules", NOW + 30) == []         # still waiting
    first = queue.pending(NOW)[0]
    queue.decide(first["id"], False, "admin", NOW + 40)
    assert queue.add({**ENTRY, "id": 9}, quarantine, "rules", NOW + 100) == []        # just decided: no nagging
    again = queue.add({**ENTRY, "id": 10}, quarantine, "rules", NOW + 40 + approvals.REPEAT_S + 1)
    assert [i["action"] for i in again] == ["QUARANTINE"]                             # asked again once the situation has lasted


def test_a_report_is_proposed_once_per_event_not_once_overall(queue):
    report = [PROPS[1]]
    assert len(queue.add({**ENTRY, "id": 7}, report, "rules", NOW)) == 1
    assert queue.add({**ENTRY, "id": 7}, report, "rules", NOW + 5) == []              # the same event: no duplicate
    other = queue.add({**ENTRY, "id": 8}, report, "rules", NOW + 10)                  # a different event has its own report
    assert [(i["action"], i["audit_id"]) for i in other] == [("PREPARE_REPORT", 8)]
    assert len(queue.pending(NOW + 20)) == 2


def test_unanswered_proposals_expire(queue):
    item = queue.add(ENTRY, PROPS, "rules", NOW)[0]
    late = NOW + approvals.EXPIRY_S + 1
    assert queue.pending(late) == []
    assert queue.get(item["id"])["status"] == "expired"
    assert queue.decide(item["id"], True, "admin", late) is None                 # an expired proposal cannot be approved


def test_the_queue_keeps_only_the_latest(queue):
    for n in range(approvals.KEEP + 20):
        t = NOW + n * (approvals.REPEAT_S + 1)
        queue.decide(queue.add({**ENTRY, "id": n}, [PROPS[1]], "rules", t)[0]["id"], False, "admin", t)
    assert len(queue.list(NOW + 10 ** 7)) == approvals.KEEP


def test_an_expired_proposal_does_not_hold_back_a_new_one(queue):
    queue.add(ENTRY, PROPS, "rules", NOW)
    later = NOW + approvals.EXPIRY_S + 1
    assert [i["action"] for i in queue.add({**ENTRY, "id": 20}, PROPS, "rules", later)] == ["QUARANTINE", "PREPARE_REPORT"]


# ---- through the API, with login on
@pytest.fixture()
def app(tmp_path, monkeypatch):
    for k, v in {"COLDTRUST_DB": str(tmp_path / "t.db"), "COLDTRUST_EVIDENCE_DIR": str(tmp_path / "ev"),
                 "COLDTRUST_STRICT_TIME": "0", "COLDTRUST_SECRET": SECRET, "COLDTRUST_LLM": "off", "TELEGRAM_BOT_TOKEN": "",
                 "COLDTRUST_DEMO": "1",
                 "COLDTRUST_ADMIN_ID": ADMIN_ID, "COLDTRUST_ADMIN_NAME": ADMIN_NAME, "COLDTRUST_ADMIN_CODE": ADMIN,
                 "COLDTRUST_DRIVER_ID": DRIVER_ID, "COLDTRUST_DRIVER_NAME": DRIVER_NAME, "COLDTRUST_DRIVER_CODE": DRIVER}.items():
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


def raise_alert(app, admin):
    """A person in the hold outside an authorised stop: SUSPICIOUS_PERSON, the cargo goes under review."""
    admin.post("/api/vehicle", json={"speed_kmh": 60, "authorised_stop": False, "route_status": "En route"})   # driving
    r = admin.post("/api/camera/observation", headers={"X-ColdTrust-Key": SECRET},
                   json={"person_present": True, "persons": 1, "behaviours": [], "description": "A person is in the cargo hold"})
    entry = r.json()["entry"]
    assert entry["kind"] == "SUSPICIOUS_PERSON"
    asyncio.run(app._propose(entry, None))             # what _refine does after the AI step (idempotent if it already ran)
    return entry


def pending(client):
    return {i["action"]: i for i in client.get("/api/approvals").json() if i["status"] == "pending"}


def test_an_alert_queues_proposals_but_changes_nothing(app):
    admin = signed_in(app, "admin")
    entry = raise_alert(app, admin)
    waiting = pending(admin)
    assert set(waiting) == {"QUARANTINE", "PREPARE_REPORT"}
    assert waiting["QUARANTINE"]["audit_id"] == entry["id"] and waiting["QUARANTINE"]["source"] == "rules"
    assert {p["status"] for p in admin.get("/api/state").json()["packages"]} == {"UNDER_REVIEW"}     # nothing quarantined yet
    kinds = [e["kind"] for e in admin.get("/api/audit").json()]
    assert kinds.count("APPROVAL_PROPOSED") == 2


def test_a_driver_can_see_the_queue_but_not_decide(app):
    admin, driver = signed_in(app, "admin"), signed_in(app, "driver")
    raise_alert(app, admin)
    assert {i["action"] for i in driver.get("/api/approvals").json()} == {"QUARANTINE", "PREPARE_REPORT"}
    assert {"approvals"} <= set(driver.get("/api/state").json())
    wid = pending(admin)["QUARANTINE"]["id"]
    assert driver.post(f"/api/approvals/{wid}/decide", json={"approve": True}).status_code == 403
    assert TestClient(app.app).post(f"/api/approvals/{wid}/decide", json={"approve": True}).status_code == 401
    assert {p["status"] for p in admin.get("/api/state").json()["packages"]} == {"UNDER_REVIEW"}
    assert wid in {i["id"] for i in pending(admin).values()}                                       # still waiting


def test_approving_quarantine_carries_it_out_and_logs_who(app):
    admin = signed_in(app, "admin")
    raise_alert(app, admin)
    wid = pending(admin)["QUARANTINE"]["id"]
    r = admin.post(f"/api/approvals/{wid}/decide", json={"approve": True, "by": "someone else", "note": "checked the camera"})
    assert r.status_code == 200
    assert r.json()["approval"]["decided_by"] == f"{ADMIN_NAME} ({ADMIN_ID}, admin)"      # identity comes from the session
    packages = admin.get("/api/state").json()["packages"]
    assert {p["status"] for p in packages} == {"QUARANTINED"} and not any(p["token_valid"] for p in packages)
    log = admin.get("/api/audit?limit=500").json()
    decided = [e for e in log if e["kind"] == "APPROVAL_DECIDED"][-1]
    assert "approved" in decided["reasoning"] and "checked the camera" in decided["reasoning"]
    assert "quarantined" in decided["actions"][0]
    assert sum(e["kind"] == "SUPERVISOR_OVERRIDE" for e in log) == len(packages)         # same path as the manual override
    assert admin.post(f"/api/approvals/{wid}/decide", json={"approve": True}).status_code == 409   # not twice


def test_rejecting_changes_nothing_but_is_logged(app):
    admin = signed_in(app, "admin")
    raise_alert(app, admin)
    wid = pending(admin)["QUARANTINE"]["id"]
    assert admin.post(f"/api/approvals/{wid}/decide", json={"approve": False}).json()["approval"]["status"] == "rejected"
    assert {p["status"] for p in admin.get("/api/state").json()["packages"]} == {"UNDER_REVIEW"}
    assert [e for e in admin.get("/api/audit?limit=500").json() if e["kind"] == "APPROVAL_DECIDED"][-1]["actions"] == []


def test_approving_offline_takes_the_vehicle_offline(app):
    admin = signed_in(app, "admin")
    raise_alert(app, admin)
    asyncio.run(app._propose({**admin.get("/api/audit").json()[-1], "scenario": "THEFT_SUSPECTED", "id": 999, "kind": "THEFT_SUSPECTED"}, None))
    oid = pending(admin)["OFFLINE"]["id"]
    admin.post(f"/api/approvals/{oid}/decide", json={"approve": True})
    assert admin.get("/api/state").json()["vehicle"]["route_status"] == "Offline"


def test_approving_divert_sends_one_driver_notice(app, monkeypatch):
    sent = []
    monkeypatch.setattr(app.notifier, "send", lambda text: sent.append(text) or 1)
    monkeypatch.setattr(type(app.notifier), "enabled", property(lambda self: True))
    admin = signed_in(app, "admin")
    entry = {"id": 5, "scenario": "THERMAL_EXCURSION", "kind": "THERMAL_EXCURSION", "ts": 1.0}
    asyncio.run(app._propose(entry, None))
    did = pending(admin)["DIVERT"]["id"]
    r = admin.post(f"/api/approvals/{did}/decide", json={"approve": True})
    assert "Seremban Cold Store" in r.json()["entry"]["actions"][0]
    asyncio.run(asyncio.sleep(0.1))
    assert len(sent) <= 1 and (not sent or "DIVERT" in sent[0] and "Seremban Cold Store" in sent[0])


def test_ai_proposals_are_filtered_through_the_rules(app):
    admin = signed_in(app, "admin")
    entry = {"id": 11, "scenario": "SUSPICIOUS_PERSON", "kind": "SUSPICIOUS_PERSON", "ts": 1.0}
    res = {"explained_by": "Gemini · Incident agent",
           "proposals": [{"action": "QUARANTINE", "reason": "A person is in the hold."},
                         {"action": "WIPE_LOGS", "reason": "cover tracks"},                   # not on the menu: dropped
                         {"action": "DIVERT", "reason": "not allowed for a person alert"}]}   # on the menu, not allowed here
    asyncio.run(app._propose(entry, res))
    waiting = pending(admin)
    assert set(waiting) == {"QUARANTINE"} and waiting["QUARANTINE"]["source"] == "Gemini · Incident agent"


def test_the_ai_failing_still_leaves_the_admin_a_proposal(app):
    admin = signed_in(app, "admin")
    asyncio.run(app._propose({"id": 12, "scenario": "FREEZE_RISK", "kind": "FREEZE_RISK", "ts": 1.0}, None))
    assert "DIVERT" in pending(admin) and pending(admin)["DIVERT"]["source"] == "rules"


def test_alerts_that_need_no_decision_queue_nothing(app):
    admin = signed_in(app, "admin")
    asyncio.run(app._propose({"id": 13, "scenario": "AUTHORISED_HANDLING", "kind": "AUTHORISED_HANDLING", "ts": 1.0}, None))
    assert admin.get("/api/approvals").json() == []


def test_telegram_alert_says_what_is_waiting_for_the_admin(app, monkeypatch):
    sent = []
    monkeypatch.setattr(app.notifier, "send", lambda text: sent.append(text) or 1)
    admin = signed_in(app, "admin")
    entry = raise_alert(app, admin)
    asyncio.run(app._deliver({**entry, "reasoning": "A person is in the hold", "actions": []}))
    assert sent[-1].endswith("Awaiting admin approval: Quarantine the cargo; Prepare the incident report")


def test_the_incident_agent_gets_the_menu_and_the_other_agents_do_not(app):
    # the allowed actions go to the Incident agent only; every number in them must come from the data it was given
    assert llm.agent_for("SUSPICIOUS_PERSON") == "incident" and llm.agent_for("THERMAL_EXCURSION") == "thermal"
    assert "allowed_actions" in llm.AGENTS["incident"]["system"] and "allowed_actions" not in llm.AGENTS["thermal"]["system"]
