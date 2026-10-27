"""Synthetic stopped-owner migration and local HTTP OAuth acceptance/regressions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs
import handover

CLAUDE = handover.PROVIDER == 'anthropic'


@contextmanager
def endpoint():
    state = {'refresh': 'SYNTHETIC-REFRESH-0', 'calls': 0, 'invalid': False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def reply(self, code, value):
            raw = json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self):
            self.reply(200, {'plan_type': 'pro', 'five_hour': {'utilization': 5}})
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length'])).decode()
            body = json.loads(raw) if self.headers.get('Content-Type') == 'application/json' else {k:v[0] for k,v in parse_qs(raw).items()}
            if self.path.endswith('/usercode'):
                return self.reply(200, {'user_code':'FAKE-1234','device_auth_id':'synthetic','interval':3})
            state['calls'] += 1
            if state['invalid'] or body.get('refresh_token') != state['refresh']:
                return self.reply(400, {'error': 'invalid_grant'})
            state['refresh'] = 'SYNTHETIC-REFRESH-' + str(state['calls'])
            self.reply(200, {'access_token':'SYNTHETIC-ACCESS-' + str(state['calls']),
                             'refresh_token':state['refresh'], 'expires_in':3600})
    srv = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=srv.serve_forever, kwargs={'poll_interval':.01}, daemon=True)
    thread.start()
    try: yield 'http://127.0.0.1:' + str(srv.server_port), state
    finally: srv.shutdown(); srv.server_close(); thread.join(3)


class HandoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root/'agent'; self.home.mkdir()
        self.app = self.root/'app'
        self.row = {'id':'old-one','label':'Work','auth_type':'oauth','priority':0,'source':'manual',
            'access_token':'SYNTHETIC-ACCESS-0','refresh_token':'SYNTHETIC-REFRESH-0',
            'expires_at_ms':int((time.time()+3600)*1000)}
        self.original = {'version':1,'active_provider':handover.PROVIDER,
            'credential_pool':{handover.PROVIDER:[self.row], 'unrelated':[{'id':'do-not-touch'}]}, 'providers':{}}
        if not CLAUDE:
            self.original['providers'][handover.PROVIDER] = {'tokens':{
                'access_token':self.row['access_token'], 'refresh_token':self.row['refresh_token'],
                'expires_at':datetime.fromtimestamp(time.time()+3600,timezone.utc).isoformat()}}
        handover.atomic(self.home/'auth.json',self.original)
        if CLAUDE:
            handover.atomic(self.home/'.anthropic_oauth.json', {'accessToken':self.row['access_token'],
                'refreshToken':self.row['refresh_token'], 'expiresAt':self.row['expires_at_ms']})
        (self.home/'config.yaml').write_text('model:\n  default: unchanged\n')
    def move(self): return handover.move(self.home,self.app,owners_stopped=True)
    def test_existing_logins_move_into_the_app_without_lockout(self):
        """existing logins move into the app without lockout"""
        self.assertEqual(self.move()['moved'],1)
        after = json.loads((self.home/'auth.json').read_text())
        self.assertEqual(after['credential_pool'][handover.PROVIDER],[])
        self.assertNotIn(handover.PROVIDER,after['providers'])
        if CLAUDE:
            self.assertEqual(json.loads((self.home/'.anthropic_oauth.json').read_text()),{})
        self.assertEqual(after['credential_pool']['unrelated'],self.original['credential_pool']['unrelated'])
        self.assertEqual((self.home/'config.yaml').read_text(),'model:\n  default: unchanged\n')
        rows = json.loads((self.app/'accounts.json').read_text())
        self.assertEqual([r['label'] for r in rows],['Work'])
        if CLAUDE:
            import inference
            self.assertEqual(inference.credential(str(self.app/'accounts'/rows[0]['id'])), self.row['access_token'])
        else:
            import codex
            self.assertEqual(codex.Connection(self.app/'accounts'/rows[0]['id']).access(), self.row['access_token'])
        self.assertEqual(self.move()['moved'],0)
        self.assertFalse((self.app/'.handover-undo.json').exists())
        filename = '.credentials.json' if CLAUDE else 'connection.json'
        self.assertEqual((self.app/'accounts'/rows[0]['id']/filename).stat().st_mode & 0o777,0o600)
    def test_failed_migration_keeps_the_original_login_working(self):
        atomic = handover.atomic
        def fail_once(path,value):
            if Path(path) == self.home/'auth.json' and value != self.original:
                raise OSError('synthetic write failure')
            atomic(path,value)
        with patch.object(handover,'atomic',side_effect=fail_once):
            with self.assertRaises(OSError): self.move()
        self.assertEqual(json.loads((self.home/'auth.json').read_text()),self.original)
        if CLAUDE:
            self.assertEqual(json.loads((self.home/'.anthropic_oauth.json').read_text())['accessToken'],self.row['access_token'])
        self.assertFalse((self.app/'accounts.json').exists())
        self.assertEqual(list((self.app/'accounts').glob('*/connection.json')),[])
        self.assertEqual(list((self.app/'accounts').glob('*/.credentials.json')),[])
        self.assertEqual(self.move()['moved'],1)
    def test_interrupted_migration_rolls_back_before_retry(self):
        # Recover exactly the pre-handover state from a durable undo record.
        self.app.mkdir()
        handover.atomic(self.app/'.handover-undo.json',{'auth_path':str(self.home/'auth.json'),
            'auth':self.original,'inventory':None,'created':['moved-fixture']})
        handover.atomic(self.app/'accounts/moved-fixture/connection.json',{'access_token':'partial'})
        handover.atomic(self.home/'auth.json',{'credential_pool':{},'providers':{}})
        self.assertEqual(self.move()['moved'],1)
    def test_without_stopped_owners_or_with_borrowed_chain_refuses(self):
        with self.assertRaises(ValueError): handover.move(self.home,self.app)
        self.original['credential_pool'][handover.PROVIDER][0]['source']='claude_code'
        handover.atomic(self.home/'auth.json',self.original)
        with self.assertRaises(ValueError): self.move()
        self.assertEqual(json.loads((self.home/'auth.json').read_text()),self.original)
    def test_pkce_singleton_without_pool_alias_moves(self):
        self.original['credential_pool'][handover.PROVIDER]=[] if CLAUDE else [{k:v for k,v in self.row.items() if k != 'expires_at_ms'}]
        handover.atomic(self.home/'auth.json',self.original)
        self.assertEqual(self.move()['moved'],1)
        self.assertEqual(self.move()['moved'],0)

    def test_duplicate_app_chain_is_not_forked(self):
        handover.atomic(self.app/'accounts.json',[{'id':'existing','label':'Already here'}])
        if CLAUDE:
            handover.atomic(self.app/'accounts/existing/.credentials.json',{'claudeAiOauth':{'refreshToken':self.row['refresh_token']}})
        else:
            handover.atomic(self.app/'accounts/existing/connection.json',{'refresh_token':self.row['refresh_token']})
        with self.assertRaises(ValueError): self.move()
        self.assertEqual(json.loads((self.home/'auth.json').read_text()),self.original)

    def test_every_subscription_and_existing_app_row_survive(self):
        second = {**self.row,'id':'two','label':'Personal','access_token':'SYNTHETIC-SECOND', 'refresh_token':'SYNTHETIC-SECOND-REFRESH'}
        self.original['credential_pool'][handover.PROVIDER].append(second)
        handover.atomic(self.home/'auth.json',self.original)
        handover.atomic(self.app/'accounts.json',[{'id':'existing','label':'Already here'}])
        self.assertEqual(self.move()['moved'],2)
        self.assertEqual([r['label'] for r in json.loads((self.app/'accounts.json').read_text())],['Already here','Work','Personal'])


class RenewalTests(unittest.TestCase):
    def test_page_poll_renews_without_an_inference_request(self):
        with tempfile.TemporaryDirectory() as tmp, endpoint() as (url,state):
            root = Path(tmp)
            import account_owner
            if CLAUDE:
                import server
                cli = Path(__file__).parent/'tests/renew_cli.py'; cli.chmod(0o755)
                pool = account_owner.Pool(root/'app'); self.addCleanup(pool.accounts.close)
                pool.accounts.save([{'id':'one','label':'Work'}]); folder=pool.directory('one')
                handover.atomic(folder/'.credentials.json',{'claudeAiOauth':{'accessToken':'SYNTHETIC-ACCESS-0',
                    'refreshToken':state['refresh'],'expiresAt':(time.time()+30)*1000}})
                handover.atomic(folder/'.fixture.json',{'issuer':url})
                with patch.multiple(server,CLAUDE=str(cli),WORK=root/'work'), patch.dict(os.environ,{'WIZARD_CLAUDE_AUTO_RENEW':'1','WIZARD_CLAUDE_USAGE_URL':url+'/usage'}):
                    self.assertEqual(pool.status('one')['status'],'renewing')
                    with pool.renewal_done:
                        self.assertTrue(pool.renewal_done.wait_for(lambda:'one' not in pool.renewing,timeout=5))
                    self.assertEqual(pool.status('one')['status'],'connected')
                    self.assertEqual(json.loads((folder/'.credentials.json').read_text())['claudeAiOauth']['refreshToken'],state['refresh'])
            else:
                import codex
                connection=codex.Connection(root/'app',url,url)
                connection.atomic('connection.json',{'status':'connected','access_token':'SYNTHETIC-ACCESS-0',
                    'refresh_token':state['refresh'],'expires_at':time.time()+30})
                pool=account_owner.Pool(connection.data,factory=lambda p:codex.Connection(p,url,url)); self.addCleanup(pool.accounts.close)
                self.assertEqual(pool.status('legacy')['status'],'connected')
                self.assertEqual(connection.stored()['refresh_token'],state['refresh'])
            self.assertEqual(state['calls'],1)

    def test_expiring_login_refreshes_without_the_owner(self):
        """expiring login refreshes without the owner"""
        with tempfile.TemporaryDirectory() as tmp, endpoint() as (url,state):
            root = Path(tmp)
            if CLAUDE:
                import account_owner, server, inference
                cli = Path(__file__).parent/'tests/renew_cli.py'; cli.chmod(0o755)
                pool = account_owner.Pool(root/'app'); self.addCleanup(pool.accounts.close)
                pool.accounts.save([{'id':'one','label':'Work'}]); folder = pool.directory('one')
                handover.atomic(folder/'.credentials.json',{'claudeAiOauth':{'accessToken':'SYNTHETIC-ACCESS-0',
                    'refreshToken':state['refresh'],'expiresAt':(time.time()+30)*1000}})
                handover.atomic(folder/'.fixture.json',{'issuer':url})
                with patch.multiple(server,CLAUDE=str(cli),WORK=root/'work'), patch.dict(os.environ,{'WIZARD_CLAUDE_AUTO_RENEW':'1','WIZARD_CLAUDE_USAGE_URL':url+'/usage'}):
                    with ThreadPoolExecutor(4) as ex:
                        results = list(ex.map(lambda _:inference.credential(pool.select()),range(4)))
                    self.assertEqual(results,['SYNTHETIC-ACCESS-1']*4)
                    saved = json.loads((folder/'.credentials.json').read_text())['claudeAiOauth']
                    self.assertEqual(saved['refreshToken'],state['refresh'])
                    restored = account_owner.Pool(pool.root); self.addCleanup(restored.accounts.close)
                    self.assertEqual(inference.credential(restored.select()),'SYNTHETIC-ACCESS-1')
                    # Expire the new generation; rejection must offer reconnect and never connect falsely.
                    saved['expiresAt']=0; handover.atomic(folder/'.credentials.json',{'claudeAiOauth':saved})
                    state['invalid']=True
                    with self.assertRaises(inference.Fault) as err: restored.select()
                    self.assertEqual(err.exception.code,'expired')
                    row = restored.accounts.public()['accounts'][0]
                    self.assertEqual(row['status'],'expired'); self.assertTrue(row['actions']['reconnect'])
                    self.assertIn('Reconnect',row['error']['message'])
                    with patch.object(server,'CLAUDE',str(Path(__file__).parent/'tests/login_cli.py')):
                        Path(server.CLAUDE).chmod(0o755)
                        self.assertTrue(restored.connect('one')['url'].startswith('https://claude.ai/'))
                        restored.cancel('one')
            else:
                import codex, account_owner
                connection = codex.Connection(root/'app',issuer=url,backend=url)
                connection.atomic('connection.json',{'status':'connected','access_token':'SYNTHETIC-ACCESS-0',
                    'refresh_token':state['refresh'],'expires_at':time.time()+30})
                with ThreadPoolExecutor(4) as ex: results = list(ex.map(lambda _:connection.access(),range(4)))
                self.assertEqual(results,['SYNTHETIC-ACCESS-1']*4)
                self.assertEqual(connection.stored()['refresh_token'],state['refresh'])
                restored = codex.Connection(connection.data,issuer=url,backend=url)
                self.assertEqual(restored.access(),'SYNTHETIC-ACCESS-1')
                restored.atomic('connection.json',{**restored.stored(),'expires_at':0})
                state['invalid']=True
                with self.assertRaises(codex.Fault) as err: restored.access()
                self.assertEqual(err.exception.code,'expired')
                self.assertEqual(restored.stored(),{'status':'expired'})
                self.assertIn('Connect',str(err.exception))
                pool = account_owner.Pool(connection.data, factory=lambda p:codex.Connection(p,url,url))
                self.addCleanup(pool.accounts.close)
                with patch.object(pool,'integration',return_value={'connected':False}):
                    row = pool.accounts.public()['accounts'][0]
                self.assertEqual(row['status'],'expired'); self.assertTrue(row['actions']['reconnect'])
                # Re-sign-in is available immediately, without retrying the dead chain.
                self.assertTrue(restored.connect()['url'].endswith('/codex/device'))
            self.assertEqual(state['calls'],2)


if __name__ == '__main__': unittest.main(verbosity=2)
