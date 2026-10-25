"use strict";
const $ = (id) => document.getElementById(id);
let loginId = "";
let busy = false;
let timer = null;

function say(text, kind) {
  const m = $("message");
  m.textContent = text || "";
  m.className = kind || "";
}

async function call(path, body) {
  let res;
  try { res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json", "X-Wizard-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  }); } catch (_) { return {ok: false, message: "The ChatGPT app is unreachable. Try again."}; }
  let data = {};
  try { data = await res.json(); } catch (_) { data = { ok: false, message: "No answer from the app." }; }
  return data;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function button(label, onClick, primary) {
  const b = el("button", primary ? "primary" : "", label);
  b.type = "button";
  b.disabled = busy;
  b.addEventListener("click", onClick);
  return b;
}

function render(accounts) {
  const list = $("accounts");
  list.replaceChildren();
  for (const a of accounts) {
    const li = el("li", "row");
    li.append(el("div", "name", a.name), el("div", "detail", a.detail || ""));
    const row = el("div", "buttons");
    if (a.status === "connected") {
      const who = a.account ? ` (${a.account})` : "";
      li.append(el("p", "state ok", `Connected${who}`));
      li.append(el("p", "state", `Hermes: ${a.integration || "not registered"}. Your default model is unchanged.`));
      row.append(button("Test", testIt, true));
      row.append(button("Disconnect", disconnect));
    } else if (a.status === "not_connected" || a.status === "expired") {
      li.append(el("p", "state off", a.status === "expired" ? "Login expired — reconnect" : "Not connected"));
      row.append(button("Connect", connect, true));
    } else {
      li.append(el("p", "state bad", a.problem || "Something is wrong with this app."));
    }
    if (row.childElementCount) li.append(row);
    list.append(li);
  }
}

async function refresh() {
  const data = await call("api/accounts");
  if (data.accounts) render(data.accounts);
}

async function run(text, fn) {
  busy = true;
  say(text);
  await refresh();
  try { await fn(); } finally { busy = false; await refresh(); }
}

function stopPolling() {
  if (timer) clearTimeout(timer);
  timer = null;
}

async function pollOnce() {
  if (!loginId) return;
  const data = await call("api/chatgpt/poll", { login_id: loginId });
  if (data.status === "pending") { timer = setTimeout(pollOnce, 3000); return; }
  stopPolling();
  loginId = "";
  $("login").hidden = true;
  say(data.message, data.ok ? "ok" : "bad");
  await refresh();
}

async function copyCode() {
  const code = $("code");
  if (!code.value || $("login").hidden) return;
  let copied = false;
  try {
    await navigator.clipboard.writeText(code.value);
    copied = true;
  } catch (_) {
    // Older browsers and denied clipboard permissions: select the visible code.
    code.focus();
    code.select();
    code.setSelectionRange(0, code.value.length);
    try { copied = document.execCommand("copy"); } catch (_) { /* Keep selected for manual copy. */ }
  }
  $("copy-code").textContent = copied ? "Copied" : "Copy";
  $("copy-status").textContent = copied ? "" : "Couldn't copy automatically. Press and hold the selected code, then choose Copy.";
}

$("copy-code").addEventListener("click", copyCode);

function connect() {
  return run("Asking ChatGPT for a sign-in code…", async () => {
    const data = await call("api/chatgpt/connect", {});
    if (!data.ok) return say(data.message, "bad");
    loginId = data.login_id;
    $("login-url").href = data.url;
    $("code").value = data.code;
    $("copy-code").textContent = "Copy";
    $("copy-status").textContent = "";
    $("login").hidden = false;
    say("Waiting for ChatGPT approval… This page will show Connected when you finish.", "waiting");
    stopPolling();
    timer = setTimeout(pollOnce, 3000);
  });
}

function testIt() {
  return run("Testing the ChatGPT connection…", async () => {
    const data = await call("api/chatgpt/test", {});
    say(data.message, data.ok ? "ok" : "bad");
  });
}

function disconnect() {
  if (!confirm("Disconnect ChatGPT? Your agent stops using your ChatGPT subscription until you connect again.")) return;
  return run("Disconnecting…", async () => {
    const data = await call("api/chatgpt/logout", {});
    say(data.message, data.ok ? "ok" : "bad");
  });
}

$("cancel").addEventListener("click", async () => {
  stopPolling();
  $("login").hidden = true;
  loginId = "";
  await call("api/chatgpt/cancel", {});
  say("");
});

history.replaceState({chatgpt: "root"}, "");
history.pushState({chatgpt: "guard"}, "");
let lastRootBack = 0;
addEventListener("popstate", () => {
  if (loginId) {
    stopPolling();
    loginId = "";
    $("login").hidden = true;
    call("api/chatgpt/cancel", {});
    say("");
    history.pushState({chatgpt: "guard"}, "");
  } else if (Date.now() - lastRootBack > 2000) {
    lastRootBack = Date.now();
    say("Press back again to exit.");
    history.pushState({chatgpt: "guard"}, "");
  }
});

refresh();
