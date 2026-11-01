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


## Explicit fresh-install initialization (staged, not released)

Both one-shots explicitly declare `x-runtipi.wizard_grants: false`. This requires
the source-only WAM service opt-out repair: older managers silently ignore the
field and still inject `/wizard`, `/wizard-requests` and their environment. Do
not claim a data-only initializer on those managers. The declaration only removes
connection injection, not shares or owner overrides. Acceptance must inspect the
complete final post-storage/network Compose config and daemon inventory; normal
front/engine grants and request functionality must remain unchanged. No release,
runtime/inference change or live install is performed here.

Reuse: EXTEND the app-owned recipe initializer; REUSE existing app credential writers and installed runtime identity 1000:1000. No view/shared shell function changes.

Docker-created bind mounts hide the image-owned directory. The recipe now gates engine startup on two bounded one-shots: root with only CHOWN prepares an **empty root-owned** mount, then 1000:1000 with ALL capabilities dropped validates metadata through nofollow directory FDs. Both have no network, read-only rootfs and no-new-privileges. Validation mounts the exact data directory read-only. This split needs no DAC_OVERRIDE, DAC_READ_SEARCH, FOWNER, SETUID or SETGID. Existing runtime services retain their original nonroot/read-only/cap-drop contract.

Existing runtime-owned data is not chmoded, chowned, parsed or rewritten by initialization. The validator rejects foreign ownership, symlinks, hard-linked regular files, special files and device-crossing trees. Partial existing runtime-owned trees pass without creating missing subdirectories; normal app writers create them privately. There is no recursive chmod/chown and no UID fallback. All normal app data paths and grants remain unchanged.

A populated root-owned directory, wrong UID/GID, or invalid link fails closed. This can expose an already-incompatible historical install: stop that app through an approved maintenance window, make a byte-preserving/ownership-preserving backup, classify the exact canonical store, and stage a separately approved migration. Do not fix this by recursive chown or by substituting an empty volume. No backup or automatic migration is performed by the initializer.

For an empty 0:0 root only, mode is set0700 before ownership is handed to1000:1000. A failed chown restores the original empty-root mode while it is still0:0. If ownership transfer succeeded but subsequent validation/startup fails, retain the prepared directory; do not blindly roll ownership back after a runtime may have written. Rollback is an approved stopped-app restore of the current canonical generation and its recorded metadata, not a stale credential copy. Returning to the old recipe is not a data rollback.

The inline helper comes from prepare_private_data.py; refresh with python3 tools/inline-private-data.py. The pinned official Python image already supplies stdlib Python, so no helper image build and no APP_SOURCE_DIR support are required. Run test_private_initialization.py with PyYAML in the test interpreter. Its mocked ownership failures are source regressions, not real cross-UID evidence; the separate forum container fixture exercises genuine0:0 to1000:1000 and1101:2202 ownership transitions.

On an old WAM release the recipe can initialize fresh storage, but the old manager's later recursive a+rwx still defeats private modes; compatibility is not continuous privacy. The patched WAM lifecycle must be separately approved. Container nofollow checks cannot detect a host bind-source symlink already resolved by the privileged Docker daemon; host installer/daemon trust remains necessary. No encryption, same-UID isolation, Docker isolation or live migration is claimed.
