"""Regression tests for hand-ported controls. All credentials are synthetic."""
import base64
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import codex
from account_pool import Pool, LEGACY

SECOND = 'acct-' + 'a' * 24
THIRD = 'acct-' + 'b' * 24


def token(email):
    payload = base64.urlsafe_b64encode(json.dumps({'email': email, 'exp': time.time() + 3600}).encode()).decode().rstrip('=')
    return 'FAKE.' + payload + '.NOT-A-SIGNATURE'


class Entry(codex.Connection):
    sends = []
    def usage(self):
        state = self.stored()
        if state.get('fixture_fault'):
            raise codex.Fault(state['fixture_fault'], 'Synthetic rejection.', 401)
        return state.get('fixture_plan', 'plus'), {'limits': [{'label': 'Session', 'usedPercent': 25, 'remainingPercent': 75, 'resetsAt': '2099-01-01T00:00:00+00:00'}]}
    def open(self, method, path, body=None, extra=None, *, pre_dispatch=None):
        state = self.stored()
        if state.get('status') != 'connected':
            raise codex.Fault('signed_out', 'Synthetic signed-out account.', 401)
        if state.get('fixture_fault'):
            raise codex.Fault(state['fixture_fault'], 'Synthetic rejection.', 429)
        if pre_dispatch:
            pre_dispatch(False)
        self.sends.append(self.data)
        return io.BytesIO(b'{"models": [{"slug": "synthetic-model"}]}')
    def connect(self):
        self.login = {'until': time.monotonic() + 300, 'id': 'FAKE-LOGIN'}
        return {'ok': True, 'login_id': 'FAKE-LOGIN', 'url': 'https://auth.openai.com/codex/device', 'code': 'FAKE-ONLY'}
    def poll(self, login_id):
        self.atomic('connection.json', {'status': 'connected', 'access_token': token('renewed@example.invalid'), 'expires_at': time.time() + 3600})
        self.login = None
        return {'ok': True, 'status': 'approved'}


