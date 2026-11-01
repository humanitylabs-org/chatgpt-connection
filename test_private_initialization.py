"""Fast source/metadata regressions. Actual UID handoff is the queued container gate."""
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import yaml

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('prepare_private_data', ROOT / 'prepare_private_data.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class InitializerContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.data = self.base / 'data'
        self.data.mkdir(mode=0o700)
        self.identity = os.geteuid(), os.getegid()

    def validate(self):
        helper.validate(str(self.data), *self.identity)

    def test_existing_identity_and_private_bytes_unchanged(self):
        secret = self.data / 'auth.json'
        secret.write_bytes(b'  {"synthetic":"original", "unknown": [1]}\n')
        secret.chmod(0o600)
        before = secret.read_bytes(), secret.stat(), self.data.stat()
        helper.prepare(str(self.data), *self.identity)
        self.validate()
        after = secret.stat(), self.data.stat()
        self.assertEqual(secret.read_bytes(), before[0])
        for old, new in zip(before[1:], after):
            self.assertEqual((old.st_uid, old.st_gid, old.st_mode, old.st_ino),
                             (new.st_uid, new.st_gid, new.st_mode, new.st_ino))

    def test_partial_existing_tree(self):
        (self.data / 'partial').mkdir(mode=0o700)
        self.validate()
        self.assertFalse((self.data / 'missing').exists())

    def test_foreign_identity_refused_without_retarget(self):
        before = self.data.stat()
        with self.assertRaises(ValueError):
            helper.prepare(str(self.data), 1101, 2202)
        self.assertEqual(before, self.data.stat())

    def test_validate_wrong_runtime_identity_refused(self):
        with self.assertRaises(ValueError):
            helper.validate(str(self.data), 1101, 2202)

    def test_symlink_does_not_touch_known_secret(self):
        outside = self.base / 'outside'
        outside.write_bytes(b'synthetic-do-not-change')
        outside.chmod(0o600)
        before = outside.read_bytes(), outside.stat()
        (self.data / 'auth.json').symlink_to(outside)
        with self.assertRaises(ValueError):
            self.validate()
        self.assertEqual((outside.read_bytes(), outside.stat()), before)

    def test_symlink_directory_refused(self):
        (self.data / 'escape').symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.validate()

    def test_root_symlink_refused(self):
        link = self.base / 'alias'
        link.symlink_to(self.data, target_is_directory=True)
        with self.assertRaises(OSError):
            helper.open_directory(str(link))

    def test_intermediate_symlink_refused(self):
        (self.data / 'child').mkdir()
        link = self.base / 'alias'
        link.symlink_to(self.data, target_is_directory=True)
        with self.assertRaises(OSError):
            helper.open_directory(str(link / 'child'))

    def test_path_escape_refused(self):
        for path in ('../data', '/', str(self.data) + '/../data', str(self.data) + '//child'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                helper.open_directory(path)

    def test_hardlink_refused(self):
        outside = self.base / 'outside'
        outside.write_bytes(b'synthetic-original')
        os.link(outside, self.data / 'auth.json')
        before = outside.stat()
        with self.assertRaises(ValueError):
            self.validate()
        self.assertEqual(before, outside.stat())

    def test_special_file_refused(self):
        os.mkfifo(self.data / 'pipe', 0o600)
        with self.assertRaises(ValueError):
            self.validate()

    def test_nested_wrong_owner_refused(self):
        secret = self.data / 'auth.json'
        secret.touch(mode=0o600)
        real_fstat = os.fstat
        def info(fd):
            result = real_fstat(fd)
            if stat.S_ISREG(result.st_mode):
                return mock.Mock(st_uid=1101, st_gid=2202, st_dev=result.st_dev)
            return result
        with mock.patch.object(helper.os, 'fstat', side_effect=info), self.assertRaises(ValueError):
            self.validate()

    def test_prepare_populated_root_refused_without_chmod_or_chown(self):
        (self.data / 'auth.json').touch(mode=0o600)
        root_info = mock.Mock(st_uid=0, st_gid=0, st_mode=stat.S_IFDIR | 0o755)
        with mock.patch.object(helper.os, 'fstat', return_value=root_info), \
             mock.patch.object(helper.os, 'fchmod') as chmod, \
             mock.patch.object(helper.os, 'fchown') as chown:
            with self.assertRaises(ValueError):
                helper.prepare(str(self.data), *self.identity)
            chmod.assert_not_called()
            chown.assert_not_called()

    def test_empty_root_handoff_order_and_failed_chown_restores_mode(self):
        root_info = mock.Mock(st_uid=0, st_gid=0, st_mode=stat.S_IFDIR | 0o755)
        events = []
        with mock.patch.object(helper.os, 'fstat', return_value=root_info), \
             mock.patch.object(helper.os, 'fchmod', side_effect=lambda fd, mode: events.append(('chmod', mode))), \
             mock.patch.object(helper.os, 'fchown', side_effect=lambda *a: (_ for _ in ()).throw(PermissionError())):
            with self.assertRaises(PermissionError):
                helper.prepare(str(self.data), *self.identity)
        self.assertEqual(events, [('chmod', 0o700), ('chmod', 0o755)])

    def test_exact_inline_compose_and_narrow_authority(self):
        compose = yaml.safe_load((ROOT / 'runtipi/docker-compose.yml').read_text())
        services = compose['services']
        app = 'wizard-chatgpt' if helper.RECIPE_ROOT == '/data' else 'wizard-claude'
        init, check, engine = (services[app + suffix] for suffix in ('-data-init', '-data-validate', '-engine'))
        source = (ROOT / 'prepare_private_data.py').read_text()
        self.assertEqual(init['command'], ['python3', '-c', source, 'prepare'])
        self.assertEqual(check['command'], ['python3', '-c', source, 'validate'])
        self.assertEqual(init['cap_add'], ['CHOWN'])
        self.assertEqual(init['cap_drop'], ['ALL'])
        self.assertEqual(init['user'], '0:0')
        self.assertEqual(check['user'], '1000:1000')
        self.assertEqual(check['cap_drop'], ['ALL'])
        self.assertNotIn('cap_add', check)
        self.assertEqual(init['network_mode'], 'none')
        self.assertEqual(check['network_mode'], 'none')
        self.assertTrue(init['read_only'] and check['read_only'])
        self.assertEqual(engine['user'], '1000:1000')
        self.assertEqual(engine['cap_drop'], ['ALL'])
        self.assertTrue(engine['read_only'])
        self.assertEqual(engine['depends_on'][app + '-data-validate']['condition'], 'service_completed_successfully')
        self.assertEqual(check['depends_on'][app + '-data-init']['condition'], 'service_completed_successfully')
        self.assertEqual(len(init['volumes']), 1)
        self.assertEqual(check['volumes'], [init['volumes'][0] + ':ro'])
        self.assertEqual(compose['x-runtipi']['schema_version'], 2)


if __name__ == '__main__':
    unittest.main()
