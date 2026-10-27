# ChatGPT connection — 0.3.0

> **Contribution fork — upstream is canonical.** Wizard OS releases install
> [oscarhenrycollins/chatgpt-connection](https://github.com/oscarhenrycollins/chatgpt-connection).
> The app-owned multi-subscription port is contributed there as
> [upstream PR #4](https://github.com/oscarhenrycollins/chatgpt-connection/pull/4),
> rebuilt on Oscar's current code rather than this fork's older divergent main.
> Follow upstream for release/onboarding work. Do not pin new boxes to this fork's
> main or treat a fork-only login fix as shipped. Existing pins/data are not changed
> by this documentation notice.


This app owns the Codex device-code login and its rotating renewal chain. Stock
Hermes and Connection Router clients use the app; they never receive a ChatGPT
access or refresh token. Version 0.3.0 uses `runtime.package: source`: App Manager
mounts the exact installed repository commit read-only at `/app` and runs its
stdlib Python engine on a digest-pinned official `python:3.12-alpine` image.
The previous custom engine image is no longer used; no image build or publication
is required. Install with an App Manager that supports source packages.

## Ownership and storage

Open the page, **Connect**, copy the large sign-in code first, open the ChatGPT
sign-in link in a new tab, then paste the code when ChatGPT asks for it. Finish
signing in and come back to the waiting page; it shows **Connected** after approval.
**Copy** confirms **Copied** only after clipboard success, with selected-code
manual copy if the browser cannot copy automatically. Do not paste codes,
passwords or tokens into chat. The
public OAuth client and endpoints match stock Hermes' Codex device flow:
`app_EMoamEEZ73f0CkXaXp7hrann`, deviceauth usercode/token, then `/oauth/token`
with the returned authorization code/verifier and `/deviceauth/callback` redirect.
The app's **Test** verifies authenticated model discovery and quota and retries
Hermes registration. **Disconnect** deletes the local ChatGPT credentials and
removes the app's Hermes pool entry; replies already in flight may finish. It is
not global account revocation: use ChatGPT's account controls for that.

`APP_DATA_DIR` is the **in-container** storage directory (default `.data` for local
source runs). Compose sets it to `/data` and bind-mounts `${APP_DATA_DIR}/data:/data`:
Runtipi's outer `APP_DATA_DIR` is the host app-data root. There are **no named
Docker volumes**. Prepare that host directory for UID/GID 1000 before starting the
container. App Manager backup captures this bind mount.

- Directory: `0700`. `connection.json`, `gateway-token.json`, `.oauth.lock` and
  `registration.json`: `0600`. They are plaintext owner-only secrets, not encrypted
  at rest; encrypt the disk/backups. Never put app-data into a source checkout.
- Refresh 120 seconds before expiry, on demand. Process mutex + filesystem flock
  serialize renewal across threads/processes. Re-read while locked; persist the
  rotated chain using same-directory replace, file fsync and directory fsync.
- `invalid_grant` and explicit expired/reused/invalidated refresh-chain errors erase
  the unusable chain, mark `expired`, require reconnect.
  No automatic replay of a timed-out token exchange: a durable refreshing marker
  becomes reconnect-required after an ambiguous result or crash. Just-received
  rotated credentials are retained in memory for retrying a failed disk write
  before any further refresh. A process crash during a provider-accepted refresh
  followed by a network/disk failure can still require reconnect; single-use
  refresh tokens cannot be made transactionally atomic with a remote OAuth server.
- No live Hermes auth/config files are mounted or read by this app.

## Stock Hermes: native Codex through the box

The preferred runtime is **(b), `openai-codex`**, not a named custom runtime:

```yaml
model:
  provider: openai-codex
  default: <an entitled model from the app catalogue>
```

Set `HERMES_CODEX_BASE_URL=http://wizard-chatgpt:8801/v1`. Hermes holds only the
opaque per-install gateway bearer. It can live in a manual **API-key** pool entry
labelled `wizard-chatgpt` for provider `openai-codex` (the registration path), or in
an isolated singleton `auth.json` entry with **only** `tokens.access_token` set to
that local bearer (the tested alternative). Never give Hermes a real ChatGPT
token or refresh token. The local key is not a JWT and has no OAuth expiry/refresh.
Do not use `hermes auth add openai-codex`: that creates a second OAuth owner.

After approval the app uses the stock dashboard APIs to:

1. Refuse existing non-app Codex pool entries and a legacy singleton login
   (via the stock read-only OAuth status endpoint) instead of redirecting another login.
2. Advertise the `wizard-chatgpt` named custom endpoint, `api_mode: codex_responses`,
   `make_default: false`, with just the local key (same endpoint API as Tinfoil).
3. Set `HERMES_CODEX_BASE_URL` and provision the native `openai-codex` API-key pool
   entry, reading back the endpoint and pool. Re-registration replaces only its
   own labelled row when its saved key fingerprint/base changes; otherwise it is
   idempotent. Registration and disconnect are serialized. The app's current default model is **never selected for you**.

To retain all native features, explicitly select **OpenAI Codex** in Hermes, not
the advertised **ChatGPT app** custom fallback. A running agent may need a fresh
session/process to reload the endpoint environment. On a pre-existing host with
real Codex credentials, registration reports `conflict`; stop/migrate that old
login deliberately before enabling this route. Do not combine two refresh owners.
Dashboard credentials are used **only for registration**, never for model calls,
usage or OAuth. If the dashboard is absent, ChatGPT and Connection Router still
work; registration reads `unavailable`. A failed registration does not erase login.

| Stock Hermes runtime | Text/tools/streaming | Native web-search tool | Quota UI path | Server-side compaction |
|---|---|---|---|---|
| (a) named `providers.chatgpt-box`, `codex_responses` | Yes | No: remains function | No | No |
| (b) `openai-codex` + box base + local bearer | Yes | Yes: maps to `web_search` | Yes, proxied | Yes, `/responses/compact` |

Verified on unmodified Hermes `818c13be1dc4fd28987e1e881a9408224afd4535`.
The comparison includes native tool mapping, real stock quota parsing against a
fake backend, and SDK compaction through the gateway. Real data-plane acceptance
covers plain text, terminal tool round-trip, incremental streaming and quota;
**real native web search/compaction generation were not exercised**.

Disconnect leaves the Codex base override fail-closed (box URL), even after pool
removal, so a stale local bearer cannot be redirected to ChatGPT by removing the
override. Do not delete that override while a residual app bearer remains. Before ever
returning to a different native Hermes OAuth login, stop its callers, verify both
the app pool row and custom endpoint/key are removed, then remove this app
override deliberately. Never add a real Hermes OAuth credential while it remains.
A failed dashboard cleanup reports `unregister_failed`, not a successful removal.

## Box-only gateway and Connection Router

Only the local bearer in `gateway-token.json` authenticates `/v1/*`. Browser
Origin/Referer/Cookie/Fetch-Metadata, proxy/forwarded and tailnet identity headers
are refused even with the key. The owner page password is not a gateway key. Keep
the app on a trusted private app network; no public port or browser CORS access.

Fixed routes, never an arbitrary URL proxy:

- `GET /v1/models` → Codex model catalogue, OpenAI-compatible list.
- `POST /v1/responses` → Codex Responses SSE, flushed line-by-line. The gateway
  forces `store:false`, `stream:true`, supplies instructions and strips API-only
  sampling/output-limit knobs rejected by Codex. Function calls and native tools
  pass unchanged. Streaming is the supported Codex transport, even when an API
  caller omits `stream`.
- `POST /v1/responses/compact` → native compaction.
- `GET /v1/api/codex/usage` and `/rate-limit-reset-credits` → the matching `/wham/`
  endpoints that stock `agent/account_usage.py` derives for a non-backend-api base.
- `POST /v1/api/codex/rate-limit-reset-credits/consume` → the explicit stock quota
  reset action, not used by Test or automated acceptance.

The app injects the real Authorization, account-id, `originator: hermes-agent` and
residency headers. Only quota/retry headers pass back; upstream error bodies and
cookies do not. Eight gateway requests at once, 16 MiB request limit, 4 MiB line /
64 MiB stream bounds, 120 s socket timeout and 600 s checked stream deadline.
Request bodies, credentials, account identifiers and upstream errors are not logged.

`GET /api/connection` retains `wizard.ai-connection/2`, including app-owned status,
plan, safe usage limits and entitled catalogue. `account` is deliberately null;
OAuth claims/account IDs are not surfaced. `chatIntegration` describes registration.

`POST /api/infer` still accepts explicit text-only router input:
`{model, system?, messages, stream?: true, max_tokens?, abilities?}`. It now calls
Codex directly with the app-owned token, not a Hermes plugin. The catalogue is
validated; messages allow only user/assistant text. Tools, alternate URLs, profiles
and identities are refused. SSE is `delta`, then an effective-model checked `done`
with provider usage, or `error`. Missing done / model mismatch is failure. Discard
provisional text on error. Two active router streams; 256 KiB requests/lines,
4 MiB stream bound, 120 s deadline, disconnect cancellation and heartbeats.
`max_tokens` is validated for compatibility but **not an enforced output budget**:
Codex rejects `max_output_tokens`; the byte/time bounds are the actual guards.

Router grants retain the existing configuration:

```json
{"enforce":true,"incoming":[{"from":"wizard-connection-router","sha256":"<64 lowercase hex characters>"}]}
```

Set `WIZARD_GRANTS_FILE` to that file, and send the matching opaque `X-Wizard-Grant`.
Absent enforcement is report-only, **not caller authentication**. Missing/malformed
configured files fail closed. Grants do not authorize OAuth controls or the model
gateway. `/api/connection` accepts a recognized router grant when the page has a
password; unknown grants cannot bypass it.

## Move to another box (one active refresher)

1. Stop the old app completely, including workers and in-flight requests. Stop
   Hermes callers. Never run old and new copies simultaneously.
2. Back up/copy **the entire host app-data directory**, preserving ownership/modes,
   including the gateway token and credential file. Treat the backup as a secret.
3. Restore it to the new app's `${APP_DATA_DIR}` bind source; use UID/GID 1000 and
   no named volume. Start only the new copy.
4. Update the app's dashboard URL/credentials and `WIZARD_CHATGPT_AGENT_URL` for the
   new private network. Press **Test** to verify login and register local routing.
5. Start a new Hermes session using the native `openai-codex` provider. At the next
   expiry/skew window only the new app refreshes. Do not restart the old copy:
   its refresh token becomes stale as soon as rotation occurs on the new box.

The automated migration test copies app-data, instantiates only the destination
for renewal, preserves the local key, and verifies successful refresh rotation.
Rollback after rotation requires copying the **newest** stopped store back, not
restoring an old token chain. This does not test a real second physical box.

## Configuration

- `APP_DATA_DIR`: writable in-container app-data location.
- `WIZARD_CHATGPT_PORT` / `WIZARD_CHATGPT_BIND`: engine listen address (default 8801,
  `0.0.0.0`; bind loopback for a local source-only run).
- `WIZARD_CHATGPT_PASSWORD`: optional owner-page Basic password, not a model key.
- `WIZARD_CHATGPT_HERMES_URL`, `_USER`, `_PASSWORD`: optional dashboard registration.
- `WIZARD_CHATGPT_AGENT_URL`: private URL Hermes should call; default
  `http://wizard-chatgpt:8801/v1`. Not a browser/Serve URL.
- `WIZARD_TRUSTED_ORIGINS`: frame-ancestor origins for hosted Appearance transport.
- `WIZARD_GRANTS_FILE`: router grant enforcement.

The page preserves Appearance v1 (`--wz-*`, live skin updates, hosted transparent
canvas, standalone fallback); Connect / Test / Disconnect never expose a token.
MCP `connection_status` is read-only, reporting the same safe app-owned status.

## Verification

```sh
python3 test-package.py
python3 test-chatgpt.py
python3 test-inference.py -v
python3 mcp/inline.py --check
```

The package test boots the real source engine against fresh empty scratch app-data,
checks health/version and the disconnected page/status, and asserts the source and
data mount contract. It is a process-level smoke test, not Docker/installed-box QA.
Other tests use only fake loopback OAuth/Codex servers. They cover device exchange,
rotation and process-safe single-flight, invalid_grant, migration, gateway browser
and auth refusals, streaming, secret suppression and Router contract behavior.
`qa/` contains the Appearance/controls hosted proxy (not real-shell/physical-device
acceptance). `test-container.sh` optionally boots the same pinned official Python
image with the repo mounted read-only on a builder; it does not build a custom
image and was not run on this control-plane task. No images or app index entries
are published.

### Remaining owner gate: real device login/renewal

On an isolated candidate built from this branch, bind a **fresh empty** app-data
folder; do not reuse or copy the owner's Hermes credentials. Have the owner open
the candidate page and press Connect, approve the code at the OpenAI page, return
and see Connected. Press Test, verify plan/quota and registration; confirm Hermes'
default model was unchanged. Explicitly select native OpenAI Codex and send a
plain question, a harmless terminal tool request and a streaming question. Wait
for natural renewal (or adjust only the candidate store's `expires_at`, never a
live token) and verify that the app alone rotates once and Hermes still has no
ChatGPT credential. Stop the old candidate, copy its app-data to an isolated second
candidate, Test/send again, and do not restart the old copy. Finally Disconnect
and verify new requests fail. **Owner approval is required for this login; this
branch's acceptance did not initiate a real device-code sign-in or rotate the
owner's existing ChatGPT refresh token.**
