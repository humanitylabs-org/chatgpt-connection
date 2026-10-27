"""Validate the actual install recipe, not just an in-process HTTP fixture.

Requires Docker Compose (no daemon). Never starts a container or reads owner env.
"""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent
APP = 'claude' if (ROOT / 'usage.py').exists() else 'chatgpt'


class RecipeTests(unittest.TestCase):
    def test_source_package_mounts_belong_to_engine_not_top_level_volume_definition(self):
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'DOCKER_CONFIG')}
        env.update(APP_SOURCE_DIR='/source', APP_DATA_DIR='/app-data')
        result = subprocess.run(['docker', 'compose', '-f', str(ROOT / 'runtipi/docker-compose.yml'),
                                 'config', '--format', 'json'],
                                env=env, capture_output=True, text=True, check=True)
        doc = json.loads(result.stdout)
        engine = doc['services']['wizard-' + APP + '-engine']
        self.assertEqual(engine['command'], ['python3', '/app/pool_server.py'])
        mounts = {m['target']: m for m in engine['volumes']}
        self.assertEqual(mounts['/app']['source'], '/source')
        self.assertTrue(mounts['/app']['read_only'])
        target = '/claude' if APP == 'claude' else '/data'
        expected = '/app-data/data/claude' if APP == 'claude' else '/app-data/data'
        self.assertEqual(mounts[target]['source'], expected)
        self.assertEqual(mounts[target]['type'], 'bind')
        self.assertEqual(engine['depends_on']['data-init']['condition'], 'service_completed_successfully')
        self.assertTrue(engine['read_only'])
        self.assertEqual(engine['user'], '1000:1000')
        self.assertNotIn('claude-login', doc.get('volumes', {}))
        if APP == 'chatgpt':
            self.assertEqual(engine['environment']['WIZARD_CHATGPT_AGENT_URL'], 'http://wizard-chatgpt:8801/v1')

    def test_shipped_page_preserves_accounts_sheet_icons_and_shared_back_update_hold(self):
        page = (ROOT / 'pool-static/index.html').read_text()
        script = (ROOT / 'pool-static/app.js').read_text()
        self.assertIn('wizard-layout.css', page)
        self.assertIn('wizard-sheet.js', page)
        self.assertIn('/wizard-os-home/core/app-client.js', page)
        self.assertIn('WizardApp?.back.register', script)
        self.assertIn('WizardApp?.update.hold', script)
        self.assertIn('integration?.connected === false', script)
        self.assertNotIn('history.pushState', script)
        for name in ('logo-claude.svg', 'logo-chatgpt.svg'):
            self.assertTrue((ROOT / 'pool-static' / name).is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
