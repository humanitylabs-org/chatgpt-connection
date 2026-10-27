# App-owned subscription sign-in — upstream port

Owner decision, James, 27 October 2026: **“The login should not live inside the agent. The login should live inside the connection apps.”**

## Baseline and scope

Start at Oscar's Claude 0.2.6 (`d0b59dd3`) and ChatGPT 0.2.4 (`476d771f`), not the divergent fork mains. Those recipes actually execute `wizard-app-store` engines at `254ee6f2`; the app repositories' older server/static sources are not the shipped accounts pages. Bring that exact engine/page source into each source package before extending it. Preserve upstream icons, unlimited account rows, one accounts page, copy-first device steps, existing status/usage contracts, missing-agent diagnostics and alongside safety.

## Upstream moved during the port

Oscar published Claude 0.2.7 and ChatGPT 0.2.5 while this work was in progress. Both candidate branches merge those new upstream mains and retain the full design-rules-v0 pages, sheets, logos, responsive layout and the later friendly-identity bugfix from wizard-app-store main. Candidate versions remain Claude 0.3.0 / ChatGPT 0.3.1. The install recipe is tested with real Docker Compose, including the engine source/data mounts.

## Credential owner and consumers

Claude runs official Claude Code sign-in per subscription beneath its own `data/claude` directory. Export the entire folder as `claude-login`, including CLI refresh locks. A consumer may use it only under an App Manager share grant. Never bind the owner's host login into the app as an implicit fallback. Existing root `.credentials.json` remains supported; additional accounts have opaque IDs and separate CLI config directories. A consumer must explicitly support this directory inventory before claiming multi-account rotation. Subscription intermediation compliance is unresolved; using the official CLI alone is not a compliance certification.

ChatGPT reuses the fork's app-owned Codex OAuth/rotating-token implementation, one isolated connection per subscription under app-data. Only the app refreshes provider credentials. Stock Hermes consumes a local bearer through the app's native Codex gateway; it never gets a provider access/refresh token. Model catalogue, quota, compaction, native tools and unchanged default-model selection remain part of the contract. Fail over only on definitive authentication/quota rejection before streaming starts; never retry an ambiguous POST or a partially emitted reply.

The accounts page keeps Oscar's operation/inventory protocol. It now talks to an app-local owner instead of driving the agent's `hermes auth`. The agent dashboard is used for consumer registration/readback only. Missing consumer registration is distinct from a successful provider login: show the agent problem and never report that an alongside agent is connected merely because an unused local credential exists.

## Onboarding

“Log in with your subscription” opens the installed Claude or ChatGPT app, not the agent dashboard. Add/Reconnect/Remove work in that same page for any number of subscriptions. The app says separately whether the agent can use the connection. Connect Tailscale is unchanged. Release pins are Oscar's decision and are not changed here.

## Existing boxes and exclusive migration

Do not silently copy or delete agent credential pools. Stop callers and the old credential owner first. Export/move each selected subscription to the connection app using an explicit operator migration with a durable journal, atomic persistence, file/directory fsync and readback; only then remove the old refreshing owner. Ambiguous or failed handover must leave both sides stopped and require review. Offer ordinary app re-sign-in as the safe fallback; it preserves old account inventory until the owner explicitly removes it. Never start two refreshers from one rotating chain. Existing app-owned ChatGPT singleton data is adopted in place, not copied. Legacy Claude named-volume and host-bind migrations also require an exclusive move; a recipe change alone is not proof of migration.

