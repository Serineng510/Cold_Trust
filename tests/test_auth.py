"""Login, roles, sessions, lockout and who-may-do-what (backend/auth.py). Accounts are a company ID plus a password."""
import importlib
import re
import time
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import auth as authmod
from sim.sim import SECRET, build_payload

ADMIN_ID, ADMIN_NAME, ADMIN = "ADM-1001", "Serine Ng", "admin-code-1234"
DRIVER_ID, DRIVER_NAME, DRIVER = "DRV-20418", "Marcus Lee", "driver-code-5678"
KEY = {"X-ColdTrust-Key": SECRET}
ADMIN_LABEL = f"{ADMIN_NAME} ({ADMIN_ID}, admin)"
DRIVER_LABEL = f"{DRIVER_NAME} ({DRIVER_ID}, driver)"


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


def client_for(app):
    return TestClient(app.app)


def login(c, role="admin", cid=None, code=None):
    r = c.post("/api/login", json={"company_id": cid or (ADMIN_ID if role == "admin" else DRIVER_ID),
                                   "code": code or (ADMIN if role == "admin" else DRIVER)})
    assert r.status_code == 200, r.text
    return r


@pytest.fixture()
def admin(app):
    c = client_for(app)
    login(c, "admin")
    return c


@pytest.fixture()
def driver(app):
    c = client_for(app)
    login(c, "driver")
    return c


@pytest.fixture()
def anon(app):
    return client_for(app)


def user(cid, role, code, name=None):
    return authmod.User(cid, name or cid, role, code)


# ------------------------------------------------------------------ the login itself

def test_login_gives_a_signed_cookie_and_the_right_role(app):
    c = client_for(app)
    r = c.post("/api/login", json={"company_id": "  adm-1001 ", "code": ADMIN})        # case and spaces do not matter
    assert r.json()["id"] == ADMIN_ID and r.json()["name"] == ADMIN_NAME and r.json()["role"] == "admin"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Max-Age=43200" in cookie and "Secure" not in cookie
    assert c.get("/api/me").json() == {"auth": True, "role": "admin", "name": ADMIN_NAME, "id": ADMIN_ID,
                                       "expires": r.json()["expires"]}
    d = client_for(app)
    assert login(d, "driver").json() == {"id": DRIVER_ID, "name": DRIVER_NAME, "role": "driver", "expires": d.get("/api/me").json()["expires"]}


def test_wrong_id_or_code_is_refused_with_the_same_message(app):
    c = client_for(app)
    wrong_code = c.post("/api/login", json={"company_id": ADMIN_ID, "code": "not-the-code"})
    unknown_id = c.post("/api/login", json={"company_id": "ADM-9999", "code": ADMIN})        # right code, no such account
    swapped = c.post("/api/login", json={"company_id": DRIVER_ID, "code": ADMIN})            # one account's ID, the other's code
    assert wrong_code.status_code == unknown_id.status_code == swapped.status_code == 401
    assert wrong_code.json() == unknown_id.json() == swapped.json()                          # nothing tells which part was wrong
    assert c.post("/api/login", json={"company_id": "A", "code": ADMIN}).status_code == 422  # too short
    assert c.post("/api/login", json={"company_id": ADMIN_ID, "code": ""}).status_code == 422
    assert c.get("/api/me").json()["role"] is None


def test_the_accounts_must_be_valid_at_startup():
    secret = "x" * 32
    with pytest.raises(RuntimeError, match="at least 8"):
        authmod.Auth([user("ADM-1001", "admin", "short")], secret)
    with pytest.raises(RuntimeError, match="at least 8"):
        authmod.Auth([user("ADM-1001", "admin", ADMIN), user("DRV-1", "driver", "tiny")], secret)
    with pytest.raises(RuntimeError, match="no admin account"):
        authmod.Auth([user("DRV-20418", "driver", DRIVER)], secret)
    with pytest.raises(RuntimeError, match="company IDs must be different"):
        authmod.Auth([user("ADM-1001", "admin", ADMIN), user("ADM-1001", "driver", DRIVER)], secret)
    with pytest.raises(RuntimeError, match="passwords must be different"):
        authmod.Auth([user("ADM-1001", "admin", ADMIN), user("DRV-20418", "driver", ADMIN)], secret)
    with pytest.raises(RuntimeError, match="company ID"):
        authmod.Auth([user("a b!", "admin", ADMIN)], secret)
    assert authmod.Auth([], secret).enabled is False                                          # open access, as before