class Controls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pool = Pool(self.tmp.name, factory=Entry)
        self.pool.root.atomic('account-order.json', [LEGACY, SECOND, THIRD])
        self.seed(LEGACY, 'alice@example.invalid', 'plus')
        self.seed(SECOND, 'bob@example.invalid', 'pro')
        self.seed(THIRD, 'carol@example.invalid', 'team')
        Entry.sends = []
    def tearDown(self):
        self.pool.cancel()
        self.tmp.cleanup()
    def seed(self, ident, email, plan):
        self.pool.entry(ident).atomic('connection.json', {'status': 'connected', 'access_token': token(email), 'refresh_token': 'FAKE-REFRESH-NEVER-REAL', 'expires_at': time.time() + 3600, 'fixture_plan': plan})
    def test_legacy_chain_and_gateway_bearer_are_adopted_in_place(self):
        before = (self.pool.data / 'connection.json').read_bytes()
        gateway = self.pool.gateway_token()
        self.pool.prefer(SECOND)
        restarted = Pool(self.tmp.name, factory=Entry)
        self.assertEqual(restarted.gateway_token(), gateway)
        self.assertEqual((restarted.data / 'connection.json').read_bytes(), before)
        self.assertEqual(restarted.entry(LEGACY).data, restarted.data)
        self.assertNotEqual(restarted.entry(SECOND).data, restarted.data)
    def test_use_first_persists_for_every_account_and_selects_next_gateway_send(self):
        for ident in (SECOND, THIRD, LEGACY):
            self.pool.prefer(ident)
            restarted = Pool(self.tmp.name, factory=Entry)
            self.assertEqual(restarted.rows()[0], ident)
            with restarted.open('GET', '/codex/models'):
                pass
            self.assertEqual(Entry.sends[-1], restarted.entry(ident).data)
    def test_use_first_accepts_broken_and_cooling_accounts_without_touching_credentials(self):
        self.pool.entry(SECOND).atomic('connection.json', {'status': 'expired'})
        self.pool.cooldowns[THIRD] = time.time() + 60
        for ident in (SECOND, THIRD):
            self.assertTrue(self.pool.prefer(ident)['ok'])
            self.assertEqual(self.pool.rows()[0], ident)
        self.assertEqual(self.pool.entry(SECOND).stored(), {'status': 'expired'})
    def test_readable_titles_and_multiple_real_plan_values_and_limit_display(self):
        rows = self.pool.public('registered')
        self.assertEqual([r['name'] for r in rows], ['alice', 'bob', 'carol'])
        self.assertEqual([r['plan'] for r in rows], ['plus', 'pro', 'team'])
        self.assertEqual(rows[0]['usage']['limits'][0]['usedPercent'], 25)
        text = json.dumps(rows)
        self.assertNotIn('FAKE-REFRESH', text)
        self.assertNotIn('NOT-A-SIGNATURE', text)
        self.assertNotIn('access_token', text)
        self.assertNotIn('gateway-token', text)
    def test_broken_signin_is_reported_for_row_reconnect_not_hidden_as_connected(self):
        self.pool.entry(SECOND).atomic('connection.json', {'status': 'expired'})
        row = self.pool.public('registered')[1]
        self.assertEqual(row['status'], 'expired')
        self.pool.connect(SECOND)
        self.assertTrue(self.pool.poll('FAKE-LOGIN')['ok'])
        self.assertEqual(self.pool.entry(SECOND).stored()['status'], 'connected')
        self.assertEqual(self.pool.entry(LEGACY).stored()['fixture_plan'], 'plus')
    def test_exact_target_and_path_validation_fail_closed(self):
        for ident in ('../outside', '', None, 'acct-' + 'f' * 24, 1):
            with self.assertRaises(codex.Fault):
                self.pool.prefer(ident)
            with self.assertRaises(codex.Fault):
                self.pool.connect(ident)
        self.assertEqual(self.pool.rows(), [LEGACY, SECOND, THIRD])
    def test_definitive_rejection_can_fall_back_but_ambiguous_transport_never_replays(self):
        self.pool.root.atomic('connection.json', {'status': 'connected', 'fixture_fault': 'limit'})
        with self.pool.open('POST', '/codex/responses', {}):
            pass
        self.assertEqual(Entry.sends, [self.pool.entry(SECOND).data])
        self.assertGreater(self.pool.cooldowns[LEGACY], time.time())
        self.pool.cooldowns.clear()
        Entry.sends.clear()
        with patch.object(self.pool.root, 'open', side_effect=codex.Fault('unreachable', 'Synthetic ambiguous transport.', 503)):
            with self.assertRaises(codex.Fault):
                self.pool.open('POST', '/codex/responses', {})
        self.assertEqual(Entry.sends, [])
    def test_dispatch_guard_is_forwarded_to_every_candidate_send(self):
        seen = []
        def guard(attempted):
            seen.append(attempted)
            raise RuntimeError('cancelled by gateway guard')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.pool.open('POST', '/codex/responses', {}, pre_dispatch=guard)
        self.assertEqual(seen, [False])
        self.assertEqual(Entry.sends, [])
    def test_disconnect_one_account_preserves_other_chains(self):
        first = (self.pool.data / 'connection.json').read_bytes()
        self.pool.disconnect(SECOND)
        self.assertEqual(self.pool.stored()['status'], 'connected')
        self.assertEqual((self.pool.data / 'connection.json').read_bytes(), first)
        self.assertEqual(self.pool.entry(THIRD).stored()['fixture_plan'], 'team')
    def test_expired_login_does_not_lock_out_next_reconnect(self):
        self.pool.connect(SECOND)
        self.pool.entry(SECOND).login['until'] = 0
        self.assertTrue(self.pool.connect(THIRD)['ok'])
        self.assertEqual(self.pool.pending, THIRD)
    def test_add_keeps_existing_chains_and_only_new_signin_uses_new_directory(self):
        first = (self.pool.data / 'connection.json').read_bytes()
        self.pool.connect(add=True)
        self.assertEqual(len(self.pool.rows()), 4)
        self.assertNotEqual(self.pool.pending, LEGACY)
        self.pool.poll('FAKE-LOGIN')
        self.assertEqual((self.pool.data / 'connection.json').read_bytes(), first)
    def test_no_hardcoded_plan_or_estimated_usage_in_ui(self):
        js = (Path(__file__).parent / 'static/app.js').read_text()
        self.assertIn('planTitle(a.plan)', js)
        self.assertIn('Usage not available yet — no estimate shown.', js)
        self.assertIn('Reconnect', js)
        self.assertIn('Use first', js)
        self.assertIn('account_id: id', js)


if __name__ == '__main__':
    unittest.main(verbosity=2)
