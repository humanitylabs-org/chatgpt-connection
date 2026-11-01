"""App-owned account controls on the 0.3 gateway; no dashboard credential bridge.

The original rotating chain stays in place as 'chatgpt-subscription'. Added
accounts get independent Connection instances/locks. This module never copies a
credential chain, changes the Hermes model, or retries an ambiguous transport.
"""
from __future__ import annotations
import json
import re
import secrets
import threading
import time
import codex

LEGACY = 'chatgpt-subscription'
VALID_ID = re.compile(r'(?:chatgpt-subscription|acct-[a-f0-9]{24})\Z')


def safe_title(value, fallback):
    if isinstance(value, str) and '@' in value and len(value) <= 254 and not re.search(r'[\x00-\x1f\x7f]', value):
        return value.split('@', 1)[0][:60] or fallback
    return fallback


class Pool:
    def __init__(self, data, factory=codex.Connection):
        self.root = factory(data)
        self.data = self.root.data
        self.factory = factory
        self.mutex = threading.RLock()
        self.entries = {LEGACY: self.root}
        self.pending = None
        self.cooldowns = {}
        self.views = {}

    def __getattr__(self, key):
        # Keep the original gateway bearer, registration lock and atomic writer.
        return getattr(self.root, key)

    @property
    def login(self):
        return self.pending and self.entry(self.pending).login

    @property
    def unsaved(self):
        return next((e.unsaved for e in self.entries.values() if e.unsaved is not None), None)

    def rows(self):
        try:
            rows = json.loads((self.data / 'account-order.json').read_text())
        except FileNotFoundError:
            return [LEGACY]
        except (OSError, ValueError):
            raise codex.Fault('provider_error', 'Account order is unreadable. Restore app-data before continuing.', 503) from None
        if not isinstance(rows, list) or not rows or len(rows) != len(set(str(x) for x in rows)) or any(not isinstance(x, str) or not VALID_ID.fullmatch(x) for x in rows) or LEGACY not in rows:
            raise codex.Fault('provider_error', 'Invalid account order. Restore app-data before continuing.', 503)
        return rows

    def entry(self, ident):
        if not isinstance(ident, str) or not VALID_ID.fullmatch(ident):
            raise codex.Fault('bad_request', 'Unknown account. Refresh the page.', 400)
        with self.mutex:
            if ident not in self.rows():
                raise codex.Fault('bad_request', 'Unknown account. Refresh the page.', 400)
            if ident not in self.entries:
                self.entries[ident] = self.factory(self.data / 'accounts' / ident)
            return self.entries[ident]

    def stored(self):
        states = [self.entry(i).stored().get('status') for i in self.rows()]
        return {'status': 'connected' if 'connected' in states else 'expired' if any(s in ('expired', 'refreshing') for s in states) else 'not_connected'}

    def prefer(self, ident):
        with self.mutex, self.root.lock():
            self.entry(ident)  # Validate exact identity even for broken/cooling accounts.
            self.root.atomic('account-order.json', [ident] + [i for i in self.rows() if i != ident])
        return {'ok': True, 'message': 'This account will be tried first. Your default model is unchanged.'}

    def connect(self, ident=LEGACY, *, add=False):
        with self.mutex:
            active = self.entry(self.pending).login if self.pending else None
            if self.pending and (not active or active.get('until', 0) <= time.monotonic()):
                self.cancel()
            if self.pending:
                raise codex.Fault('busy', 'Finish or cancel the current sign-in first.', 409)
            if add:
                ident = 'acct-' + secrets.token_hex(12)
                with self.root.lock():
                    self.root.atomic('account-order.json', self.rows() + [ident])
            entry = self.entry(ident)
            result = entry.connect()
            self.pending = ident
            return result

    def poll(self, login_id):
        with self.mutex:
            if not self.pending:
                raise codex.Fault('expired', 'This sign-in expired. Press Connect again.', 409)
            ident = self.pending
            result = self.entry(ident).poll(login_id)
            if result.get('status') == 'approved':
                self.pending = None
                self.views.pop(ident, None)
                self.cooldowns.pop(ident, None)
            return result

    def cancel(self):
        with self.mutex:
            if self.pending:
                self.entry(self.pending).cancel()
                self.pending = None

    def disconnect(self, ident=LEGACY):
        with self.mutex:
            self.entry(ident).disconnect()
            self.views.pop(ident, None)
            self.cooldowns.pop(ident, None)
            if self.pending == ident:
                self.pending = None

    def access(self, rejected=None):
        for ident in self.rows():
            if self.entry(ident).stored().get('status') == 'connected':
                return self.entry(ident).access(rejected)
        raise codex.Fault('signed_out', 'Connect ChatGPT in the ChatGPT app.', 401)

    def open(self, method, path, body=None, extra=None, *, pre_dispatch=None):
        last = None
        for ident in self.rows():
            if self.cooldowns.get(ident, 0) > time.time():
                last = codex.Fault('limit', 'ChatGPT is cooling down. Try again shortly.', 429)
                continue
            try:
                return self.entry(ident).open(method, path, body, extra, pre_dispatch=pre_dispatch)
            except codex.Fault as error:
                # Only definitive rejections before any reply/stream is returned.
                if error.code not in ('expired', 'signed_out', 'limit'):
                    raise
                if error.code == 'limit':
                    self.cooldowns[ident] = time.time() + 60
                last = error
        raise last or codex.Fault('signed_out', 'Connect ChatGPT in the ChatGPT app.', 401)

    get = codex.Connection.get
    models = codex.Connection.models
    usage = codex.Connection.usage

    def public(self, integration):
        result = []
        for number, ident in enumerate(self.rows(), 1):
            entry = self.entry(ident)
            error, plan, usage = None, None, None
            try:
                state = entry.stored()
                status = 'expired' if state.get('status') == 'refreshing' else state.get('status', 'not_connected')
                # Identity is a display hint only, never authorization or deduplication.
                claims = codex.claims(state.get('access_token', ''))
                email = claims.get('email') or (claims.get('https://api.openai.com/profile') or {}).get('email')
                name = safe_title(email, 'ChatGPT subscription' if number == 1 else f'ChatGPT subscription {number}')
                if status == 'connected':
                    cached = self.views.get(ident)
                    if cached and time.monotonic() - cached[0] < 30:
                        plan, usage, error = cached[1:]
                    else:
                        try:
                            plan, usage = entry.usage()
                        except codex.Fault as fault:
                            error = fault.code
                        self.views[ident] = (time.monotonic(), plan, usage, error)
                    if error in ('expired', 'signed_out'):
                        status = 'expired'
                    elif error == 'limit':
                        self.cooldowns[ident] = max(self.cooldowns.get(ident, 0), time.time() + 30)
                cooling = self.cooldowns.get(ident, 0) > time.time()
                if usage:
                    cooling = cooling or any(w.get('remainingPercent') == 0 for w in usage.get('limits', []))
                problem = ('ChatGPT sign-in stopped working.' if error in ('expired', 'signed_out') else
                           'Usage is temporarily unavailable.' if error else None)
            except codex.Fault as fault:
                status, name, cooling, problem = 'error', 'ChatGPT subscription', False, str(fault)
            result.append({'id': ident, 'name': name, 'status': status, 'plan': plan, 'usage': usage,
                           'preferred': number == 1, 'cooling_down': cooling, 'problem': problem,
                           'detail': 'Login not safely saved — repair app-data storage and press Test before restarting.' if entry.unsaved is not None else 'This app stores and renews your ChatGPT login. Hermes uses a box-only connection.',
                           'account': '', 'signing_in': self.pending == ident, 'integration': integration})
        return result
