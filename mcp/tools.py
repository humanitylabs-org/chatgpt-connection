# Read-only by design: connecting, switching and disconnecting are owner actions on the ChatGPT page.
# The MCP reports app-owned wizard.ai-connection/2 status and usage, never a token.
# Box-only: a call that came through Tailscale Serve, a tunnel or a proxy is refused; agents reach this
# MCP on the app network (http://wizard-chatgpt:8801/mcp), which adds none of these headers.

PROXY_HEADERS = ("tailscale-user-login", "x-forwarded-for", "forwarded", "x-real-ip")


@tool("connection_status", "ChatGPT subscription status on this Wizard: connected / not_connected / expired / error, "
      "plan and usage from the app-owned login, and an error message that says what to do. "
      "Read-only; never returns a token.")
def connection_status():
    if any(k in PROXY_HEADERS or k in ("origin", "referer", "cookie", "via") or k.startswith(("cf-", "x-forwarded-", "sec-fetch-")) for k in REQUEST.get()):
        raise PermissionError("box-only: call this MCP from the server (app network), not through the tailnet or a proxy")
    headers = {}
    auth = REQUEST.get().get("authorization")
    if auth:
        headers["Authorization"] = auth  # the page password (Basic), forwarded unchanged
    _, raw = http("GET", "/api/connection", None, headers, timeout=15)
    d = json.loads(raw)
    if d.get("status") == "not_connected":
        d["next"] = ("Ask the owner to open the ChatGPT app, press Connect, open the ChatGPT link and enter the code "
                     "shown. Never ask for the code or a token in chat.")
    elif d.get("status") in ("error", "expired"):
        d["next"] = "Tell the owner error.message; reconnect in this app when expired, never in Hermes."
    return d
