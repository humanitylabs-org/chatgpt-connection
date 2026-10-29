"""Real-handler offline concurrency regressions; only loopback synthetic upstreams.

Run: python3 -m unittest discover -v -p 'test_gateway*.py'
The FIFO regression deliberately also runs against the unmodified live revision.
"""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import importlib.util
from pathlib import Path
import socket
import struct
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import codex
import server
_spec = importlib.util.spec_from_file_location("gateway_test_front", Path(__file__).parent / "mcp/front.py")
assert _spec is not None and _spec.loader is not None
front = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(front)


TOKEN = "synthetic-test-bearer-not-a-login"


@contextmanager
def serving(srv):
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": .01})
    thread.start()
    try:
        yield srv.server_port
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(5)
        if thread.is_alive():
            raise AssertionError("fixture server did not stop")


@contextmanager
def proxy(up_port):
    """Exercise the shipped MCP byte-pipe, including client disconnect propagation."""
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    box = {}

    async def start():
        box["srv"] = await asyncio.start_server(front.handle, "127.0.0.1", 0)
        box["port"] = box["srv"].sockets[0].getsockname()[1]
        ready.set()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    with patch.multiple(front, UP_HOST="127.0.0.1", UP_PORT=up_port):
        thread = threading.Thread(target=run)
        thread.start()
        if not ready.wait(3):
            raise AssertionError("proxy did not start")
        try:
            yield box["port"]
        finally:
            async def close():
                box["srv"].close()
                await box["srv"].wait_closed()
            asyncio.run_coroutine_threadsafe(close(), loop).result(3)
            loop.call_soon_threadsafe(loop.stop)
            thread.join(5)
            if thread.is_alive():
                raise AssertionError("proxy did not stop")


class State:
    def __init__(self):
        self.condition = threading.Condition()
        self.gates = {}
        self.requests = []
        self.active = self.maximum = 0
        self.auto = False

    def gate(self, name):
        with self.condition:
            return self.gates.setdefault(name, threading.Event())

    def release_all(self):
        with self.condition:
            self.auto = True
            for gate in self.gates.values():
                gate.set()


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.do_POST()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        data = json.loads(body) if body else None
        name = self.headers.get("x-client-request-id", "metadata")
        state = self.server.state
        with state.condition:
            state.requests.append((name, self.path, data))
            state.active += 1
            state.maximum = max(state.maximum, state.active)
            state.condition.notify_all()
        try:
            if name.startswith("fail"):
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/codex/responses":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("x-codex-primary-used-percent", "25")
                self.send_header("Set-Cookie", "synthetic=must-not-forward")
                self.end_headers()
                self.wfile.write(b'event: response.created\ndata: {"type":"response.created"}\n\n')
                self.wfile.flush()
                gate = state.gate(name)
                if not state.auto and not gate.wait(8):
                    raise AssertionError("synthetic stream hold exceeded fixture deadline")
                if name == "oversize-stream":
                    self.wfile.write(b"x" * (4 * 1024 * 1024 + 1) + b"\n")
                else:
                    self.wfile.write(b'event: response.completed\ndata: {"type":"response.completed"}\n\n')
                self.wfile.flush()
            else:
                obj = ({"models": [{"slug": "synthetic-model", "display_name": "Synthetic"}]}
                       if self.path.startswith("/codex/models") else
                       {"usage": "synthetic-quota-unchanged"} if self.path == "/wham/usage" else
                       {"object": "response.compaction"})
                raw = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with state.condition:
                state.active -= 1
                state.condition.notify_all()


class SyntheticOwner:
    def __init__(self, port):
        self.base = "http://127.0.0.1:" + str(port)

    def gateway_token(self):
        return TOKEN

    def open(self, method, path, body=None, extra=None):
        req = Request(self.base + path, method=method,
                      data=None if body is None else json.dumps(body).encode(),
                      headers=extra or {})
        try:
            return urlopen(req, timeout=5)
        except HTTPError as exc:
            exc.close()
            raise codex.Fault("provider_error", "Synthetic upstream rejected request.", 502) from None


class GatewayAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.state = State()
        backend = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        backend.daemon_threads = False
        backend.state = self.state
        self.backend = serving(backend)
        upstream = self.backend.__enter__()
        settings = {"OWNER": SyntheticOwner(upstream), "PASSWORD": "", "HERMES_PASSWORD": ""}
        # Leave the historical semaphore intact for the negative baseline run.
        if hasattr(server, "GatewayAdmission"):
            settings["GATEWAY_SLOTS"] = server.GatewayAdmission()
        self.settings = patch.multiple(server, **settings)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        fixture_server = type("FixtureServer", (server.Server,), {"request_queue_size": 64, "daemon_threads": False})
        self.app = serving(fixture_server(("127.0.0.1", 0), server.Handler))
        self.port = self.app.__enter__()
        self.pool = ThreadPoolExecutor(max_workers=48)

    def tearDown(self):
        self.state.release_all()
        self.pool.shutdown(wait=True)
        self.app.__exit__(None, None, None)
        self.backend.__exit__(None, None, None)
        if hasattr(server.GATEWAY_SLOTS, "snapshot"):
            self.assertEqual(server.GATEWAY_SLOTS.snapshot()["active"], 0)
            self.assertEqual(server.GATEWAY_SLOTS.snapshot()["waiting"], 0)
        self.assertEqual(self.state.active, 0)
        self.assertLessEqual(self.state.maximum, 8)

    def request(self, name="normal", path="/v1/responses", headers=None, port=None, method=None):
        conn = HTTPConnection("127.0.0.1", port or self.port, timeout=6)
        data = {"model": "synthetic-model", "input": [], "max_tokens": 2, "temperature": .2}
        method = method or ("POST" if path in server.POST_GATEWAY else "GET")
        try:
            conn.request(method, path, body=json.dumps(data) if method == "POST" else None,
                         headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json",
                                  "x-client-request-id": name, **(headers or {})})
            res = conn.getresponse()
            return res.status, res.read(), dict(res.getheaders())
        finally:
            conn.close()

    def until(self, predicate, message, timeout=3):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            threading.Event().wait(.01)
        self.fail(message)

    def arrivals(self):
        with self.state.condition:
            return [r[0] for r in self.state.requests]

    def waiters(self, count):
        self.until(lambda: server.GATEWAY_SLOTS.snapshot()["waiting"] == count,
                   "queue did not reach expected waiting count")

    def hold_eight(self):
        held = [self.pool.submit(self.request, "held-" + str(i)) for i in range(8)]
        self.until(lambda: len(self.arrivals()) == 8, "eight streams did not reach upstream")
        return held

    def test_eight_held_streams_fifo_and_ceiling(self):
        held = self.hold_eight()
        ninth = self.pool.submit(self.request, "ninth")
        # This observable fails for the original immediate-429 handler, before
        # using any candidate-only diagnostics. No arbitrary success sleep.
        if not hasattr(server.GATEWAY_SLOTS, "snapshot"):
            self.until(ninth.done, "baseline did not settle")
            self.assertFalse(ninth.done(), "ninth request was rejected instead of waiting")
        self.waiters(1)
        self.assertFalse(ninth.done())
        tenth = self.pool.submit(self.request, "tenth")
        self.waiters(2)
        self.assertEqual(len(self.arrivals()), 8)
        self.state.gate("held-0").set()
        self.until(lambda: "ninth" in self.arrivals(), "ninth did not dispatch after release")
        self.assertNotIn("tenth", self.arrivals())
        self.state.gate("ninth").set()
        self.until(lambda: "tenth" in self.arrivals(), "tenth did not dispatch in FIFO order")
        self.state.release_all()
        for future in held + [ninth, tenth]:
            code, raw, headers = future.result(3)
            self.assertEqual(code, 200)
            self.assertIn(b"response.completed", raw)
            self.assertEqual(headers["x-codex-primary-used-percent"], "25")
            self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(Counter(self.arrivals()), Counter(["held-" + str(i) for i in range(8)] + ["ninth", "tenth"]))
        self.assertEqual(self.state.maximum, 8)

    def test_default_queue_bound_and_truthful_local_busy(self):
        self.assertEqual(server.GATEWAY_SLOTS.max_waiters, 32)
        self.hold_eight()
        queued = []
        for i in range(32):
            queued.append(self.pool.submit(self.request, "queued-" + str(i)))
            self.waiters(i + 1)
        code, raw, headers = self.request("overflow")
        self.assertEqual(code, 429)
        self.assertEqual(headers["Retry-After"], "1")
        err = json.loads(raw)["error"]
        self.assertEqual((err["code"], err["type"], err["detail"]), ("gateway_busy", "local_capacity", "queue_full"))
        self.assertEqual(len(self.arrivals()), 8)
        self.state.release_all()
        for future in queued:
            self.assertEqual(future.result(5)[0], 200)
        self.assertNotIn("overflow", self.arrivals())
        self.assertEqual(server.GATEWAY_SLOTS.snapshot()["rejected"], 1)

    def test_deadline_no_stale_dispatch_and_next_waiter_recovers(self):
        self.hold_eight()
        start = time.monotonic()
        expired = self.pool.submit(self.request, "expired", "/v1/responses", {"X-Stainless-Timeout": ".65"})
        self.waiters(1)
        survivor = self.pool.submit(self.request, "survivor")
        self.waiters(2)
        code, raw, headers = expired.result(2)
        self.assertEqual(code, 429)
        self.assertEqual(json.loads(raw)["error"]["detail"], "deadline")
        self.assertEqual(headers["Retry-After"], "1")
        self.assertLess(time.monotonic() - start, .65)
        self.waiters(1)
        self.state.gate("held-0").set()
        self.until(lambda: "survivor" in self.arrivals(), "live waiter failed after timeout")
        self.state.release_all()
        self.assertEqual(survivor.result(3)[0], 200)
        self.assertNotIn("expired", self.arrivals())

    def raw_request(self, name, port=None, extra="", length=None, body=b"{}"):
        sock = socket.create_connection(("127.0.0.1", port or self.port), timeout=3)
        raw = ("POST /v1/responses HTTP/1.1\r\nHost: local\r\nAuthorization: Bearer " + TOKEN +
               "\r\nContent-Type: application/json\r\nx-client-request-id: " + name +
               "\r\nContent-Length: " + str(len(body) if length is None else length) + "\r\n" + extra + "\r\n").encode() + body
        sock.sendall(raw)
        return sock

    def test_disconnected_queued_body_never_dispatches_fin_and_reset(self):
        self.hold_eight()
        for mode in ("fin", "reset"):
            sock = self.raw_request(mode)
            self.waiters(1)
            if mode == "reset":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            sock.close()
            self.waiters(0)
            self.assertNotIn(mode, self.arrivals())
        self.assertEqual(server.GATEWAY_SLOTS.snapshot()["cancelled"], 2)
        self.state.release_all()
        self.assertEqual(self.request("recovered")[0], 200)

    def test_shipped_front_propagates_queued_cancellation(self):
        self.hold_eight()
        with proxy(self.port) as port:
            sock = self.raw_request("proxy-cancelled", port=port)
            self.waiters(1)
            sock.close()
            self.waiters(0)
            self.assertNotIn("proxy-cancelled", self.arrivals())
            self.state.release_all()
            self.assertEqual(self.request("proxy-recovered", port=port)[0], 200)

    def test_body_upload_has_absolute_dispatch_deadline(self):
        with patch.object(server, "GATEWAY_WAIT_SECONDS", .25):
            sock = self.raw_request("slow-upload", length=100, body=b"{")
            try:
                # Keep feeding bytes sooner than the original socket timeout;
                # no chunk is allowed to reset the absolute dispatch deadline.
                for _ in range(5):
                    threading.Event().wait(.06)
                    try:
                        sock.sendall(b" ")
                    except OSError:
                        break
                raw = sock.recv(65536)
                self.assertIn(b"429", raw)
                while b"gateway_busy" not in raw:
                    part = sock.recv(65536)
                    if not part:
                        break
                    raw += part
                self.assertIn(b"gateway_busy", raw)
            finally:
                sock.close()
        self.assertNotIn("slow-upload", self.arrivals())
        self.state.release_all()
        self.assertEqual(self.request("upload-recovered")[0], 200)

    def test_upstream_failure_and_repeated_waves_release_every_permit(self):
        self.state.release_all()
        self.assertEqual(self.request("fail-before-stream")[0], 502)
        for wave in range(3):
            futures = [self.pool.submit(self.request, "wave-%d-%d" % (wave, i)) for i in range(16)]
            self.assertEqual([f.result(5)[0] for f in futures], [200] * 16)
        self.until(lambda: server.GATEWAY_SLOTS.snapshot()["active"] == 0, "permit leak")
        self.assertEqual(len(self.arrivals()), 49)

    def test_started_stream_error_and_active_disconnect_release_permits(self):
        self.state.release_all()
        code, raw, _ = self.request("oversize-stream")
        self.assertEqual(code, 200)  # headers already flushed: in-band error, not a new HTTP status
        self.assertIn(b"event: error", raw)
        self.assertIn(b"exceeded its limit", raw)
        self.state.auto = False
        sock = self.raw_request("active-reset")
        self.until(lambda: "active-reset" in self.arrivals(), "active request did not dispatch")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()
        self.state.gate("active-reset").set()
        self.until(lambda: server.GATEWAY_SLOTS.snapshot()["active"] == 0, "active disconnect leaked permit")
        self.state.release_all()
        self.assertEqual(self.request("after-active-reset")[0], 200)

    def test_get_wait_budget_and_security_checks_precede_queue(self):
        self.hold_eight()
        self.assertEqual(self.request(headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.request(headers={"X-Forwarded-For": "untrusted"})[0], 403)
        self.assertEqual(server.GATEWAY_SLOTS.snapshot()["waiting"], 0)
        with patch.object(server, "GATEWAY_GET_WAIT_SECONDS", .2):
            code, raw, headers = self.request("quota-expired", "/v1/api/codex/usage")
        self.assertEqual(code, 429)
        self.assertEqual(json.loads(raw)["error"]["detail"], "deadline")
        self.assertEqual(headers["Retry-After"], "1")
        self.state.release_all()
        self.assertNotIn("quota-expired", self.arrivals())

    def test_models_quota_compaction_and_security_limits_unchanged(self):
        self.state.release_all()
        code, raw, _ = self.request(path="/v1/models")
        self.assertEqual(code, 200)
        data = json.loads(raw)
        self.assertEqual(data["models"][0]["slug"], "synthetic-model")
        self.assertEqual(data["data"][0]["id"], "synthetic-model")
        code, raw, _ = self.request(path="/v1/api/codex/usage")
        self.assertEqual((code, json.loads(raw)), (200, {"usage": "synthetic-quota-unchanged"}))
        self.assertEqual(self.request(path="/v1/responses/compact")[0], 200)
        before = len(self.arrivals())
        self.assertEqual(self.request(headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.request(headers={"Origin": "https://untrusted.invalid"})[0], 403)
        self.assertEqual(self.request(path="/v1/unsupported")[0], 404)
        sock = self.raw_request("oversized", length=16 * 1024 * 1024 + 1, body=b"")
        try:
            self.assertIn(b"413", sock.recv(65536))
        finally:
            sock.close()
        self.assertEqual(len(self.arrivals()), before)
        _, raw, _ = self.request(path="/health")
        self.assertEqual(set(json.loads(raw)["gateway"]), {"active", "waiting", "rejected", "cancelled", "capacity", "max_waiters"})
        self.assertNotIn(TOKEN.encode(), raw)


if __name__ == "__main__":
    unittest.main()
