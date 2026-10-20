"""App-owned Codex OAuth and fixed upstream transport (no Hermes token dependency)."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
BACKEND = "https://chatgpt.com/backend-api"
REFRESH_SKEW = 120  # stock Hermes CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS
MAX_JSON = 4 * 1024 * 1024


class Fault(Exception):
    def __init__(self, code="provider_error", message="ChatGPT request failed.", status=502):
        self.code, self.status = code, status
        super().__init__(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def claims(token):
    try:
        value = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==="))
        return value if isinstance(value, dict) else {}
    except (ValueError, IndexError, TypeError):
        return {}


class Connection:
    def __init__(self, data, issuer=ISSUER, backend=BACKEND):
        self.data = Path(data)
        self.issuer, self.backend = issuer.rstrip("/"), backend.rstrip("/")
        # Test injection is constructor-only: never accepts request-controlled URLs.
        for url in (self.issuer, self.backend):
            u = urlsplit(url)
            if u.scheme != "https" and not (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1")):
                raise ValueError("HTTPS or test loopback required")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.mutex = threading.RLock()
        self.login = None
        self.data.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.data, 0o700)
        with self.lock():
            if not (self.data / "gateway-token.json").exists():
                self.atomic("gateway-token.json", {"token": secrets.token_urlsafe(48)})
            for name in ("connection.json", "gateway-token.json"):
                if (self.data / name).exists():
                    os.chmod(self.data / name, 0o600)

    @contextmanager
    def lock(self):
        # flock coordinates app workers; reread after acquiring, never cache refresh tokens.
        with self.mutex:
            fd = os.open(self.data / ".oauth.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def atomic(self, name, obj):
        fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=self.data)
        try:
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(obj, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.data / name)
            d = os.open(self.data, os.O_DIRECTORY)
            try:
                os.fsync(d)
            finally:
                os.close(d)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def stored(self):
        try:
            return json.loads((self.data / "connection.json").read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise Fault("provider_error", "Saved connection is unreadable; restore app-data or reconnect.", 503) from None

    def gateway_token(self):
        return json.loads((self.data / "gateway-token.json").read_text())["token"]

    def json_request(self, url, body=None, *, form=False):
        data = None if body is None else (urlencode(body).encode() if form else json.dumps(body).encode())
        req = Request(url, data=data, headers={"Accept": "application/json", "User-Agent": "wizard-chatgpt/0.3.0-dev",
                      **({"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"} if data else {})})
        try:
            with self.opener.open(req, timeout=20) as res:
                raw = res.read(MAX_JSON + 1)
                if len(raw) > MAX_JSON:
                    raise ValueError()
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise ValueError()
                return obj
        except HTTPError as e:
            status = e.code
            try:
                error = json.loads(e.read(65536)).get("error")
                invalid = error == "invalid_grant" or isinstance(error, dict) and error.get("code") == "invalid_grant"
            except (ValueError, AttributeError):
                invalid = False
            finally:
                e.close()
            if invalid:
                raise Fault("expired", "ChatGPT login expired. Press Connect again.", 401) from None
            raise Fault("limit" if status == 429 else "provider_error", "ChatGPT sign-in request was rejected.", status) from None
        except (OSError, ValueError):
            raise Fault("unreachable", "ChatGPT is unavailable. Try again shortly.", 503) from None

    def save_tokens(self, result, previous=None):
        access = result.get("access_token")
        refresh = result.get("refresh_token") or (previous or {}).get("refresh_token")
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise Fault(message="ChatGPT did not return renewable credentials.")
        exp = claims(access).get("exp")
        expires = float(exp) if isinstance(exp, (int, float)) else time.time() + float(result.get("expires_in", 3600))
        self.atomic("connection.json", {"status": "connected", "access_token": access, "refresh_token": refresh,
                                       "expires_at": expires, "last_refresh": time.time()})

    def expire(self):
        # Erase the dead rotating chain; reconnect is the only recovery.
        self.atomic("connection.json", {"status": "expired"})

    def access(self, rejected=None):
        with self.lock():
            s = self.stored()
            if s.get("status") != "connected" or not s.get("access_token"):
                code = "expired" if s.get("status") == "expired" else "signed_out"
                raise Fault(code, "Connect ChatGPT in the ChatGPT app.", 401)
            access = s["access_token"]
            if s.get("expires_at", 0) > time.time() + REFRESH_SKEW and (not rejected or access != rejected):
                return access
            # Access-only seed is supported for the read-only acceptance run; never refresh it.
            if not s.get("refresh_token"):
                if s.get("expires_at", 0) > time.time() and not rejected:
                    return access
                self.expire()
                raise Fault("expired", "ChatGPT login expired. Press Connect again.", 401)
            try:
                result = self.json_request(self.issuer + "/oauth/token", {
                    "grant_type": "refresh_token", "refresh_token": s["refresh_token"], "client_id": CLIENT_ID}, form=True)
                self.save_tokens(result, s)
            except Fault as e:
                if e.code == "expired":
                    self.expire()
                raise
            # Network-ambiguous refresh failures are never replayed automatically inside this call.
            return self.stored()["access_token"]

    def connect(self):
        with self.lock():
            d = self.json_request(self.issuer + "/api/accounts/deviceauth/usercode", {"client_id": CLIENT_ID})
            code, device = d.get("user_code", ""), d.get("device_auth_id", "")
            import re
            if not isinstance(device, str) or not device or not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9-]{4,24}", code):
                raise Fault(message="ChatGPT did not provide a valid device code.")
            self.login = {"id": secrets.token_urlsafe(24), "device": device, "code": code,
                          "until": time.monotonic() + 900, "next": 0,
                          "interval": max(3, int(d.get("interval", 5)))}
            return {"ok": True, "login_id": self.login["id"], "code": code,
                    "url": ISSUER + "/codex/device", "expires_in": 900}

    def poll(self, login_id):
        import hmac
        with self.lock():
            s = self.login
            if not s or not hmac.compare_digest(str(login_id).encode(), s["id"].encode()) or time.monotonic() >= s["until"]:
                raise Fault("expired", "This sign-in expired. Press Connect again.", 409)
            if time.monotonic() < s["next"]:
                return {"ok": True, "status": "pending"}
            s["next"] = time.monotonic() + s["interval"]
            try:
                d = self.json_request(self.issuer + "/api/accounts/deviceauth/token", {"device_auth_id": s["device"], "user_code": s["code"]})
            except Fault as e:
                if e.status in (403, 404, 429):
                    if e.status == 429:
                        s["interval"] = min(60, s["interval"] + 5)
                    return {"ok": True, "status": "pending"}
                raise
            if not d.get("authorization_code") or not d.get("code_verifier"):
                raise Fault(message="ChatGPT authorization is incomplete.")
            # Clear before exchange: a single-use authorization code must never be retried.
            self.login = None
            tokens = self.json_request(self.issuer + "/oauth/token", {
                "grant_type": "authorization_code", "code": d["authorization_code"], "code_verifier": d["code_verifier"],
                "redirect_uri": self.issuer + "/deviceauth/callback", "client_id": CLIENT_ID}, form=True)
            self.save_tokens(tokens)
            return {"ok": True, "status": "approved", "message": "Connected. Hermes keeps its current default model."}

    def cancel(self):
        with self.lock():
            self.login = None

    def disconnect(self):
        with self.lock():
            self.login = None
            (self.data / "connection.json").unlink(missing_ok=True)
            fd = os.open(self.data, os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def open(self, method, path, body=None, extra=None):
        access = self.access()
        for attempt in range(2):
            auth = claims(access).get("https://api.openai.com/auth", {})
            headers = {"Authorization": "Bearer " + access, "originator": "hermes-agent", "User-Agent": "HermesAgent/wizard-chatgpt",
                       "Accept": "text/event-stream" if method == "POST" and path == "/codex/responses" else "application/json",
                       "OpenAI-Beta": "responses=experimental"}
            if auth.get("chatgpt_account_id"):
                headers["ChatGPT-Account-ID"] = auth["chatgpt_account_id"]
            residency = auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency")
            if residency:
                headers["x-openai-internal-codex-residency"] = residency
            headers.update(extra or {})
            if body is not None:
                headers["Content-Type"] = "application/json"
            req = Request(self.backend + path, data=None if body is None else json.dumps(body).encode(), method=method, headers=headers)
            try:
                return self.opener.open(req, timeout=120)
            except HTTPError as e:
                status = e.code
                e.close()  # Never return upstream error bodies, headers, cookies or credentials.
                if status == 401 and attempt == 0:
                    access = self.access(rejected=access)
                    continue
                if status == 401:
                    with self.lock():
                        if self.stored().get("access_token") == access:
                            self.expire()
                raise Fault("limit" if status == 429 else "expired" if status == 401 else "provider_error",
                            "ChatGPT rejected the request.", status if status in (400, 401, 403, 429) else 502) from None
            except OSError:
                raise Fault("unreachable", "ChatGPT is unavailable.", 503) from None

    def get(self, path):
        with self.open("GET", path) as r:
            raw = r.read(MAX_JSON + 1)
            try:
                if len(raw) > MAX_JSON:
                    raise ValueError()
                return json.loads(raw)
            except ValueError:
                raise Fault(message="Invalid ChatGPT response.") from None

    def models(self):
        rows = self.get("/codex/models?client_version=99.0.0").get("models", [])
        return [{"id": r["slug"], "label": str(r.get("display_name") or r["slug"]), "tier": "standard",
                 "abilities": ["text", "streaming"]} for r in rows if isinstance(r, dict) and isinstance(r.get("slug"), str) and r["slug"]]

    def usage(self):
        d = self.get("/wham/usage")
        limits = []
        from datetime import datetime, timezone
        for key, label in (("primary_window", "Session"), ("secondary_window", "Weekly")):
            w = (d.get("rate_limit") or {}).get(key) or {}
            if isinstance(w.get("used_percent"), (int, float)):
                label = {18000: "Session", 604800: "Weekly"}.get(w.get("limit_window_seconds"), label)
                used = max(0, min(100, w["used_percent"]))
                reset = w.get("reset_at")
                limits.append({"id": key, "label": label, "usedPercent": used, "remainingPercent": 100 - used,
                               "resetsAt": datetime.fromtimestamp(reset, timezone.utc).isoformat() if isinstance(reset, (int, float)) else None})
        return str(d.get("plan_type") or "")[:40] or None, {"fetchedAt": datetime.now(timezone.utc).isoformat(), "limits": limits, "paid": None}
