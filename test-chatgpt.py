"""Offline OAuth, rotation, migration and gateway integration tests. No live credentials."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs
from urllib.request import Request, urlopen

import codex
import server


def jwt(exp=None):
    b64 = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
    return b64({"alg": "none"}) + "." + b64({"exp": exp or time.time() + 3600,
        "https://api.openai.com/auth": {"chatgpt_account_id": "FAKE-ACCOUNT-PRIVATE", "chatgpt_plan_type": "pro"}}) + ".fake"


@contextmanager
def serving(s):
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:" + str(s.server_port)
    finally:
        s.shutdown()
        s.server_close()
        t.join(5)


class Fake(BaseHTTPRequestHandler):
    state = None
    def log_message(self, *args):
        pass

    def reply(self, status, obj):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        s = self.state
        s["requests"].append((self.command, self.path, dict(self.headers), None))
        if self.path.startswith("/backend-api/codex/models"):
            return self.reply(200, {"models": [{"slug": "gpt-5.6-sol", "display_name": "GPT 5.6"}]})
        if self.path == "/backend-api/wham/usage":
            return self.reply(200, {"plan_type": "pro", "rate_limit": {"primary_window": {"used_percent": 25, "limit_window_seconds": 18000, "reset_at": 1900000000}}})
        if self.path.endswith("rate-limit-reset-credits"):
            return self.reply(200, {"available": 0})
        self.reply(404, {})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = parse_qs(raw.decode()) if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded") else json.loads(raw or b"{}")
        s = self.state
        with s["lock"]:
            s["requests"].append((self.command, self.path, dict(self.headers), body))
        if self.path == "/api/accounts/deviceauth/usercode":
            return self.reply(200, {"user_code": "ABCD-1234", "device_auth_id": "PRIVATE-DEVICE", "interval": "3"})
        if self.path == "/api/accounts/deviceauth/token":
            s["polls"] += 1
            if s["polls"] == 1:
                return self.reply(403, {"error": "authorization_pending"})
            return self.reply(200, {"authorization_code": "PRIVATE-CODE", "code_verifier": "PRIVATE-VERIFIER"})
        if self.path == "/oauth/token":
            assert body["client_id"] == [codex.CLIENT_ID]
            if body["grant_type"] == ["authorization_code"]:
                assert body["redirect_uri"][0].endswith("/deviceauth/callback")
                return self.reply(200, {"access_token": jwt(), "refresh_token": s["refresh"]})
            with s["lock"]:
                s["refreshes"] += 1
                if s["invalid"] or body["refresh_token"] != [s["refresh"]]:
                    return self.reply(400, {"error": "invalid_grant", "detail": jwt()})
                s["refresh"] = "FAKE-ROTATED-" + str(s["refreshes"])
            time.sleep(.04)
            return self.reply(200, {"access_token": jwt(), "refresh_token": s["refresh"]})
        if self.path == "/backend-api/codex/responses/compact":
            return self.reply(200, {"object": "response.compaction", "output": [{"type": "compaction", "encrypted_content": "fake-encrypted"}], "usage": {"input_tokens": 10, "output_tokens": 2}})
        if self.path == "/backend-api/codex/responses":
            if s["mode"] == "error":
                return self.reply(400, {"error": jwt(), "refresh_token": s["refresh"]})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Set-Cookie", "private=never-forward")
            self.send_header("x-codex-primary-used-percent", "25")
            self.end_headers()
            events = [{"type": "response.created", "response": {"id": "resp_fake", "object": "response", "status": "in_progress", "output": []}},
                      {"type": "response.output_text.delta", "delta": "hello", "item_id": "msg_fake", "output_index": 0, "content_index": 0},
                      {"type": "response.completed", "response": {"id": "resp_fake", "object": "response", "status": "completed", "model": "wrong" if s["mode"] == "mismatch" else body["model"], "output": [{"type": "message", "id": "msg_fake", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": "hello", "annotations": []}]}], "usage": {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5}}}]
            for i, e in enumerate(events):
                if i == 2 and s["mode"] == "missing":
                    break
                line = ("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n").encode()
                self.wfile.write(line)
                self.wfile.flush()
                if i == 1 and s["mode"] == "slow":
                    s["delta_sent"].set()
                    s["continue"].wait(4)
            return
        self.reply(404, {})


class AppCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = io.StringIO()
        self.out = redirect_stdout(self.log); self.out.__enter__()
        self.err = redirect_stderr(self.log); self.err.__enter__()
        self.state = {"lock": threading.Lock(), "requests": [], "polls": 0, "refreshes": 0,
                      "refresh": "FAKE-INITIAL-REFRESH", "invalid": False, "mode": "ok", "delta_sent": threading.Event(), "continue": threading.Event()}
        self.fake = type("CaseFake", (Fake,), {"state": self.state})
        self.backend = ThreadingHTTPServer(("127.0.0.1", 0), self.fake)
        self.bctx = serving(self.backend); self.upstream = self.bctx.__enter__()
        self.conn = codex.Connection(Path(self.tmp.name) / "app-data", self.upstream, self.upstream + "/backend-api")
        self.settings = patch.multiple(server, OWNER=self.conn, PASSWORD="", HERMES_PASSWORD="")
        self.settings.start()
        self.env = patch.dict(os.environ, {"WIZARD_GRANTS_FILE": ""})
        self.env.start()
        self.srv = server.Server(("127.0.0.1", 0), server.Handler)
        self.sctx = serving(self.srv); self.base = self.sctx.__enter__()

    def tearDown(self):
        self.state["continue"].set()
        self.sctx.__exit__(None, None, None); self.bctx.__exit__(None, None, None)
        self.settings.stop(); self.env.stop()
        self.err.__exit__(None, None, None); self.out.__exit__(None, None, None)
        for secret in (jwt(), self.state["refresh"], "PRIVATE-DEVICE", "PRIVATE-VERIFIER", "FAKE-ACCOUNT-PRIVATE"):
            self.assertNotIn(secret, self.log.getvalue())
        self.tmp.cleanup()

    def call(self, path, body=None, headers=None):
        req = Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                      headers={"Content-Type": "application/json", **(headers or {})})
        try:
            r = urlopen(req, timeout=8)
        except HTTPError as e:
            r = e
        with r:
            return r.status, r.read(), dict(r.headers)

    def login(self):
        d = self.conn.connect()
        self.assertEqual(self.conn.poll(d["login_id"])["status"], "pending")
        self.conn.login["next"] = 0
        self.assertEqual(self.conn.poll(d["login_id"])["status"], "approved")

    def seed(self, exp=None):
        with self.conn.lock():
            self.conn.atomic("connection.json", {"status": "connected", "access_token": jwt(exp), "refresh_token": self.state["refresh"], "expires_at": exp or time.time() + 3600})

    def auth(self):
        return {"Authorization": "Bearer " + self.conn.gateway_token()}


class OAuthTests(AppCase):
    def test_device_exchange_ui_api_status_and_disconnect(self):
        status, raw, _ = self.call("/api/chatgpt/connect", {}, {"X-Wizard-Request": "1"})
        self.assertEqual(status, 200)
        d = json.loads(raw)
        self.assertNotIn("PRIVATE-DEVICE", raw.decode())
        self.assertEqual(d["url"], "https://auth.openai.com/codex/device")
        self.call("/api/chatgpt/poll", {"login_id": d["login_id"]}, {"X-Wizard-Request": "1"})
        self.conn.login["next"] = 0
        status, raw, _ = self.call("/api/chatgpt/poll", {"login_id": d["login_id"]}, {"X-Wizard-Request": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["status"], "approved")
        status, raw, _ = self.call("/api/connection")
        d = json.loads(raw)
        self.assertEqual((status, d["contract"], d["status"], d["plan"]), (200, "wizard.ai-connection/2", "connected", "pro"))
        self.assertEqual(d["usage"]["limits"][0]["remainingPercent"], 75)
        for secret in (self.conn.stored()["access_token"], self.state["refresh"], "FAKE-ACCOUNT-PRIVATE"):
            self.assertNotIn(secret, raw.decode())
        self.assertEqual(self.call("/api/chatgpt/test", {}, {"X-Wizard-Request": "1"})[0], 200)
        self.assertEqual(self.call("/api/chatgpt/logout", {}, {"X-Wizard-Request": "1"})[0], 200)
        self.assertEqual(self.conn.stored(), {})
        self.assertEqual(self.call("/v1/models", headers=self.auth())[0], 401)

    def test_skew_rotation_atomic_modes_and_singleflight_across_instances(self):
        self.seed(time.time() + 60)
        other = codex.Connection(self.conn.data, self.upstream, self.upstream + "/backend-api")
        with ThreadPoolExecutor(max_workers=12) as pool:
            tokens = list(pool.map(lambda i: (self.conn if i % 2 else other).access(), range(24)))
        self.assertEqual(len(set(tokens)), 1)
        self.assertEqual(self.state["refreshes"], 1)
        self.assertEqual(self.conn.stored()["refresh_token"], "FAKE-ROTATED-1")
        self.assertEqual(self.conn.data.stat().st_mode & 0o777, 0o700)
        for p in self.conn.data.iterdir():
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.conn.data.glob(".pending-*")))

    def test_singleflight_between_independent_processes(self):
        import subprocess
        import sys
        self.seed(time.time() + 30)
        code = "import codex; c=codex.Connection(" + repr(str(self.conn.data)) + "," + repr(self.upstream) + "," + repr(self.upstream + "/backend-api") + "); c.access()"
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent, capture_output=True, timeout=15).returncode, range(6)))
        self.assertEqual(results, [0] * 6)
        self.assertEqual(self.state["refreshes"], 1)
        self.assertEqual(self.conn.stored()["refresh_token"], "FAKE-ROTATED-1")

    def test_invalid_grant_expires_erases_chain_and_no_retries(self):
        self.seed(time.time() - 1); self.state["invalid"] = True
        for _ in range(3):
            with self.assertRaises(codex.Fault) as e:
                self.conn.access()
            self.assertEqual(e.exception.code, "expired")
        self.assertEqual(self.state["refreshes"], 1)
        self.assertEqual(self.conn.stored(), {"status": "expired"})

    def test_cancel_wrong_login_and_access_only_no_refresh(self):
        d = self.conn.connect()
        with self.assertRaises(codex.Fault): self.conn.poll("wrong")
        self.conn.cancel()
        with self.assertRaises(codex.Fault): self.conn.poll(d["login_id"])
        self.seed()
        s = self.conn.stored(); s.pop("refresh_token")
        self.conn.atomic("connection.json", s)
        self.assertEqual(self.conn.access(), s["access_token"])
        s["expires_at"] = 1; self.conn.atomic("connection.json", s)
        with self.assertRaises(codex.Fault): self.conn.access()
        self.assertEqual(self.state["refreshes"], 0)

    def test_stopped_copy_migration_preserves_key_rotates_on_new_box(self):
        self.login()
        self.seed(time.time() - 1)
        target = Path(self.tmp.name) / "new-box" / "app-data"
        shutil.copytree(self.conn.data, target)
        # Old instance stopped: no further call against source store.
        new = codex.Connection(target, self.upstream, self.upstream + "/backend-api")
        self.assertEqual(new.gateway_token(), self.conn.gateway_token())
        self.assertTrue(new.access())
        self.assertEqual(new.stored()["refresh_token"], "FAKE-ROTATED-1")
        self.assertEqual(self.state["refreshes"], 1)

    def test_csrf_page_auth_gateway_ignores_page_password(self):
        self.assertEqual(self.call("/api/chatgpt/connect", {})[0], 403)
        self.assertEqual(self.call("/api/chatgpt/connect", {}, {"Origin": "https://evil.example", "X-Wizard-Request": "1"})[0], 403)
        with patch.object(server, "PASSWORD", "owner-pass"):
            self.assertEqual(self.call("/api/accounts")[0], 401)
            self.assertEqual(self.call("/v1/models", headers={"Authorization": "Basic " + base64.b64encode(b"owner:owner-pass").decode()})[0], 401)


class GatewayTests(AppCase):
    def test_gateway_header_auth_path_refusals_and_secret_error_sanitization(self):
        self.seed()
        self.assertEqual(self.call("/v1/models")[0], 401)
        self.assertEqual(self.call("/v1/models", headers={"Authorization": "Bearer wrong"})[0], 401)
        for k in ("Origin", "Referer", "Cookie", "Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "CF-Ray", "Sec-Fetch-Site", "Via", "Tailscale-User-Login"):
            self.assertEqual(self.call("/v1/models", headers={**self.auth(), k: "browser"})[0], 403, k)
        self.assertEqual(self.call("/v1/evil", {}, self.auth())[0], 404)
        self.state["mode"] = "error"
        status, raw, hdr = self.call("/v1/responses", {"model": "gpt-5.6-sol", "input": []}, self.auth())
        self.assertEqual(status, 400)
        self.assertNotIn(self.state["refresh"], raw.decode())
        self.assertNotIn("eyJ", raw.decode())
        self.assertFalse(any(k.lower() == "set-cookie" for k in hdr))

    def test_models_usage_compaction_and_real_token_injection(self):
        self.seed()
        status, raw, _ = self.call("/v1/models", headers=self.auth())
        self.assertEqual((status, json.loads(raw)["data"][0]["id"]), (200, "gpt-5.6-sol"))
        self.assertEqual(self.call("/v1/api/codex/usage", headers=self.auth())[0], 200)
        self.assertEqual(self.call("/v1/api/codex/rate-limit-reset-credits", headers=self.auth())[0], 200)
        status, raw, _ = self.call("/v1/responses/compact", {"model": "gpt-5.6-sol", "input": []}, self.auth())
        self.assertEqual((status, json.loads(raw)["object"]), (200, "response.compaction"))
        h = {k.lower(): v for k, v in self.state["requests"][-1][2].items()}
        self.assertEqual(h["authorization"], "Bearer " + self.conn.stored()["access_token"])
        self.assertEqual(h["chatgpt-account-id"], "FAKE-ACCOUNT-PRIVATE")
        self.assertEqual(h["originator"], "hermes-agent")
        self.assertNotIn(self.conn.gateway_token(), str(h))

    def test_streaming_flush_normalization_and_safe_headers(self):
        self.seed(); self.state["mode"] = "slow"
        body = {"model": "gpt-5.6-sol", "input": [], "max_tokens": 100, "temperature": .2}
        req = Request(self.base + "/v1/responses", data=json.dumps(body).encode(), headers={**self.auth(), "Content-Type": "application/json"})
        with urlopen(req, timeout=3) as r:
            raw = b""
            while b"response.output_text.delta" not in raw:
                raw += r.readline()
            self.assertFalse(self.state["continue"].is_set())
            self.assertEqual(r.headers["x-codex-primary-used-percent"], "25")
            self.assertIsNone(r.headers.get("Set-Cookie"))
            self.state["continue"].set()
            raw += r.read()
            self.assertIn(b"response.completed", raw)
        data = self.state["requests"][-1][3]
        self.assertTrue(data["stream"]); self.assertFalse(data["store"])
        self.assertNotIn("max_tokens", data); self.assertNotIn("temperature", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
