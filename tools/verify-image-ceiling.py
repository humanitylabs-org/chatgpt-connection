"""Optional worker-only ceiling probe: all 16 x 25 MiB references over real HTTP.
Run inside the candidate's 4 GiB memory / 0.5 CPU budget. Synthetic data only.
"""
import base64
import http.client
from http.server import ThreadingHTTPServer
import io
import json
import resource
import tempfile
import threading

import codex
import server


class Reply(io.BytesIO):
    status = 200
    headers = {"x-codex-imagegen-request-id": "fixture-ceiling"}


class Transport:
    def open(self, req, timeout):
        assert req.full_url == "https://chatgpt.com/backend-api/codex/images/edits"
        assert timeout == 300
        assert req.data.count(b'data:image/png;base64,') == 16
        assert len(req.data) > 500 * 1024 * 1024
        return Reply(b'{"data":[{"b64_json":"YWJj"}]}')


def main():
    with tempfile.TemporaryDirectory() as root:
        connection = codex.Connection(root)
        connection.access = lambda **_: "FAKE-CEILING-ACCESS"
        connection.opener = Transport()
        server.OWNER = connection
        engine = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=engine.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        try:
            image = base64.b64encode(b"a" * (25 * 1024 * 1024))
            prefix = b'{"prompt":"fixture ceiling","model":"gpt-image-2","images":['
            start = b'{"image_url":"data:image/png;base64,'
            end = b'"}'
            length = len(prefix) + 16 * (len(start) + len(image) + len(end)) + 15 + 2
            assert length < server.MAX_IMAGE_BODY
            h = http.client.HTTPConnection("127.0.0.1", engine.server_port, timeout=300)
            h.putrequest("POST", "/v1/images/edits")
            h.putheader("Authorization", "Bearer " + connection.gateway_token())
            h.putheader("Content-Type", "application/json")
            h.putheader("Content-Length", str(length))
            h.endheaders()
            h.send(prefix)
            for i in range(16):
                if i:
                    h.send(b",")
                h.send(start)
                h.send(image)
                h.send(end)
            h.send(b"]}")
            r = h.getresponse()
            raw = r.read()
            assert r.status == 200, (r.status, raw)
            assert r.headers["x-codex-imagegen-request-id"] == "fixture-ceiling"
            h.close()
            print(json.dumps({"references": 16, "raw_bytes_per_image": 25 * 1024 * 1024,
                              "wire_bytes": length, "status": r.status,
                              "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
        finally:
            engine.shutdown(); engine.server_close(); thread.join(5)


if __name__ == "__main__":
    main()
