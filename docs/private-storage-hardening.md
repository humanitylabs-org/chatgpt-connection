# Private storage hardening (source-only)

Reuse:
- Credential persistence: EXTEND: `codex.Connection.atomic` and `Connection.lock` — keep existing JSON schema, native OAuth refresh and atomic mkstemp/fsync/replace; reassert directory and existing lock modes.
- MCP front: EXTEND: `mcp/front.py` — fixed error messages rather than exception text; regenerate inline compose with `python3 mcp/inline.py`.

The branch is based on deployed source `72b5e9b0f733`, including image gateway support. No release or custody migration is performed by these changes.

## Verify

Run `python3 -m unittest -v test_private_storage` and `python3 test-chatgpt.py` with synthetic scratch directories. These cover restrictive creation/refresh, legacy 0777 repair/restart without rewriting canonical bytes, failed replace preserving the original, rejected lock symlink, sanitized MCP/HTTP exceptions, single-writer refresh and ambiguous-outcome refusal. No real tokens/provider calls are needed.

The app writes secret JSON atomically at 0600 and uses 0700 for its data directory. The existing lock is fchmoded0600 too. Existing canonical byte formatting is untouched by startup hardening. API status never serves raw OAuth/gateway tokens.

## Boundary and release gate

**This is permission hardening, not encryption or same-UID isolation.** Runtipi's `setAppDataDirPermissions` recursively chmods app-data `a+rwx` on lifecycle actions, even while containers may still be running. No app-level chmod can prevent a privileged concurrent chmod. Startup/refresh repairs do not close that race. Before claiming continuous 0600/0700 persistence across lifecycle, stage a compatible private mount outside that recursive operation or repair lifecycle behavior with whole-app acceptance.

Do not change mounts blindly: an empty new volume would hide the current connection. Migration must preserve original bytes and one refresh writer; prove actual read, inference and refresh with no account/provider fallback, and generation-current rollback before considering plaintext removal. No default model switch, credential rotation, token revocation or live restart is authorized here. Container/browser acceptance belongs on the forum queue, not the agent VPS.
