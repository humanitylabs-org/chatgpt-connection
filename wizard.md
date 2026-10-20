---
wizard: 0.2
id: wizard-chatgpt
name: ChatGPT
type: external
category: system
tags: [security]
version: 0.3.0-dev
summary: App-owned ChatGPT login and renewal; stock Hermes uses its box-only model endpoint without ChatGPT credentials.
icon: icon.png
license: MIT
source: https://github.com/humanitylabs-org/chatgpt-connection
runtime:
  kind: runtipi
  config: runtipi/config.json
  compose: runtipi/docker-compose.yml
mcp:
  path: /mcp
  port: 8801
  transport: streamable-http
skill: SKILL.md
view:
  path: /
  port: 8801
provides:
- ai-connection
- chatgpt-subscription
uses:
- hermes-agent
health: /health
---

# ChatGPT

The ChatGPT app owns its Codex device-code login, stores it in its private app-data bind mount, and is the only refresher. Connect / Test / Disconnect are owner actions on its Appearance v1 page. Hermes receives only a per-install box-local bearer, never a ChatGPT credential. Connecting never selects a default model.

`GET /api/connection` and the read-only MCP tool `connection_status` report `wizard.ai-connection/2` with app-owned status, plan, safe usage and models. Connection Router `/api/infer` also uses the app-owned login directly. Stock Hermes uses `/v1/responses`, models, compaction and quota proxy routes. Prefer native `openai-codex` + `HERMES_CODEX_BASE_URL` for native web search, quota and server-side compaction; the named custom fallback loses those features.

This is **0.3.0-dev source only**. The compose engine remains pinned to the previous immutable release pending an authorized image build/pin update. See README for storage, single-refresher migration, verification and the remaining owner device-login gate.
