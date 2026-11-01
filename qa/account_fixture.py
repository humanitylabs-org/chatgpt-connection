"""Disposable candidate UI with fake accounts only; never the production entrypoint.

Run from a separately mounted /qa against candidate code baked into /app.
No provider requests, agent integration, host files, credentials or owner volumes.
"""
import base64
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
sys.path.insert(0, '/app')
import codex
import server
from account_pool import Pool, LEGACY

SECOND = 'acct-' + 'a' * 24
THIRD = 'acct-' + 'b' * 24


def fake_token(email):
    payload = base64.urlsafe_b64encode(json.dumps({'email': email, 'exp': time.time() + 3600}).encode()).decode().rstrip('=')
    return 'FAKE.' + payload + '.NOT-VALID'


class Fixture(codex.Connection):
    def usage(self):
        state = self.stored()
        used = 100 if self.data.name == SECOND else 35
        return state.get('fixture_plan', 'plus'), {'limits': [
            {'id': 'primary_window', 'label': 'Session', 'usedPercent': used, 'remainingPercent': 100 - used, 'resetsAt': '2099-01-01T12:00:00+00:00'},
            {'id': 'secondary_window', 'label': 'Weekly', 'usedPercent': 42, 'remainingPercent': 58, 'resetsAt': '2099-01-07T12:00:00+00:00'}]}
    def connect(self):
        self.login = {'until': time.monotonic() + 900, 'id': 'SYNTHETIC-LOGIN'}
        return {'ok': True, 'login_id': 'SYNTHETIC-LOGIN', 'url': 'https://auth.openai.com/codex/device', 'code': 'FAKE-1234'}
    def poll(self, login_id):
        return {'ok': True, 'status': 'pending'}
    def models(self):
        return [{'id': 'synthetic-model', 'label': 'Synthetic model', 'tier': 'standard', 'abilities': ['text']}]
    def open(self, *args, **kwargs):
        raise codex.Fault('fixture_only', 'Synthetic UI fixture does not call providers.', 503)


class Hosted(server.Handler):
    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path in ('/qa/host.html', '/qa/host.js', '/qa/host.css', '/qa/skins.json'):
            name = path.rsplit('/', 1)[-1]
            kind = 'text/html' if name.endswith('.html') else 'text/javascript' if name.endswith('.js') else 'text/css' if name.endswith('.css') else 'application/json'
            return self.send(200, (Path(__file__).parent / name).read_bytes(), kind)
        return super().do_GET()


def main():
    with tempfile.TemporaryDirectory(prefix='port-fixture-', dir='/data') as data:
        pool = Pool(data, factory=Fixture)
        pool.root.atomic('account-order.json', [LEGACY, SECOND, THIRD])
        for ident, email, plan, state in ((LEGACY, 'alice@example.invalid', 'plus', 'expired'), (SECOND, 'bob@example.invalid', 'plus', 'connected'), (THIRD, 'carol@example.invalid', 'pro', 'connected')):
            pool.entry(ident).atomic('connection.json', {'status': state, 'access_token': fake_token(email), 'expires_at': time.time() + 3600, 'fixture_plan': plan})
        server.OWNER = pool
        server.HERMES_PASSWORD = ''
        # The actual registration code cannot be reached by this synthetic harness.
        server.reconcile = lambda: 'not_configured'
        server.Hermes.configured = classmethod(lambda cls: False)
        http = server.Server(('0.0.0.0', 8801), Hosted)
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=http.shutdown, daemon=True).start())
        print('SYNTHETIC-ONLY preview ready', flush=True)
        try:
            http.serve_forever()
        finally:
            pool.cancel()
            http.server_close()


if __name__ == '__main__':
    main()
