# Account controls on the live app-owned lineage

REUSE: `codex.Connection` remains the only OAuth, rotating-token storage/renewal,
file-lock, backend transport and per-attempt gateway guard implementation.
EXTEND: `account_pool.Pool` holds independent Connections and a secret-free,
atomically saved preference order. Existing `connection.json`, `gateway-token`
and `registration.json` stay in place. No dashboard import, migration or model
change. The Dockerfile is a disposable candidate carrier; `wizard.md` and the
source-package install recipe are unchanged. This is not a published 0.3 release.

## Intended visible changes

- Broken/expired sign-ins have Reconnect on their own row, including `?reconnect=1`.
- Use first works for every row, including broken/cooling rows, and survives a
  restart. The gateway skips definite expired/signed-out/rate-limit rejections;
  it never replays an ambiguous transport failure or returned stream.
- Titles use the login's email local part when available (display only, never
  authorization). Otherwise a plain subscription title is used.
- Actual provider plan, session/weekly usage and reset times appear per account.
  Missing usage is not presented as zero or as an invented estimate.
- Additional app-owned accounts have independent storage/renewal locks. Reconnect
  and Disconnect target one account and do not remove other accounts' chains.

## Verification (commands; results belong in the PR / external receipt)

```
python3 -m unittest discover -v -p 'test_gateway*.py'
python3 test-package.py
python3 test-chatgpt.py
python3 test-account-controls.py -v
python3 test-inference.py -v
python3 mcp/inline.py --check
sh test-container.sh
```

`qa/account_controls.cjs` drives the baked candidate in an internal-only Docker
network, with `qa/account_fixture.py` separately mounted read-only. Its accounts,
issuer codes, provider plans and usage are explicitly synthetic. No host owner
volumes, real credentials, agent bridge or private data are mounted or used.
The existing repo Appearance host is a labelled mock, not the installed shell:
it exercises live token transport on Nebula/Signal, desktop/phone, and standalone
dark. Screenshots still need visual inspection and independent code review.

## Review risks / rollback

This adds an app-owned multi-account control layer because the live app exposes
only one connection; copying the store UI alone would make Use first a dead
button. The old draft multisubscription port targets a different upstream
lineage (PR #4 there is conflicting) and must not replace this live gateway.
Review auth storage/concurrency, retry boundaries and account-target isolation.
A snapshot before adoption is required for a later approved rollout. Rolling
back the source/carrier leaves the original root connection/bearer in place;
the older version ignores `accounts/` and `account-order.json`. It cannot use
newly added accounts; do not delete their files or reuse older refresh snapshots.
No automatic downgrade, account copying, live deployment or hold change occurs.
