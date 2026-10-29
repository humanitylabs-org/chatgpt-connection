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
  increase the cap. Deadline expiry is checked before taking a permit, after
  body upload, and inside the real account owner's `Connection.open`: **after**
  `access()` (including lock waits, refresh and refresh persistence), immediately
  before **each** backend send, including a 401 retry. Body reads use
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
  check happens after account access immediately before dispatch/retry. As with any socket check, a close
  racing *after* dispatch is handled by the existing streaming cleanup, not by
  an assertion that an already-started upstream request never existed.
- Full queue or dispatch-deadline expiry returns HTTP 429 with the existing
  top-level `error` field, standard nested message/code, `code: gateway_busy`,
  `type: local_capacity`, distinct `detail: queue_full|deadline`, and
  `Retry-After: 1`. It explicitly says no request was sent upstream; it is not an
  OpenAI quota or login error. Failures after upstream dispatch never use that
  local-busy message. If the first backend attempt returned 401 and refreshing
  consumed the remaining deadline, no retry is sent; the response is HTTP 502
  `gateway_retry_stopped` and explicitly says an attempt already occurred (not
  a local-capacity 429 or a claim that nothing was sent). A disconnected caller
  is closed silently. Completed token rotations are persisted even when the
  caller expires or disconnects; a guard never interrupts refresh persistence.
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
Fifteen regressions use the real HTTP handler and loopback synthetic upstream:
8 held SSE streams; FIFO ninth/tenth single dispatch; cap never exceeds eight;
32 waiting and the next rejected; advertised deadline with no stale dispatch;
FIN/reset with unread queued body; cancellation through the shipped front;
absolute slow-upload deadline; upstream and started-stream failure cleanup;
active-client reset and repeated leak-free waves; models/quota/compaction;
security/body-size checks before upstream. Five exercise the **real**
`Connection.open/access` with a synthetic OAuth-refresh barrier: deadline and
disconnect before the first send (zero backend calls), both during 401 refresh
(exactly one failed backend attempt and no second send), and a live-caller 401
retry that succeeds. They verify completed refreshes remain saved and the
gateway permit is released. The other transport fixture alone cannot prove
this account-access boundary.

The forum runner `tools/verify-gateway-admission.py` checks full source identity
and clean trees, runs the new FIFO regression against the unchanged live base
and requires the **specific observable assertion failure**, runs current CI
entrypoints and ordinary repository-wide discovery, repeats the new suite, and
writes phase logs, hashes and `receipt.json`. Discovery must run all fifteen
regressions, not zero tests. The baseline is copied into disposable evidence;
its staged checkout remains unchanged. Only the coordinator submits the durable
forum light-lane job. Syntax checks on the VPS do not count as concurrency proof.

The revision runner `tools/verify-gateway-admission-r2.py` binds candidate and
previous source (`4742b662af5bd0cc0bbde1d5736424b86c3ada9e`) plus the previous
manifest/receipt/log hashes. It first runs the five real-access regressions,
requires all four cancellation/expiry assertions to fail on that previous
candidate, reruns ordinary discovery and affected ChatGPT/inference/package
suites, and repeats the focused five. Only the hash-bound historical live-base
FIFO negative and unchanged MCP inline check are inherited; affected tests are
not claimed from the older result.

## Promotion and rollback (not performed by this repair)

Independent read-only review and real test evidence precede activation. The
coordinator must not confuse an idle observation with a fenced drain. The
installed WAM `app_update` path has no universal ingress fence or inference
drain. Restarting this shared gateway cuts active streams; the Agent Service
keeper only protects its client-to-agent stream, not the inference transport.
The shipped front has no drain endpoint; the live engine uses a semaphore with
no active/waiting counters and has no graceful signal handler. Container stop
grace alone is not an application drain. Native `/v1` and helper `/api/infer`
can reach the app on the shared Docker network, so gating only the host-loopback
front or UI would leave bypasses. No safe zero-interruption activation route is
established by these mechanisms.

Until an existing complete fence is independently demonstrated, the minimal
timing gate is an owner-agreed brief maintenance window: finish current replies
and avoid new ChatGPT-backed work during the update. Autonomous callers could
still arrive; any request in the replace interval can fail, and already active
replies can be interrupted and need an explicit retry. Do not promise replay,
silently change models/accounts, or invent firewall orchestration. This is a
timing decision for activation, not new approval to make the authorized repair.

Use the already installed WAM source-pin resolver followed by its App Manager
`app_update` integration, **not** a raw Docker restart or unpinned `main` update.
The build report supplies the exact candidate and executable command after it
is pushed. A bare `wam app update wizard-chatgpt:core` alone does not select the
candidate source. The unchanged rollback source is
`10887eba131b738b865c8725d72172a8c5755d14`, with the same digest-pinned Python
runtime and existing data mount. Read back the source bind/full SHA, health,
selected route/model and connection after any future activation; never claim
installation from a lifecycle request's acknowledgement alone.
