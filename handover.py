"""Explicit offline handover. Stop BOTH owners before running; never a startup hook.

No OAuth calls: the rotating chain is moved, not exercised or forked. A private
undo journal repairs interruption on the next invocation, before either owner
may restart. External CLI/keychain/env credentials must be retired separately.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

PROVIDER = 'openai-codex'


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.handover-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(tmp).unlink(missing_ok=True)


def unlink(path):
    path = Path(path)
    path.unlink(missing_ok=True)
    if path.parent.exists():
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def expiry(row):
    if isinstance(row.get('expires_at_ms'), (int, float)):
        return row['expires_at_ms'] / 1000
    v = row.get('expires_at')
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace('Z', '+00:00')).timestamp()
    raise ValueError('Missing expiry; original login was not changed')


def move(home, app, *, owners_stopped=False):
    if not owners_stopped:
        raise ValueError('Stop the agent AND connection app, then explicitly confirm owners_stopped')
    if Path(home).is_symlink() or Path(app).is_symlink():
        raise ValueError('Symlink storage is unsupported')
    home, app = Path(home).resolve(), Path(app).resolve()
    if home == app or home in app.parents or app in home.parents:
        raise ValueError('Agent and app private directories must be separate')
    if home.parent.name == 'profiles':
        raise ValueError('Named-profile/root sharing requires a separate exclusive handover')
    auth = home / 'auth.json'
    if auth.is_symlink() or app.is_symlink():
        raise ValueError('Symlink storage is unsupported')
    app.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(app, 0o700)
    journal = app / '.handover-undo.json'
    pkce_path = home / '.anthropic_oauth.json'
    if pkce_path.is_symlink():
        raise ValueError('Symlink login storage is unsupported')
    # The stopped-owner declaration is essential: advisory file locks cannot
    # revoke credentials already loaded in an agent or official CLI process.
    with ExitStack() as stack:
        for path in (home / 'auth.lock', app / '.account-operation.lock', app / '.handover.lock'):
            f = stack.enter_context(path.open('a'))
            os.chmod(path, 0o600)
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def restore(undo):
            if undo['auth_path'] != str(auth):
                raise ValueError('Undo belongs to another agent home; keep both owners stopped')
            if undo.get('pkce') is not None:
                atomic(pkce_path, undo['pkce'])
            atomic(auth, undo['auth'])
            if undo['inventory'] is None:
                unlink(app / 'accounts.json')
            else:
                atomic(app / 'accounts.json', undo['inventory'])
            for ident in undo['created']:
                if not isinstance(ident, str) or ident in ('.', '..') or '/' in ident or '\\' in ident:
                    raise ValueError('Invalid undo account ID; keep both owners stopped')
                folder = app / 'accounts' / ident
                for filename in ('.credentials.json', 'connection.json'):
                    unlink(folder / filename)
                if folder.exists():
                    folder.rmdir()
            unlink(journal)
        if journal.exists():
            restore(json.loads(journal.read_text()))
        original = json.loads(auth.read_text())
        updated = json.loads(json.dumps(original))
        pool = updated.setdefault('credential_pool', {})
        providers = updated.setdefault('providers', {})
        if not isinstance(pool, dict) or not isinstance(providers, dict):
            raise ValueError('Invalid stock auth store')
        rows = pool.get(PROVIDER, [])
        if not isinstance(rows, list):
            raise ValueError('Unsupported stock pool shape')
        candidates, kept = [], []
        for row in rows:
            if row.get('auth_type') != 'oauth':
                kept.append(row)
                continue
            if str(row.get('source', '')).startswith(('claude_code', 'codex_cli')):
                raise ValueError('Borrowed external login: stop and retire its external owner first')
            candidates.append(row)
        pkce = json.loads(pkce_path.read_text()) if PROVIDER == 'anthropic' and pkce_path.exists() else None
        if pkce and pkce.get('accessToken'):
            candidates.append({'access_token': pkce.get('accessToken'), 'refresh_token': pkce.get('refreshToken'),
                'expires_at_ms': pkce.get('expiresAt'), 'label': 'Subscription', 'scopes': pkce.get('scopes')})
        singleton = providers.get(PROVIDER)
        if singleton:
            if PROVIDER != 'openai-codex' or not isinstance(singleton.get('tokens'), dict):
                raise ValueError('Unsupported singleton; original login was not changed')
            candidates.append({**singleton['tokens'], 'label': 'Subscription'})
        if not candidates:
            return {'moved': 0, 'status': 'unchanged'}
        inventory_path = app / 'accounts.json'
        inventory = json.loads(inventory_path.read_text()) if inventory_path.exists() else None
        # Require an initialized inventory, to avoid masking an old app singleton.
        if inventory is None and any((app / n).exists() for n in ('.credentials.json', 'connection.json')):
            raise ValueError('Start then stop the app once to register its existing singleton')
        new_inventory = list(inventory or [])
        files, seen = [], {}
        for row in candidates:
            access, refresh = row.get('access_token'), row.get('refresh_token')
            if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
                raise ValueError('Login is not renewable; original login was not changed')
            if refresh in seen:
                if seen[refresh] != access:
                    raise ValueError('Divergent copies of one chain; resolve before handover')
                continue
            seen[refresh] = access
            for existing in new_inventory[:len(inventory or [])]:
                folder = app if existing['id'] == 'legacy' else app / 'accounts' / existing['id']
                path = folder / ('.credentials.json' if PROVIDER == 'anthropic' else 'connection.json')
                if path.exists():
                    saved = json.loads(path.read_text())
                    current = saved.get('claudeAiOauth', {}).get('refreshToken') if PROVIDER == 'anthropic' else saved.get('refresh_token')
                    if current == refresh:
                        raise ValueError('This chain is already in the app; resolve duplicate ownership first')
            ident = 'moved-' + hashlib.sha256(refresh.encode()).hexdigest()[:24]
            folder = app / 'accounts' / ident
            if folder.exists():
                raise ValueError('Destination conflict; original login was not changed')
            try:
                until = expiry(row)
            except ValueError:
                # Stock Codex's device_code pool alias can omit expiry; its
                # matching auth.json singleton remains the authoritative source.
                peers = [p for p in candidates if p.get('refresh_token') == refresh and p.get('access_token') == access
                         and (p.get('expires_at') is not None or p.get('expires_at_ms') is not None)]
                if not peers:
                    raise
                until = expiry(peers[0])
            if PROVIDER == 'anthropic':
                filename = '.credentials.json'
                value = {'claudeAiOauth': {**{k: row[k] for k in ('scopes', 'subscriptionType', 'rateLimitTier') if row.get(k) is not None},
                    'accessToken': access, 'refreshToken': refresh, 'expiresAt': until * 1000}}
            else:
                filename = 'connection.json'
                value = {'status': 'connected', 'access_token': access, 'refresh_token': refresh, 'expires_at': until}
            files.append((ident, filename, value))
            new_inventory.append({'id': ident, 'label': str(row.get('label') or 'Subscription')})
        undo = {'auth_path': str(auth), 'auth': original, 'inventory': inventory, 'created': [r[0] for r in files], 'pkce': pkce}
        atomic(journal, undo)
        try:
            for ident, filename, value in files:
                atomic(app / 'accounts' / ident / filename, value)
                if json.loads((app / 'accounts' / ident / filename).read_text()) != value:
                    raise OSError('Destination readback failed')
            atomic(inventory_path, new_inventory)
            pool[PROVIDER] = kept
            providers.pop(PROVIDER, None)
            if pkce is not None:
                atomic(pkce_path, {})  # retire PKCE alias, covered by the same stopped-owner undo journal
            atomic(auth, updated)  # retire all singleton/pool aliases in one stock-store write
            if json.loads(auth.read_text()) != updated:
                raise OSError('Agent retirement readback failed')
        except BaseException:
            restore(undo)
            raise
        # If interrupted before deletion, next run rolls back; neither owner may
        # restart until this command returns successfully with no journal left.
        unlink(journal)
        return {'moved': len(files), 'status': 'moved'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hermes-home', required=True)
    p.add_argument('--app-data', required=True)
    p.add_argument('--owners-stopped', action='store_true')
    a = p.parse_args()
    print(json.dumps(move(a.hermes_home, a.app_data, owners_stopped=a.owners_stopped)))
