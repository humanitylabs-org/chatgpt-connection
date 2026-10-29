# Gateway burst admission

## Preflight and scope

Standard delivery; **consequential shared inference runtime admission surface**.
REUSE the existing `Server`, `Handler.gateway`, account owner and MCP byte-pipe.
EXTEND gateway admission only; no UI, Hermes core, new service, dependency,
provider/model selection, upstream ceiling or account-renewal changes.

The base is the exact installed source commit
`10887eba131b738b865c8725d72172a8c5755d14`, not newer main. Before promotion,
reconcile the live source again: another release may have landed during QA.

## Bounds and behavior

- Eight active upstream gateway requests, unchanged. Native replies and helpers
  share this cap, along with models, quota and compaction.
- At most 32 queued requests; waiting requests keep their bodies in the existing
  transport, not in a new application buffer. One `threading.Condition` and FIFO
  ticket deque replace the old immediate-failure semaphore. A new arrival cannot
  bypass an existing waiter.
- Admission plus body upload has one absolute dispatch deadline measured from
  gateway entry: 15 seconds for POST; 2 seconds for GET/HEAD. The SDK's
  `X-Stainless-Timeout` can shorten this, with 0.25 seconds of headroom; it cannot
  increase the cap. Deadline expiry is checked before taking a permit and again
  immediately before calling the account owner's upstream method. Body reads use
  the remaining budget, not a timeout reset by each arriving chunk.
- These are **dispatch/admission** deadlines, not a newly shortened generation
  limit. Existing 120-second upstream I/O and 600-second stream/size limits stay
  unchanged after dispatch. The app cannot infer an unadvertised caller deadline;
  callers that leave are removed by socket cancellation detection.
- Inspected caller source: native usage probe uses 10 seconds; helper API kwargs
  default to 30 seconds. The shipped MCP front's ordinary byte-pipe has no added
  response-header timeout (the separate `http(..., timeout=60)` is for MCP tools).
  The short GET and POST admission caps fit inside those known caller budgets.
- Linux `poll(POLLRDHUP|POLLHUP|POLLERR|POLLNVAL)` detects a cancelled waiter even
  with unread POST data. FIN/half-close is treated as cancellation, as is reset.
  The shipped front propagates client close to the engine connection. A final
  check happens immediately before dispatch. As with any socket check, a close
  racing *after* dispatch is handled by the existing streaming cleanup, not by
  an assertion that an already-started upstream request never existed.
- Full queue or dispatch-deadline expiry returns HTTP 429 with the existing
  top-level `error` field, standard nested message/code, `code: gateway_busy`,
  `type: local_capacity`, distinct `detail: queue_full|deadline`, and
  `Retry-After: 1`. It explicitly says no request was sent upstream; it is not an
  OpenAI quota or login error. Failures after upstream dispatch never use that
  local-busy message.
- Every admitted request releases in `finally`, including upstream failure,
  malformed/oversized body, streaming error and client disconnect. Timeout or
  cancellation removes the ticket and notifies the next caller.
- `/health` adds only aggregate `active`, `waiting`, `rejected`, `cancelled`,
  `capacity`, `max_waiters`. `rejected` counts failed admission acquisitions;
  `cancelled` counts callers removed while acquiring. No identifiers, prompts,
  headers, tokens or per-agent data are exposed. Existing health fields remain.

Auth, box-only/forwarded-header refusal, route allowlists, 16 MiB gateway body
limit, account-owned renewal and model/quota shapes are unchanged. This does not
replay failed turns or silently change a model/account.

## Offline acceptance

`python3 -m unittest discover -v -p 'test_gateway*.py'` is included in normal CI.
Ten regressions use the real HTTP handler and loopback synthetic upstream:
8 held SSE streams; FIFO ninth/tenth single dispatch; cap never exceeds eight;
32 waiting and the next rejected; advertised deadline with no stale dispatch;
FIN/reset with unread queued body; cancellation through the shipped front;
absolute slow-upload deadline; upstream and started-stream failure cleanup;
active-client reset and repeated leak-free waves; models/quota/compaction;
security/body-size checks before upstream.

The forum runner `tools/verify-gateway-admission.py` checks full source identity
and clean trees, runs the new FIFO regression against the unchanged live base
and requires the **specific observable assertion failure**, runs current CI
entrypoints and ordinary repository-wide discovery, repeats the new suite, and
writes phase logs, hashes and `receipt.json`. Discovery must run all ten new
regressions, not zero tests. The baseline is copied into disposable evidence;
its staged checkout remains unchanged. Only the coordinator submits the durable
forum light-lane job. Syntax checks on the VPS do not count as concurrency proof.

## Promotion and rollback (not performed by this repair)

Independent read-only review and real test evidence precede activation. The
coordinator must coordinate an idle-safe drain of all callers: restarting this
shared inference gateway cuts active streams; the Agent Service keeper does not
protect it. Do not add an unrelated owner-approval gate to this authorized fix.

Use the already installed WAM source-pin resolver followed by its App Manager
`app_update` integration, **not** a raw Docker restart or unpinned `main` update.
The build report supplies the exact candidate and executable command after it
is pushed. A bare `wam app update wizard-chatgpt:core` alone does not select the
candidate source. The unchanged rollback source is
`10887eba131b738b865c8725d72172a8c5755d14`, with the same digest-pinned Python
runtime and existing data mount. Read back the source bind/full SHA, health,
selected route/model and connection after any future activation; never claim
installation from a lifecycle request's acknowledgement alone.
