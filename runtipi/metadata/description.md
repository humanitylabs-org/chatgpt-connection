# ChatGPT

Connect your ChatGPT subscriptions (Plus or Pro) to this Wizard server. Your agent then uses them.

## Sign in

1. In ChatGPT, open **Settings → Security** and turn on **Device code sign-in for Codex** (once).
2. Open the ChatGPT app here and press **Connect** (or **Add account** for another subscription).
3. Copy the code, press **Open sign-in and paste the code**, and approve on ChatGPT.
4. Come back: the page shows the subscription with its usage.

If the page says it can't reach your agent, press **Retry**; if that keeps failing, ask your agent to reconnect the ChatGPT app.

## How it works

- Each sign-in is **your agent's own ChatGPT login**. This app only asks the agent's dashboard to start it and shows you the code. The agent stores and renews the tokens itself; this app never sees them.
- Wizard's home screen shows each subscription and how much of it is left.
- Works with the Hermes Agent app from Wizard Apps or with your own Hermes on this server (Wizard links it).

## Privacy and access

Keep this app on your private network (Tailscale). Public domain exposure is off. The status answer is box-only: requests that come through Tailscale Serve, a tunnel or a proxy are refused. You can also set an optional page password at install.

## Personal use only

This is for using **your own** ChatGPT subscriptions on **your own** server.
