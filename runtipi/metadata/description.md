# ChatGPT — 0.3.0

Connect your ChatGPT Plus or Pro subscription in this app. The app, not Hermes,
stores and renews the login in its private app-data bind mount.

1. Open this page and press **Connect**.
2. Open the OpenAI device link, sign in and approve the short code there.
3. Return to **Connected**. **Test** checks models/quota and Hermes registration.
4. Explicitly select native **OpenAI Codex** in Hermes when you want to use it.
   Connecting never changes your current default model.

Hermes receives only a per-install box-local bearer. Model requests, native
compaction and quota go through this app; no ChatGPT credential or refresh token
belongs in Hermes. The named **ChatGPT app** entry is a custom fallback without
native web search/quota/compaction; choose native OpenAI Codex for those features.
A pre-existing Hermes Codex login causes a registration conflict, not a takeover.

**Disconnect** deletes the local ChatGPT login; in-flight replies may finish.
Move the complete app-data folder only after stopping the old copy and callers;
never run two boxes from one rotating refresh chain. Keep app-data/backups private.

Keep the app on a trusted private network; the owner page can have a password.
The model gateway requires its local bearer and refuses browser/forwarded headers.
Other apps use Connection Router. Usage/status come from this app's own login.

This is a source package: App Manager runs the exact installed repository revision
read-only on a digest-pinned official Python image. No custom engine image build is
needed. See README for verification, safe migration and owner acceptance gates.
