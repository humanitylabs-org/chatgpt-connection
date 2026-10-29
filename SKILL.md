---
name: wizard-chatgpt
description: Use when checking the ChatGPT app connection or routing stock Hermes through its app-owned Codex login.
---

# ChatGPT app-owned connection

The **ChatGPT app**, not Hermes, stores and renews the subscription login in its
private app-data bind mount. Never read/copy/print its credential or gateway key.
Never ask for passwords, device codes or tokens in chat.

- Owner login: open the app page, **Connect**, approve the short code at OpenAI,
  return to Connected. **Test** verifies models/quota and retries registration.
  **Disconnect** removes local login; in-flight replies may finish.
- MCP `connection_status` or box-only `GET /api/connection` reports
  `wizard.ai-connection/2`: status, plan, safe usage, models and registration.
  Account IDs/tokens are never returned. `expired` means reconnect **in this app**,
  not `hermes auth add openai-codex`.
- Connecting **never changes the Hermes default model**. For full native features,
  the owner explicitly selects `openai-codex`; `HERMES_CODEX_BASE_URL` points to
  this app's `/v1`, and Hermes holds only the opaque app bearer in an API-key pool
  entry. No real ChatGPT token or refresh token belongs in Hermes.
- The dashboard also advertises a named custom **ChatGPT app** fallback. Selecting
  that custom entry loses native web search/quota/compaction; use native OpenAI
  Codex for those. Existing non-app Codex pool entries cause a registration
  conflict rather than a silent takeover. A fresh Hermes process may be needed.
- Other apps declare model requirements and use Connection Router, not the owner
  UI. `/api/infer` validates explicit text only, with no persona/memory/session.
  `done` must name the effective approved model; provisional text is not success.
  Never work around an error by switching model, identity or account.
- Gateway requests require the box-local bearer; browser/forwarded headers are
  refused even with it. The gateway runs at most eight upstream requests, with
  a bounded FIFO for short bursts. A `gateway_busy` / `local_capacity` 429 is
  local admission pressure, not an upstream quota or login failure; it includes
  `Retry-After: 1`. Never switch model/account or reconnect to work around it.
  Router grants are report-only unless enforced: keep all
  model traffic on the private app network. Page passwords are not model keys.
- Migration: **stop old app and callers first**, copy the entire app-data folder
  with its modes/owner, start only the new copy, Test and re-register. Rotation
  makes old backups stale. Never run two boxes from one rotating token chain.
- `0.3.0` uses `runtime.package: source`: App Manager mounts the exact installed
  repository revision read-only on a digest-pinned official Python image. No
  custom image build is required. Do not claim a branch is installed without readback.

See README for routing config, storage/backups, test commands, migration and the
remaining owner-approved real device-login/renewal acceptance steps.
