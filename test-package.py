#!/usr/bin/env python3
"""Offline source-package regression and fresh app-data startup (stdlib only)."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
VERSION = "0.3.0"
BASE = "python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4"


class PackageTests(unittest.TestCase):
    def test_source_recipe(self):
        manifest = (ROOT / "wizard.md").read_text().split("---", 2)[1]
        compose = (ROOT / "runtipi/docker-compose.yml").read_text()
        self.assertIn("  package: source", manifest)
        self.assertEqual(compose.count("image: " + BASE), 3)  # proxy, engine, data-init
        self.assertIn("condition: service_completed_successfully", compose)
        self.assertNotIn("ghcr.io/", compose)
        self.assertNotIn("build:", compose)
        self.assertIn("${APP_SOURCE_DIR}:/app:ro", compose)
        self.assertIn("${APP_DATA_DIR}/data:/data", compose)
        self.assertIn('command: ["python3", "/app/server.py"]', compose)
        self.assertIn('PYTHONDONTWRITEBYTECODE: "1"', compose)
        self.assertIn('APP_DATA_DIR: /data', compose)

    def test_release_versions(self):
        self.assertEqual(json.loads((ROOT / "runtipi/config.json").read_text())["version"], VERSION)
        self.assertIn("version: " + VERSION + "\n", (ROOT / "wizard.md").read_text())
        self.assertIn("APP_VERSION: " + VERSION + "\n", (ROOT / "runtipi/docker-compose.yml").read_text())
        self.assertIn('VERSION = "' + VERSION + '"', (ROOT / "server.py").read_text())
        self.assertNotIn("0.3.0-dev", (ROOT / "codex.py").read_text())

    def test_engine_starts_from_installed_source_with_fresh_app_data(self):
        with tempfile.TemporaryDirectory(prefix="chatgpt-package-") as td:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env = {"PATH": os.defpath, "APP_DATA_DIR": td,
                   "WIZARD_CHATGPT_BIND": "127.0.0.1", "WIZARD_CHATGPT_PORT": str(port),
                   "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
            proc = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=ROOT,
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 5
                while True:
                    try:
                        with urlopen(base + "/health", timeout=1) as reply:
                            health = json.load(reply)
                        break
                    except OSError:
                        if proc.poll() is not None or time.monotonic() > deadline:
                            self.fail("source engine did not become healthy")
                        time.sleep(0.02)
                self.assertEqual({k: health[k] for k in ("status", "version")}, {"status": "ok", "version": VERSION})
                self.assertEqual(health["gateway"], {"active": 0, "waiting": 0, "rejected": 0, "cancelled": 0,
                                                     "capacity": 8, "max_waiters": 32})
                with urlopen(base + "/api/connection", timeout=2) as reply:
                    self.assertEqual(json.load(reply)["status"], "not_connected")
                with urlopen(base + "/", timeout=2) as reply:
                    self.assertIn(b"<title>ChatGPT</title>", reply.read())
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
