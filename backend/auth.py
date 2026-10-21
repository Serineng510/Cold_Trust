"""Login, roles and who-may-do-what.

Two roles, each with a company ID and a password in `.env`:

    admin   everything (quarantine, release, reassign, vehicle status, mock chips, AI summaries, EPCIS export)
    driver  look at everything, acknowledge alerts, activate the route

You sign in with your company ID and code. A login gives a signed session cookie (HMAC-SHA256, 12 hours, HttpOnly,
SameSite=Strict). The name and ID in the audit log come from the session, never from what a request says. Devices (the camera agent, the simulator) keep using the
device key in the `X-ColdTrust-Key` header and have their own narrow permissions. With no admin code set, the system is in
OPEN mode, exactly as before, and the dashboard shows a warning.

Honest limits: one account per role (not one per person), and a cookie sent over plain HTTP is not encrypted.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any

from starlette.responses import JSONResponse

COOKIE = "ct_session"
MIN_CODE_LEN = 8
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,23}$")
ROLES = ("driver", "admin")

# ---------------------------------------------------------------- who may call what
# (method, path pattern, rule). First match wins. Rules:
#   public  no credentials needed (the page itself, login, the board's mock poll, signed device messages, custody check)
#   device  the device key only (camera agent, simulator, a GPS feed)
#   any     a logged-in driver or admin (the camera agent's device key is also accepted for GET /api/audit)
#   admin   a logged-in admin only
POLICY: list[tuple[str, str, str]] = [
    ("*", r"^/api/(login|logout|me|health)$", "public"),
    ("GET", r"^/api/demo$", "public"),                           # the board polls this; it holds no secrets
    ("POST", r"^/api/(telemetry|event)$", "public"),             # signed with the device key; checked by the engine
    ("GET", r"^/api/custody/[^/]+$", "public"),                  # a receiving scanner checks a package token
    ("POST", r"^/api/(camera/observation|camera/frame|evidence)$", "device"),
    ("POST", r"^/api/vehicle$", "device+admin"),                 # GPS or the demo speed card
    ("GET", r"^/api/audit$", "any+device"),                      # the camera agent reads alerts
    ("POST", r"^/api/demo$", "admin"),
    ("POST", r"^/api/maintenance/summary$", "admin"),
    ("POST", r"^/api/package/[^/]+/(override|reassign)$", "admin"),
    ("POST", r"^/api/approvals/\d+/decide$", "admin"),           # only an admin approves what the agent proposes
    ("POST", r"^/api/incidents/\d+/report$", "admin"),           # the person who presses it is named as the reporter
    ("GET", r"^/api/reports/[^/]+$", "admin"),
    ("GET", r"^/api/epcis(/.*)?$", "admin"),
    ("GET", r"^/(docs|redoc|openapi\.json)(/.*)?$", "admin"),
    ("POST", r"^/api/audit/\d+/ack$", "any"),
    ("POST", r"^/api/vehicle/action$", "any"),                   # admin-only actions are checked inside (maintenance, offline)
    ("GET", r"^/api/(state|readings|maintenance|approvals|config|camera/(stream|latest\.jpg|status)|evidence/[^/]+)$", "any"),
]
ADMIN_ONLY_VEHICLE_ACTIONS = {"maintenance", "offline"}
_COMPILED = [(m, re.compile(p), r) for m, p, r in POLICY]


def rule_for(method: str, path: str) -> str:
    for m, pat, rule in _COMPILED:
        if (m == "*" or m == method) and pat.match(path):
            return rule
    if path.startswith("/api/"):
        return "admin"            # an endpoint nobody listed is closed to everyone but the admin
    return "public"               # the dashboard files themselves hold no secrets and no data


# ---------------------------------------------------------------- sessions (signed, stateless)

def clean_name(name: str) -> str:
    name = re.sub(r"[^\w .'-]", "", (name or "").strip(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", name)[:40].strip()


def clean_id(text: str) -> str:
    """A company ID as typed: letters, digits, dot, dash and underscore only, upper case (adm-1001 and ADM-1001 are the same)."""
    return re.sub(r"[^A-Za-z0-9._-]", "", (text or "").strip()).upper()[:24]


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: str
    code: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Auth:
    def __init__(self, users: list[User], device_secret: str, session_hours: float = 12, session_secret: str = "",
                 clock=time.time):
        self.users = list(users)
        self.device_secret = device_secret
        self.ttl = session_hours * 3600
        self.clock = clock
        self.enabled = bool(self.users)
        self._revoked: dict[str, float] = {}
        self.limiter = LoginLimiter(clock=clock)
        if self.users and not any(u.role == "admin" for u in self.users):
            raise RuntimeError("Refusing to start: a driver account is set but there is no admin account "
                               "(COLDTRUST_ADMIN_ID and COLDTRUST_ADMIN_CODE).")
        for u in self.users:
            if u.role not in ROLES or not ID_RE.match(u.id):
                raise RuntimeError(f"Refusing to start: company ID {u.id!r} must be 3 to 24 characters: letters, digits, "
                                   "dot, dash or underscore.")
            if len(u.code) < MIN_CODE_LEN:
                raise RuntimeError(f"Refusing to start: the password for {u.id} must be at least {MIN_CODE_LEN} characters.")
        if len({u.id for u in self.users}) != len(self.users):
            raise RuntimeError("Refusing to start: the admin and driver company IDs must be different.")
        if len({u.code for u in self.users}) != len(self.users):
            raise RuntimeError("Refusing to start: the admin and driver passwords must be different.")
        basis = "|".join(f"{u.id}:{u.code}" for u in self.users)
        self._key = (session_secret or hashlib.sha256(
            f"coldtrust-session|{basis}|{device_secret}".encode()).hexdigest()).encode()

    @classmethod
    def from_config(cls, cfg, clock=time.time) -> "Auth":
        """Builds the accounts from COLDTRUST_ADMIN_ID / _NAME / _CODE and COLDTRUST_DRIVER_ID / _NAME / _CODE."""
        users = []
        for role, cid, name, code in (("admin", cfg.admin_id, cfg.admin_name, cfg.admin_code),
                                      ("driver", cfg.driver_id, cfg.driver_name, cfg.driver_code)):
            if not (cid or name or code):
                continue                                           # this role is not set up
            prefix = f"COLDTRUST_{role.upper()}"
            if not cid:
                raise RuntimeError(f"Refusing to start: {prefix}_ID is not set (the company ID used to sign in).")
            if not code:
                raise RuntimeError(f"Refusing to start: {prefix}_CODE is not set (it goes with {prefix}_ID).")
            users.append(User(clean_id(cid), clean_name(name) or clean_id(cid), role, code))
        return cls(users, cfg.device_secret, cfg.session_hours, cfg.session_secret, clock)

    # ---- credentials
    def authenticate(self, company_id: str, code: str) -> User | None:
        """The account for this ID and code. Every account is always compared in full, so the time taken does not reveal
        whether the ID existed or how much of the code matched."""
        cid, given, found = clean_id(company_id).encode(), (code or "").encode(), None
        for u in self.users:
            id_ok = hmac.compare_digest(cid, u.id.encode())
            code_ok = hmac.compare_digest(given, u.code.encode())
            if id_ok and code_ok:
                found = u
        return found

    # ---- tokens
    def issue(self, user: User) -> tuple[str, dict[str, Any]]:
        now = self.clock()
        data = {"n": user.name, "u": user.id, "r": user.role, "i": int(now), "e": int(now + self.ttl),
                "j": secrets.token_hex(8)}
        body = _b64(json.dumps(data, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}", data

    def _sign(self, body: str) -> str:
        return hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()

    def parse(self, token: str | None) -> dict[str, Any] | None:
        """The session data, or None if the token is missing, forged, expired or signed out."""
        if not token or token.count(".") != 1:
            return None
        body, sig = token.split(".")
        if not hmac.compare_digest(sig, self._sign(body)):
            return None
        try:
            data = json.loads(_unb64(body))
        except (ValueError, UnicodeDecodeError):
            return None
        now = self.clock()
        if (not isinstance(data, dict) or data.get("e", 0) <= now or data.get("r") not in ROLES or not data.get("u")
                or data.get("j") in self._revoked):
            return None
        return data

    def revoke(self, data: dict[str, Any]) -> None:
        now = self.clock()
        self._revoked = {j: e for j, e in self._revoked.items() if e > now}   # forget tokens that have expired anyway
        self._revoked[data["j"]] = float(data["e"])

    def device_key_ok(self, key: str | None) -> bool:
        return bool(key) and hmac.compare_digest((key or "").encode(), self.device_secret.encode())

    # ---- requests
    def user_from_cookie(self, cookie_header: str | None) -> dict[str, Any] | None:
        if not cookie_header:
            return None
        try:
            jar = SimpleCookie()
            jar.load(cookie_header)
        except Exception:
            return None
        morsel = jar.get(COOKIE)
        return self.parse(morsel.value if morsel else None)

    def decide(self, rule: str, user: dict[str, Any] | None, device: bool) -> tuple[int, str] | None:
        """None = allowed, otherwise (HTTP status, message)."""
        if not self.enabled or rule == "public":
            return None
        role = user["r"] if user else None
        if rule == "device":
            return None if device else (401, "device key required")
        if rule == "device+admin":
            return None if device or role == "admin" else (403 if user else 401, "admin or device key required")
        if rule == "any+device":
            return None if user or device else (401, "login required")
        if rule == "any":
            return None if user else (401, "login required")
        if rule == "admin":
            if role == "admin":
                return None
            return (403, "admin only") if user else (401, "login required")
        return 403, "not allowed"


# ---------------------------------------------------------------- login throttle

class LoginLimiter:
    """5 wrong codes within a minute lock that address out for 5 minutes (even for the right code)."""

    def __init__(self, max_fail: int = 5, window_s: float = 60, lockout_s: float = 300, clock=time.time):
        self.max_fail, self.window_s, self.lockout_s, self.clock = max_fail, window_s, lockout_s, clock
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}

    def retry_after(self, ip: str) -> int:
        until = self._locked.get(ip, 0)
        return max(0, int(until - self.clock() + 0.999)) if until > self.clock() else 0

    def fail(self, ip: str) -> bool:
        """Records a wrong code. True when this one started a lockout."""
        now = self.clock()
        recent = [t for t in self._fails.get(ip, []) if now - t < self.window_s] + [now]
        self._fails[ip] = recent
        if len(recent) >= self.max_fail:
            self._locked[ip] = now + self.lockout_s
            self._fails[ip] = []
            return True
        return False

    def ok(self, ip: str) -> None:
        self._fails.pop(ip, None)


def client_ip(peer: str | None, headers: dict[str, str]) -> str:
    """The caller's address. Behind a tunnel on this same PC (peer is loopback) the real address is in CF-Connecting-IP;
    from anywhere else that header could be forged, so it is ignored."""
    if peer in ("127.0.0.1", "::1", "localhost") and headers.get("cf-connecting-ip"):
        return headers["cf-connecting-ip"].strip()[:64]
    return peer or "unknown"


def cookie_header(token: str, max_age: int, secure: bool) -> str:
    return (f"{COOKIE}={token}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Strict" + ("; Secure" if secure else ""))


def clear_cookie_header(secure: bool) -> str:
    return f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict" + ("; Secure" if secure else "")


# ---------------------------------------------------------------- the gate in front of every request

class AuthMiddleware:
    """Checks every request against POLICY before any endpoint runs (plain ASGI, so streaming video and WebSockets work)."""

    def __init__(self, app, auth: Auth):
        self.app, self.auth = app, auth

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not self.auth.enabled:
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        user = self.auth.user_from_cookie(headers.get("cookie"))
        device = self.auth.device_key_ok(headers.get("x-coldtrust-key"))
        scope.setdefault("state", {})["user"] = user
        scope["state"]["device"] = device
        if scope["type"] == "websocket":
            if user is None:
                return await send({"type": "websocket.close", "code": 4401})
            return await self.app(scope, receive, send)
        denied = self.auth.decide(rule_for(scope["method"], scope["path"]), user, device)
        if denied:
            status, message = denied
            return await JSONResponse({"detail": message}, status_code=status)(scope, receive, send)
        return await self.app(scope, receive, send)