def test_from_config_needs_an_id_and_a_code_together():
    base = dict(admin_id="", admin_name="", admin_code="", driver_id="", driver_name="", driver_code="",
                device_secret="x" * 32, session_hours=12, session_secret="")
    assert authmod.Auth.from_config(SimpleNamespace(**base)).enabled is False
    with pytest.raises(RuntimeError, match="COLDTRUST_ADMIN_ID is not set"):
        authmod.Auth.from_config(SimpleNamespace(**{**base, "admin_code": ADMIN}))
    with pytest.raises(RuntimeError, match="COLDTRUST_ADMIN_CODE is not set"):
        authmod.Auth.from_config(SimpleNamespace(**{**base, "admin_id": ADMIN_ID}))
    ok = authmod.Auth.from_config(SimpleNamespace(**{**base, "admin_id": "adm-1001", "admin_code": ADMIN}))
    assert [(u.id, u.name, u.role) for u in ok.users] == [("ADM-1001", "ADM-1001", "admin")]    # name defaults to the ID


def test_a_short_code_stops_the_server_from_starting(tmp_path, monkeypatch):
    monkeypatch.setenv("COLDTRUST_ADMIN_ID", ADMIN_ID)
    monkeypatch.setenv("COLDTRUST_ADMIN_CODE", "short")
    monkeypatch.setenv("COLDTRUST_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("COLDTRUST_STRICT_TIME", "0")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    import backend.agent as a
    import backend.config as c
    importlib.reload(c)
    with pytest.raises(RuntimeError, match="at least 8"):
        importlib.reload(a)


# ------------------------------------------------------------------ who may call what

def test_nothing_is_readable_without_a_login(anon):
    for path in ("/api/state", "/api/audit", "/api/readings", "/api/maintenance", "/api/config", "/api/camera/status",
                 "/api/camera/latest.jpg", "/api/camera/stream", "/api/evidence/1700000000_abcdef012345.jpg", "/api/epcis"):
        assert anon.get(path).status_code == 401, path


def test_public_doors_stay_open(anon):
    assert anon.get("/api/health").json()["auth"] is True
    assert anon.get("/api/demo").status_code == 200                 # the board polls this
    assert anon.get("/").status_code == 200                         # the dashboard page itself (it shows the login screen)
    assert anon.get("/api/custody/unknown").json()["valid"] is False    # a receiving scanner


def test_signed_device_messages_need_no_login(anon):
    base = 1_800_000_000
    p = build_payload(1, base + 60, [{"t": base + i * 5, "temp_c": 3, "hum": 50, "light": 20 + i % 3} for i in range(1, 7)])
    assert anon.post("/api/telemetry", json=p).json()["accepted"] is True
    bad = {**p, "seq": 99, "sig": "0" * 64}
    assert anon.post("/api/telemetry", json=bad).json()["accepted"] is False       # still protected by its signature


ADMIN_ONLY = [
    ("post", "/api/package/SHP-8841/override", {"status": "QUARANTINED", "reason": "test reason"}),
    ("post", "/api/package/SHP-8841/reassign", {"truck": "TRK-2", "reason": "test reason"}),
    ("post", "/api/vehicle/action", {"action": "offline"}),
    ("post", "/api/vehicle/action", {"action": "maintenance"}),
    ("post", "/api/demo", {"mock": True, "temp_c": 5}),
    ("post", "/api/maintenance/summary", {"hours": 24}),
    ("get", "/api/epcis", None),
    ("get", "/api/epcis/verify", None),
    ("get", "/docs", None),
]


@pytest.mark.parametrize("method, path, body", ADMIN_ONLY)
def test_admin_only_actions(admin, driver, anon, method, path, body):
    send = lambda c: getattr(c, method)(path, **({"json": body} if body is not None else {}))
    assert send(anon).status_code == 401
    assert send(driver).status_code == 403
    assert send(admin).status_code == 200


def test_what_a_driver_may_do(driver, anon):
    for path in ("/api/state", "/api/audit", "/api/readings", "/api/maintenance?hours=24", "/api/config", "/api/camera/status"):
        assert driver.get(path).status_code == 200, path
    assert driver.get("/api/camera/latest.jpg").status_code in (200, 404)           # allowed; 404 only means no frame yet
    r = driver.post("/api/vehicle/action", json={"action": "activate"})                # a driver may activate the route
    assert r.status_code == 200 and r.json()["vehicle"]["route_status"] == "En route"
    entry_id = r.json()["entry"]["id"]
    assert driver.post(f"/api/audit/{entry_id}/ack", json={"by": "x"}).status_code == 200   # and acknowledge
    assert driver.post("/api/vehicle", json={"speed_kmh": 50}).status_code == 403            # the demo speed card is admin only
    assert anon.post(f"/api/audit/{entry_id}/ack", json={"by": "x"}).status_code == 401


def test_the_device_key_has_its_own_narrow_permissions(anon, driver, admin):
    assert anon.get("/api/audit", headers=KEY).status_code == 200                    # the camera agent reads alerts
    assert anon.get("/api/state", headers=KEY).status_code == 401                    # but not the rest
    assert anon.post("/api/vehicle", json={"speed_kmh": 30}, headers=KEY).status_code == 200   # GPS / simulator
    assert anon.post("/api/camera/observation", json={"person_present": False}, headers=KEY).status_code == 200
    assert anon.post("/api/camera/observation", json={"person_present": False}).status_code == 401
    assert driver.post("/api/camera/observation", json={"person_present": False}).status_code == 401   # people cannot post as the camera
    assert anon.post("/api/vehicle", json={"speed_kmh": 30}, headers={"X-ColdTrust-Key": "wrong"}).status_code == 401
    assert admin.post("/api/vehicle", json={"speed_kmh": 30}).status_code == 200
    assert anon.post("/api/package/SHP-8841/override", headers=KEY,
                     json={"status": "NORMAL", "reason": "pretending"}).status_code == 401      # the key does not make you an admin


def test_every_endpoint_is_listed_in_the_policy(app):
    """A new endpoint must be classified on purpose, not fall into the default of admin-only."""
    listed = [(m, re.compile(p)) for m, p, _ in authmod.POLICY]
    missing = []
    for route in app.app.routes:
        if isinstance(route, APIRoute) and route.path.startswith(("/api/", "/docs", "/redoc", "/openapi")):
            path = re.sub(r"\{[^}]+\}", "1", route.path)
            for method in route.methods - {"HEAD", "OPTIONS"}:
                if not any((m == "*" or m == method) and pat.match(path) for m, pat in listed):
                    missing.append((method, route.path))
    assert missing == []


def test_unlisted_api_paths_are_closed_to_everyone_but_the_admin():
    assert authmod.rule_for("GET", "/api/brand-new-endpoint") == "admin"
    assert authmod.rule_for("GET", "/index.html") == "public"


# ------------------------------------------------------------------ the name and ID come from the session

def test_the_audit_log_uses_the_logged_in_account_not_the_one_in_the_request(driver, admin):
    entry = driver.post("/api/vehicle/action", json={"action": "activate", "by": "admin"}).json()["entry"]
    assert entry["params"]["by"] == DRIVER_LABEL and DRIVER_LABEL in entry["reasoning"]
    ack = driver.post(f"/api/audit/{entry['id']}/ack", json={"by": "Chief Executive"}).json()["entry"]
    assert ack["params"]["by"] == DRIVER_LABEL
    ovr = admin.post("/api/package/SHP-8841/override",
                     json={"status": "QUARANTINED", "reason": "seal broken", "supervisor": "somebody else"}).json()["entry"]
    assert ADMIN_LABEL in ovr["actions"][0] and "somebody else" not in ovr["actions"][0]


# ------------------------------------------------------------------ sessions cannot be forged, kept or reused

def test_a_tampered_cookie_is_rejected(app, admin):
    token = admin.cookies.get(authmod.COOKIE)
    forged = client_for(app)
    forged.cookies.set(authmod.COOKIE, token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert forged.get("/api/state").status_code == 401
    body, sig = token.split(".")
    swapped = client_for(app)                                           # a driver's session edited to say "admin"
    drv = client_for(app)
    login(drv, "driver")
    dbody, _ = drv.cookies.get(authmod.COOKIE).split(".")
    swapped.cookies.set(authmod.COOKIE, f"{dbody}.{sig}")
    assert swapped.get("/api/state").status_code == 401


def test_a_session_signed_with_another_key_is_rejected(app):
    other = authmod.Auth([user("ADM-9999", "admin", "other-admin-code", "Mallory")], "another-device-secret-0123456789")
    token, _ = other.issue(other.users[0])
    c = client_for(app)
    c.cookies.set(authmod.COOKIE, token)
    assert c.get("/api/state").status_code == 401


def test_sessions_expire_after_12_hours(app, admin):
    assert admin.get("/api/state").status_code == 200
    app.auth.clock = lambda: time.time() + 12 * 3600 + 60
    assert admin.get("/api/state").status_code == 401


def test_signing_out_kills_the_session_everywhere(app, admin):
    stolen = client_for(app)
    stolen.cookies.set(authmod.COOKIE, admin.cookies.get(authmod.COOKIE))
    assert stolen.get("/api/state").status_code == 200
    assert admin.post("/api/logout").status_code == 200
    assert stolen.get("/api/state").status_code == 401                  # the copied cookie no longer works
    assert admin.get("/api/state").status_code == 401


def test_the_live_feed_needs_a_session(app, anon, admin):
    with pytest.raises(WebSocketDisconnect):
        with anon.websocket_connect("/ws"):
            pass
    with admin.websocket_connect("/ws") as ws:
        ws.send_text("ping")


# ------------------------------------------------------------------ guessing the code

def test_five_wrong_codes_lock_the_address_out(app, anon, monkeypatch):
    sent = []
    app.notifier.token, app.notifier.chat_ids = "t", ("1",)
    monkeypatch.setattr(app.notifier, "send", lambda text: sent.append(text) or 1)
    for _ in range(5):
        assert anon.post("/api/login", json={"company_id": ADMIN_ID, "code": "guess-guess"}).status_code == 401
    r = anon.post("/api/login", json={"company_id": ADMIN_ID, "code": ADMIN})        # even the right code is refused now
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    failed = [e for e in anon.get("/api/audit", headers=KEY).json() if e["kind"] == "LOGIN_FAILED"]
    assert len(failed) == 5 and failed[-1]["params"]["locked"] is True and failed[0]["severity"] == "warning"
    assert failed[0]["params"]["company_id"] == ADMIN_ID                              # which ID was attacked is recorded
    assert "guess-guess" not in str(failed)                                           # the wrong code itself is never stored
    time.sleep(0.2)
    assert len(sent) == 1 and "SECURITY" in sent[0]                                   # one message when the lockout starts
    app.auth.limiter.clock = lambda: time.time() + 400                                # the lockout ends after 5 minutes
    assert anon.post("/api/login", json={"company_id": ADMIN_ID, "code": ADMIN}).status_code == 200


def test_a_right_login_clears_the_wrong_ones(app, anon):
    for _ in range(4):
        anon.post("/api/login", json={"company_id": ADMIN_ID, "code": "nope-nope-1"})
    login(anon, "admin")
    for _ in range(4):
        assert anon.post("/api/login", json={"company_id": ADMIN_ID, "code": "nope-nope-1"}).status_code == 401


def test_limiter_and_client_address_rules():
    lim = authmod.LoginLimiter(max_fail=3, window_s=60, lockout_s=100, clock=lambda: 1000.0)
    assert [lim.fail("a"), lim.fail("a"), lim.fail("a")] == [False, False, True]
    assert lim.retry_after("a") == 100 and lim.retry_after("b") == 0
    assert authmod.client_ip("127.0.0.1", {"cf-connecting-ip": "203.0.113.7"}) == "203.0.113.7"   # behind the tunnel on this PC
    assert authmod.client_ip("192.168.1.50", {"cf-connecting-ip": "203.0.113.7"}) == "192.168.1.50"  # forged: ignored


def test_id_and_code_are_both_checked_in_full(app):
    assert app.auth.authenticate(ADMIN_ID, ADMIN).role == "admin"
    assert app.auth.authenticate("drv-20418", DRIVER).role == "driver"
    assert app.auth.authenticate(ADMIN_ID, DRIVER) is None and app.auth.authenticate(DRIVER_ID, ADMIN) is None
    assert app.auth.authenticate("", ADMIN) is None and app.auth.authenticate(ADMIN_ID, ADMIN + "x") is None
    assert authmod.clean_id("  drv-20418 ") == "DRV-20418" and authmod.clean_id("a b<script>") == "ABSCRIPT"


def test_login_events_are_in_the_audit_log(app, admin):
    events = admin.get("/api/audit").json()
    ok = next(e for e in events if e["kind"] == "LOGIN_OK")
    assert ADMIN_ID in ok["reasoning"] and ok["params"]["by"] == ADMIN_LABEL
    admin.post("/api/logout")
    assert "LOGOUT" in [e["kind"] for e in client_for(app).get("/api/audit", headers=KEY).json()]


# ------------------------------------------------------------------ open mode keeps working as before

def test_open_mode_has_no_login(tmp_path, monkeypatch):
    for k, v in {"COLDTRUST_DB": str(tmp_path / "o.db"), "COLDTRUST_STRICT_TIME": "0", "COLDTRUST_SECRET": SECRET,
                 "COLDTRUST_LLM": "off", "TELEGRAM_BOT_TOKEN": ""}.items():
        monkeypatch.setenv(k, v)
    for key in ("ADMIN_ID", "ADMIN_NAME", "ADMIN_CODE", "DRIVER_ID", "DRIVER_NAME", "DRIVER_CODE"):
        monkeypatch.setenv(f"COLDTRUST_{key}", "")          # empty beats whatever the real .env contains
    import backend.agent as a
    import backend.config as c
    importlib.reload(c)
    a = importlib.reload(a)
    c = TestClient(a.app)
    assert c.get("/api/me").json() == {"auth": False, "role": "admin", "name": None}
    assert c.get("/api/state").status_code == 200
    assert c.post("/api/vehicle/action", json={"action": "offline", "by": "typed name"}).json()["entry"]["params"]["by"] == "typed name"
    assert c.post("/api/login", json={"company_id": "ADM-1001", "code": "whatever1"}).status_code == 400
