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
    li.dataset.accountId = a.id;
    li.append(el("div", "name", a.name), el("div", "detail", a.detail || ""));
    if (a.plan) li.append(el("p", "plan", planTitle(a.plan)));
    if (a.preferred) li.append(el("p", "state", "Tried first"));
    if (a.cooling_down) li.append(el("p", "state off", "Cooling down — another connected account can be used meanwhile."));
    renderLimits(li, a.usage);
    if (a.problem) li.append(el("p", "state bad", a.problem));
    const row = el("div", "buttons");
    if (a.status === "connected") {
      const who = a.account ? ` (${a.account})` : "";
      li.append(el("p", "state ok", `Connected${who}`));
      li.append(el("p", "state", `Hermes: ${a.integration || "not registered"}. Your default model is unchanged.`));
      row.append(button("Test", () => testIt(a.id), true));
      row.append(button("Disconnect", () => disconnect(a.id)));
    } else if (a.status === "not_connected" || a.status === "expired") {
      li.append(el("p", "state off", a.status === "expired" ? "Login expired — reconnect" : "Not connected"));
      row.append(button(a.status === "expired" ? "Reconnect" : "Connect", () => connect(a.id), true));
    } else {
      li.append(el("p", "state bad", a.problem || "Something is wrong with this app."));
      row.append(button("Reconnect", () => connect(a.id), true));
    }
    if (a.status === "connected" && a.problem) row.append(button("Reconnect", () => connect(a.id)));
    if (!a.preferred) row.append(button("Use first", () => useFirst(a.id)));
    if (row.childElementCount) li.append(row);
    list.append(li);
  }
}

function planTitle(plan) {
  const names = {free: "Free", plus: "Plus", pro: "Pro", team: "Business", business: "Business", enterprise: "Enterprise", edu: "Education"};
  return `ChatGPT ${names[String(plan).toLowerCase()] || String(plan)}`;
}

function renderLimits(row, usage) {
  const limits = usage?.limits || [];
  if (!limits.length) {
    row.append(el("p", "state off", "Usage not available yet — no estimate shown."));
    return;
  }
  for (const limit of limits) {
    if (!Number.isFinite(limit.usedPercent)) continue;
    const used = Math.max(0, Math.min(100, limit.usedPercent));
    const label = el("label", "limit", `${limit.label || "Usage"}: ${used}% used · ${100 - used}% left`);
    const meter = el("progress");
    meter.max = 100; meter.value = used;
    meter.setAttribute("aria-label", `${limit.label || "Usage"} used`);
    label.append(meter);
    const reset = limit.resetsAt && new Date(limit.resetsAt);
    if (reset && Number.isFinite(reset.getTime())) label.append(el("span", "reset", `Resets ${reset.toLocaleString()}`));
    row.append(label);
  }
}

function useFirst(id) {
  return run("Changing which account is tried first…", async () => {
    const data = await call("api/chatgpt/prefer", {account_id: id});
    say(data.message, data.ok ? "ok" : "bad");
  });
}

let reconnectTried = false;
async function refresh() {
  const data = await call("api/accounts");
  if (data.accounts) {
    render(data.accounts);
    const target = new URLSearchParams(location.search).get("reconnect");
    if (target && !reconnectTried) {
      reconnectTried = true;
      const account = target === "1" ? data.accounts.find(a => a.status === "expired" || a.status === "error" || a.problem) : data.accounts.find(a => a.id === target);
      if (account) connect(account.id);
    }
  }
}

async function run(text, fn) {
  if (busy) return;
  busy = true;
  say(text);
  document.querySelectorAll("button").forEach(b => { if (b.id !== "cancel") b.disabled = true; });
  try { await fn(); } finally { busy = false; $("add").disabled = false; await refresh(); }
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

function connect(id, add = false) {
  return run("Asking ChatGPT for a sign-in code…", async () => {
    const data = await call(add ? "api/chatgpt/add" : "api/chatgpt/connect", id ? {account_id: id} : {});
    if (!data.ok) return say(data.message, "bad");
    loginId = data.login_id;
    $("login-url").href = data.url;
    $("code").textContent = data.code;
    $("login").hidden = false;
    say("Waiting for you to approve the code on ChatGPT…");
    stopPolling();
    timer = setTimeout(pollOnce, 3000);
  });
}

function testIt(id) {
  return run("Testing the ChatGPT connection…", async () => {
    const data = await call("api/chatgpt/test", id ? {account_id: id} : {});
    say(data.message, data.ok ? "ok" : "bad");
  });
}

function disconnect(id) {
  if (!confirm("Disconnect this ChatGPT account? Other connected accounts are kept.")) return;
  return run("Disconnecting…", async () => {
    const data = await call("api/chatgpt/logout", id ? {account_id: id} : {});
    say(data.message, data.ok ? "ok" : "bad");
  });
}

$("add").addEventListener("click", () => connect(null, true));

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
