"""Synthetic permission regression tests. No live data or provider calls."""
import os
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import codex
import server


class PrivateStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'private'
        self.old_umask = os.umask(0)

    def tearDown(self):
        os.umask(self.old_umask)
        self.temp.cleanup()

    def assert_modes(self):
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        for path in self.root.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_create_refresh_and_legacy_restart_preserve_bytes(self):
        c = codex.Connection(self.root)
        self.assert_modes()
        legacy = b'{ "unknown": "preserve formatting", "status":"connected" }\n'
        (self.root/'connection.json').write_bytes(legacy)
        before = {p.name:p.read_bytes() for p in self.root.iterdir()}
        self.root.chmod(0o777)
        for p in self.root.iterdir(): p.chmod(0o777)
        restarted = codex.Connection(self.root)
        self.assert_modes()
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.root.iterdir()})
        self.root.chmod(0o777)
        (self.root/'.oauth.lock').chmod(0o777)
        with restarted.lock():
            restarted.atomic('connection.json', {'synthetic':'refreshed'})
        self.assert_modes()
        self.assertEqual(restarted.stored(), {'synthetic':'refreshed'})

    def test_atomic_replace_failure_keeps_original_and_cleans_temp(self):
        c = codex.Connection(self.root)
        c.atomic('connection.json', {'synthetic':'original'})
        before = (self.root/'connection.json').read_bytes()
        with patch.object(codex.os, 'replace', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError): c.atomic('connection.json', {'synthetic':'new'})
        self.assertEqual(before, (self.root/'connection.json').read_bytes())
        self.assertFalse(list(self.root.glob('.pending-*')))
        self.assert_modes()

    def test_lock_symlink_does_not_chmod_outside(self):
        c = codex.Connection(self.root)
        outside = Path(self.temp.name)/'outside'
        outside.write_bytes(b'synthetic')
        outside.chmod(0o644)
        (self.root/'.oauth.lock').unlink()
        (self.root/'.oauth.lock').symlink_to(outside)
        with self.assertRaises(OSError):
            with c.lock(): pass
        self.assertEqual(outside.stat().st_mode & 0o777, 0o644)

    def test_exception_logs_and_mcp_errors_do_not_echo_secret(self):
        sentinel = 'SYNTHETIC-DO-NOT-EXPOSE'
        spec = importlib.util.spec_from_file_location('test_front', Path(__file__).parent/'mcp/front.py')
        assert spec is not None and spec.loader is not None
        front = importlib.util.module_from_spec(spec); spec.loader.exec_module(front)
        def fail(): raise RuntimeError(sentinel)
        front.TOOLS['synthetic'] = ({}, fail)
        answer = front.rpc({'method':'tools/call','id':1,'params':{'name':'synthetic'}})
        self.assertNotIn(sentinel, json.dumps(answer))
        self.assertTrue(answer['result']['isError'])
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            try: fail()
            except RuntimeError: server.Server.handle_error(None,None,None)
        self.assertNotIn(sentinel, out.getvalue())


if __name__ == '__main__': unittest.main()
