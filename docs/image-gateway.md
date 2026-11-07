# Native Codex image gateway

The existing box-only bearer gateway additionally accepts exactly:

| Method | Local path | Fixed upstream |
|---|---|---|
| POST | `/v1/images/generations` | `https://chatgpt.com/backend-api/codex/images/generations` |
| POST | `/v1/images/edits` | `https://chatgpt.com/backend-api/codex/images/edits` |

No new login, provider selection, model default, browser access, or generic proxy
is introduced. JSON fields are forwarded unchanged (in particular `model`, `n`,
`quality`, `size`, `background`, and `images`); Responses-only normalization is
not applied. The app still injects its OAuth/account headers, blocks redirects,
filters request headers, and sanitizes upstream errors. It additionally permits
`x-codex-image-turn-id` on these two paths and returns only the image-specific
`x-codex-imagegen-request-id`, never cookies or upstream auth headers.

## Endpoint evidence

Verified against official `openai/codex` commit
`67f0994b532b8740f14b5b738af7b7b2a915b1d6`:

- [`codex-rs/model-provider-info/src/lib.rs`](https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/model-provider-info/src/lib.rs#L86)
  defines `CHATGPT_CODEX_BASE_URL` as `https://chatgpt.com/backend-api/codex`.
- [`codex-rs/codex-api/src/endpoint/images.rs`](https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/codex-api/src/endpoint/images.rs)
  appends `images/generations` / `images/edits` and extracts
  `x-codex-imagegen-request-id`.
- [`codex-rs/ext/image-generation/src/backend.rs`](https://github.com/openai/codex/blob/67f0994b532b8740f14b5b738af7b7b2a915b1d6/codex-rs/ext/image-generation/src/backend.rs)
  sends `x-codex-image-turn-id` through that client.
- Hermes' native image plugin uses that same JSON contract and appends those
  relative paths to its resolved credential base URL. No Hermes changes needed.

## Bounds

- At most 16 raster reference data URLs, each at most 25 MiB decoded, validated
  as base64. The existing plugin remains responsible for raster format checks.
- Total JSON request ceiling accounts for base64 expansion of all 16 references
  plus 1 MiB JSON overhead. Ordinary gateway requests retain the 16 MiB limit.
- Image upload has a 60-second absolute deadline after queue admission. Existing
  admission capacity/FIFO/waiter limits remain unchanged. Only one image request
  may hold its large upload/response working set; additional image requests get
  the existing truthful 429/local-capacity response without an upstream send.
- Image upstream and downstream socket timeouts are 300 seconds, versus the
  unchanged 120 seconds for existing routes. These are socket timeouts, not a
  promise of a 300-second wall-clock deadline including admission and upload.
- Image JSON responses are bounded at 64 MiB (the previous 4 MiB cap would
  truncate generated base64 images). Other response limits remain unchanged.
- The engine's Compose memory ceiling rises from 128 MiB to 4 GiB, with no swap;
  this is a limit, not reserved memory. Supporting the full JSON/base64 edit
  contract cannot work under the previous 128 MiB ceiling. The image-only
  semaphore prevents multiplying that working set; text traffic is unaffected.

## Verification (offline; no owner login)

```sh
python3 -m unittest discover -v -p 'test_gateway*.py'
python3 test-package.py
python3 test-chatgpt.py
python3 test-inference.py -v
python3 mcp/inline.py --check
# Optional full-size ceiling probe, on the build worker in a 4 GiB container:
PYTHONPATH=. python3 tools/verify-image-ceiling.py
sh test-container.sh
```

`test_gateway_images.py` checks both real HTTP routes against a fake upstream,
body/auth/header forwarding, POST-only access, existing path refusals, browser
and proxy-header refusals, 25 MiB edits, reference limits, responses larger than
4 MiB, 300-second transport configuration, bounded errors, permit cleanup, and
the shipped MCP byte-pipe. The ceiling probe sends all 16 maximum-size references
through real HTTP plus the real JSON serialization/transport path, replacing
only the upstream network send with an explicitly synthetic transport.

## Promotion hold

This is a live-baseline repair, based on
`aa9987bf696188bcdb52de12e6d9e32b10340745`, not a wholesale replacement from
GitHub main. **Rebase/integrate with account-controls PR #13 before promotion**
if it has advanced the live app. Do not merge this older frontend over newer
main or #13. Rerun the full suite and live-source parity gate on the combined
head and build a newly pinned candidate. Never edit a revision-named source
mount in place. Preserve the current Python source-package runtime and app-data
mount; use the App Manager's source resolver and managed lifecycle for promotion.
Owner-specific exact commands and rollback live in the private task receipt,
not in this public repository. Staging does not authorize deployment.
