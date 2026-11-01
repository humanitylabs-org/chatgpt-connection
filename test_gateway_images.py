"""Offline image gateway regressions; fake providers only, no owner credentials."""
import base64
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import codex
import server

spec = importlib.util.spec_from_file_location("chatgpt_contract", Path(__file__).with_name("test-chatgpt.py"))
assert spec is not None and spec.loader is not None
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class ImageFake(contract.Fake):
    def do_POST(self):
        if self.path not in ("/backend-api/codex/images/generations", "/backend-api/codex/images/edits"):
            return contract.Fake.do_POST(self)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.state["requests"].append((self.command, self.path, dict(self.headers), body))
        if self.state["mode"] == "error":
            return self.reply(400, {"error": "PRIVATE-UPSTREAM-ERROR"})
        if self.state["mode"] == "once401" and len(self.state["requests"]) == 1:
            return self.reply(401, {"error": "fixture-expired-access"})
        raw = json.dumps({"data": [{"b64_json": "A" * (codex.MAX_JSON + 1)}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("x-codex-imagegen-request-id", "fixture-image-request")
        self.send_header("Set-Cookie", "private=never-forward")
        self.send_header("Authorization", "never-forward")
        self.send_header("ChatGPT-Account-ID", "never-forward")
        self.end_headers()
        self.wfile.write(raw)


class ImageGatewayTests(contract.AppCase):
    def setUp(self):
        super().setUp()
        self.fake.do_POST = ImageFake.do_POST
        self.seed()

    def image_body(self, edits=False):
        body = {"model": "gpt-image-2", "prompt": "offline fixture", "n": 1,
                "quality": "high", "size": "1024x1024", "background": "opaque"}
        if edits:
            body["images"] = [{"image_url": "data:image/png;base64,YWJj"}]
        return body

    def test_both_routes_forward_json_auth_headers_and_large_response(self):
        with patch.object(self.conn.opener, "open", wraps=self.conn.opener.open) as transport:
            for route in ("generations", "edits"):
                body = self.image_body(route == "edits")
                code, raw, headers = self.call("/v1/images/" + route, body,
                    {**self.auth(), "x-codex-image-turn-id": "fixture-turn", "session_id": "fixture-session",
                     "x-client-request-id": "fixture-client", "ChatGPT-Account-ID": "untrusted",
                     "originator": "untrusted", "X-Untrusted": "do-not-forward"})
                self.assertEqual(code, 200)
                self.assertGreater(len(raw), codex.MAX_JSON)
                self.assertEqual(headers["x-codex-imagegen-request-id"], "fixture-image-request")
                self.assertNotIn("Set-Cookie", headers)
                self.assertNotIn("Authorization", headers)
                self.assertNotIn("ChatGPT-Account-ID", headers)
                method, path, upstream, sent = self.state["requests"][-1]
                upstream = {k.lower(): v for k, v in upstream.items()}
                self.assertEqual((method, path, sent), ("POST", "/backend-api/codex/images/" + route, body))
                self.assertEqual(upstream["x-codex-image-turn-id"], "fixture-turn")
                self.assertEqual(upstream["session_id"], "fixture-session")
                self.assertEqual(upstream["x-client-request-id"], "fixture-client")
                self.assertEqual(upstream["chatgpt-account-id"], "FAKE-ACCOUNT-PRIVATE")
                self.assertEqual(upstream["originator"], "hermes-agent")
                self.assertNotIn("x-untrusted", upstream)
                self.assertNotEqual(upstream["authorization"], self.auth()["Authorization"])
                self.assertEqual(transport.call_args.kwargs["timeout"], 300)
        with patch.object(self.conn.opener, "open", wraps=self.conn.opener.open) as transport:
            self.call("/v1/models", headers=self.auth())
            self.assertEqual(transport.call_args.kwargs["timeout"], 120)

    def test_image_401_retry_retains_turn_id_body_and_timeout(self):
        self.state["mode"] = "once401"
        body = self.image_body(True)
        code, _, _ = self.call("/v1/images/edits", body,
                                {**self.auth(), "x-codex-image-turn-id": "retry-fixture"})
        self.assertEqual(code, 200)
        images = [r for r in self.state["requests"] if r[1].startswith("/backend-api/codex/images/")]
        self.assertEqual(len(images), 2)
        self.assertEqual(self.state["refreshes"], 1)
        for _, path, headers, sent in images:
            self.assertEqual(path, "/backend-api/codex/images/edits")
            self.assertEqual(sent, body)
            self.assertEqual({k.lower(): v for k, v in headers.items()}["x-codex-image-turn-id"], "retry-fixture")

    def test_routes_remain_post_only_and_other_paths_refused(self):
        for route in ("generations", "edits"):
            for method in ("GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"):
                h = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=8)
                h.request(method, "/v1/images/" + route, headers=self.auth())
                r = h.getresponse()
                self.assertEqual(r.status, 404 if method in ("GET", "HEAD") else 501)
                r.read(); h.close()
        for path in ("/v1/images", "/v1/images/variations", "/v1/images/edits/extra", "/v1/images/../responses", "/v1/chat/completions"):
            self.assertEqual(self.call(path, {}, self.auth())[0], 404)
        self.assertEqual(self.state["requests"], [])

    def test_box_only_and_bearer_guards_apply_to_both_routes(self):
        for route in ("generations", "edits"):
            path = "/v1/images/" + route
            self.assertEqual(self.call(path, {}, {})[0], 401)
            self.assertEqual(self.call(path, {}, {"Authorization": "Bearer wrong"})[0], 401)
            for key in ("Origin", "Referer", "Cookie", "Sec-Fetch-Site", "Sec-Fetch-Dest", "Forwarded", "X-Forwarded-For", "CF-Ray"):
                self.assertEqual(self.call(path, {}, {**self.auth(), key: "fixture"})[0], 403)
        self.assertEqual(self.state["requests"], [])

    def test_image_reference_limits_and_large_edit_upload(self):
        body = self.image_body(True)
        body["images"] *= 16
        self.assertEqual(self.call("/v1/images/edits", body, self.auth())[0], 200)
        body["images"] *= 2
        self.assertEqual(self.call("/v1/images/edits", body, self.auth())[0], 413)
        for size, status in ((25 * 1024 * 1024, 200), (25 * 1024 * 1024 + 1, 413)):
            body["images"] = [{"image_url": "data:image/png;base64," + base64.b64encode(b"a" * size).decode()}]
            self.assertEqual(self.call("/v1/images/edits", body, self.auth())[0], status)
        self.assertGreater(server.MAX_IMAGE_BODY, 16 * (25 * 1024 * 1024 * 4 // 3))
        for images in ([{"image_url": "https://example.invalid/private"}], ["bad"], [{"image_url": "data:image/png;base64,?"}], "bad"):
            body["images"] = images
            self.assertEqual(self.call("/v1/images/edits", body, self.auth())[0], 413 if images == "bad" else 400)

    def test_body_and_response_bounds_error_sanitization_and_slot_release(self):
        with patch.object(server, "MAX_IMAGE_BODY", 64):
            self.assertEqual(self.call("/v1/images/generations", self.image_body(), self.auth())[0], 413)
        with patch.object(server, "MAX_IMAGE_RESPONSE", 1024):
            self.assertEqual(self.call("/v1/images/generations", self.image_body(), self.auth())[0], 502)
        self.state["mode"] = "error"
        code, raw, _ = self.call("/v1/images/generations", self.image_body(), self.auth())
        self.assertEqual(code, 400)
        self.assertNotIn(b"PRIVATE-UPSTREAM-ERROR", raw)
        self.state["mode"] = "ok"
        self.assertTrue(server.IMAGE_SLOT.acquire(blocking=False))
        try:
            before = len(self.state["requests"])
            code, raw, headers = self.call("/v1/images/generations", self.image_body(), self.auth())
            self.assertEqual(code, 429)
            self.assertEqual(json.loads(raw)["error"]["detail"], "image_capacity")
            self.assertEqual(headers["Retry-After"], "1")
            self.assertEqual(len(self.state["requests"]), before)
            self.assertEqual(server.GATEWAY_SLOTS.snapshot()["active"], 0)
        finally:
            server.IMAGE_SLOT.release()
        self.assertEqual(self.call("/v1/images/generations", self.image_body(), self.auth())[0], 200)

    def test_real_mcp_front_byte_pipe_accepts_both_image_routes(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        env = {**os.environ, "FRONT_PORT": str(port), "UPSTREAM_HOST": "127.0.0.1", "UPSTREAM_PORT": str(self.srv.server_port)}
        p = subprocess.Popen([sys.executable, "-c", "import asyncio, runpy; asyncio.run(runpy.run_path('mcp/front.py')['main']())"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        old = self.base
        try:
            self.base = "http://127.0.0.1:" + str(port)
            for _ in range(100):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.1):
                        break
                except OSError:
                    time.sleep(.02)
            else:
                self.fail("MCP front did not start")
            for route in ("generations", "edits"):
                code, raw, headers = self.call("/v1/images/" + route, self.image_body(route == "edits"),
                    {**self.auth(), "x-codex-image-turn-id": "front-fixture-turn"})
                self.assertEqual(code, 200)
                self.assertEqual(headers["x-codex-imagegen-request-id"], "fixture-image-request")
        finally:
            self.base = old
            p.terminate(); p.wait(5)


if __name__ == "__main__":
    unittest.main()
