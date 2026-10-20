"""Disposable hosted proxy: real app/headers + fake OAuth/Codex, never live accounts."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import signal
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import codex
import server
spec = importlib.util.spec_from_file_location("fixtures", ROOT / "test-chatgpt.py")
f = importlib.util.module_from_spec(spec); spec.loader.exec_module(f)
STATE = {"lock": threading.Lock(), "requests": [], "polls": 1, "refreshes": 0, "refresh": "FAKE-INITIAL-REFRESH", "invalid": False, "mode": "ok", "delta_sent": threading.Event(), "continue": threading.Event()}
Fake = type("QAFake", (f.Fake,), {"state": STATE})


class Hosted(server.Handler):
    def do_GET(self):
        if self.path.split("?")[0] in ("/qa/host.html", "/qa/host.js", "/qa/host.css", "/qa/skins.json"):
            name = self.path.split("?")[0].split("/")[-1]
            kind = "text/html" if name.endswith(".html") else "text/javascript" if name.endswith(".js") else "text/css" if name.endswith(".css") else "application/json"
            return self.send(200, (ROOT / "qa" / name).read_bytes(), kind)
        super().do_GET()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="chatgpt-qa-", dir=os.environ.get("TMPDIR")) as data:
        backend = f.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{backend.server_port}"
        server.OWNER = codex.Connection(data, base, base + "/backend-api")
        server.HERMES_PASSWORD = ""
        page = server.Server(("127.0.0.1", 18841), Hosted)
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=page.shutdown, daemon=True).start())
        print("qa-ready", flush=True)
        try:
            page.serve_forever()
        finally:
            page.server_close(); backend.shutdown(); backend.server_close()