WAM must preserve existing share grants (draft #215 is relevant but not sufficient proof), recognize app-owned declarations before applying alongside host-login overrides, and compile consumer share mounts. Hermes' Claude consumer needs directory-inventory selection/rotation; ChatGPT needs app-labelled local-bearer registration without changing the current model or redirecting other Codex credentials. The old dashboard broker must not remain onboarding's credential owner.

## Explicit offline handover now provided

`handover.py` implements the stopped-owner operation above. It is **not** an
implicit install hook, a live-agent credential copier, or a profile/global-root
migration. First stop every agent/profile, CLI and connection-app process that
could use these subscriptions; a flag/advisory lock cannot revoke an in-memory
token. Keep both sides stopped until the command succeeds. Run with the same
filesystem permissions as the app data owner:

```sh
python3 handover.py --hermes-home /path/to/stopped/hermes-home \
  --app-data /path/to/connection/private-data --owners-stopped
```

For Claude, `--app-data` is the exported `data/claude` directory (the engine's
`/claude`); for ChatGPT it is `data` (the engine's `/data`). Use explicit paths,
not a guessed running agent home. Start/stop an existing app once first if it
still has an unindexed legacy singleton. Do not point this command at an owner
installation as an exploratory test.

Supported stock shapes are `auth.json` OAuth `credential_pool.anthropic` and
`credential_pool.openai-codex`, the Codex `providers.openai-codex.tokens`
singleton, and Hermes's native `.anthropic_oauth.json` PKCE singleton. Alias
copies of one token pair become one row, including Codex pool aliases whose
expiry is recorded only in the singleton. Account labels, unrelated providers,
API-key rows, existing app inventory and model configuration are preserved.
Borrowed CLI sources, conflicting existing app chains, missing renewable
credentials, malformed data or divergent copies of one refresh chain fail
closed. External CLI/keychain/env logins and cross-profile/root sharing need
separate explicit retirement/re-sign-in; this command does not access them.

The command stages private app files, verifies them, writes the inventory and
retires the agent's pool/singleton aliases under a mode-0600 undo journal.
Writes use temp-file fsync, atomic replacement, directory fsync and readback.
An exception restores the original agent credentials and app inventory; a
second successful invocation is a no-op. On interruption, **do not start either
owner**: rerun the command with the same paths to restore then retry. The
`.handover-undo.json` is temporarily credential-bearing, remains private and
must never be attached to a report. Delete no journal manually to bypass a
failed recovery. A successful handover leaves no journal or credential backup.
Consumer mount/local-bearer registration is still a separate granted setup
step; migration alone never declares the agent connected.

## Renewal behavior and regression scope

Claude retains the official CLI as its only token refresher, including the
CLI's credential-file locking. A page status poll schedules its renewal turn
within 120 seconds of expiry; app inference waits for that renewal instead of
rejecting the credential before the CLI can refresh it. Failed renewal exposes
Reconnect, never a false connected result. There is a completion-based
10-minute attempt cooldown; this is demand-driven renewal, not an idle daemon.
The one-word Haiku renewal can consume subscription usage. ChatGPT's existing
app-owned `Connection.access()` refreshes on usage/inference access within
120 seconds of expiry, persists the rotated pair before returning, and clears
a rejected `invalid_grant` to `expired` with an immediate re-sign-in path.

`TMPDIR=/tmp/aol python3 test-renewal.py` exercises the owner-requested
regressions **existing logins move into the app without lockout** and
**expiring login refreshes without the owner**, plus interrupted recovery,
PKCE/singleton aliases, multiple rows, duplicate-chain refusal and page-driven
renewal. Tokens and HTTP OAuth endpoints are synthetic. The Claude renewal CLI
is also a labelled stand-in: this proves app orchestration and persistence,
not the official CLI's real account/issuer behavior. A real subscription's
refresh rotation, rejection and re-login remain unproven until tested with a
separately authorized disposable account. No real credentials are required by
these tests.

## Acceptance and evidence boundaries

Use forum, native Linux amd64, disposable app-data and synthetic providers only. Exercise real source-package startup, two accounts, restart/readback, renewal/exhaustion, expired/reconnect, removal, denied/cross-site requests and missing consumer. A fake provider or CLI is a contract test, not a real paid-subscription proof. Live provider login/refresh is an owner-only gate if no authorized synthetic account exists. Never read James's credentials, deploy to his box, change release pins, or mark draft #215 ready without its migration/grant proof.

Reuse:
- accounts page/operations: EXTEND Oscar's shipped `pool_server.py`, `pool-static/*` from wizard-app-store 254ee6f2; retain all account actions and state.
- provider auth: REUSE installed connection apps, existing `codex.Connection` and official Claude Code CLI.
- skin/Back/update holds: REUSE `WizardApp` from `/wizard-os-home/core/app-client.js`; remove inherited per-app root guards rather than add another implementation.
- rows/meters: REUSE shell Wizard UI kit and semantic `--wz-*` tokens.
- consumer grants: EXTEND WAM's declared shares and grants compiler, not guessed sibling paths.
Visual: preserve upstream page and icons; browser evidence must distinguish synthetic fixture tests from actual installed-shell coverage.
