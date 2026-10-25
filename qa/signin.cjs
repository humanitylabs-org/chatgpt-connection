"use strict";
// Focused UI regression: real static page + security headers, mocked device flow.
const {chromium, devices} = require("playwright");
const fs = require("fs"), path = require("path"), cp = require("child_process");
const ROOT = path.resolve(__dirname, ".."), ORIGIN = "http://127.0.0.1:18841";
const OUT = process.env.QA_OUT || path.join(__dirname, "output", "signin");
const CODE = "ABCD-1234", DEVICE_URL = "https://auth.openai.com/codex/device";
const assert = (value, message) => { if (!value) throw Error(message); };
fs.mkdirSync(OUT, {recursive: true});
const child = cp.spawn(process.env.QA_PYTHON || "/usr/bin/python3", [path.join(__dirname, "host.py")], {
  cwd: ROOT, env: process.env, stdio: ["ignore", "pipe", "pipe"],
});
let logs = "";
child.stdout.on("data", x => logs += x); child.stderr.on("data", x => logs += x);

async function fixture(context) {
  const state = {approved: false, pending: 0, cancels: 0};
  await context.route(`${ORIGIN}/api/**`, async route => {
    const endpoint = new URL(route.request().url()).pathname;
    let data;
    if (endpoint === "/api/accounts") data = {accounts: [{name: "ChatGPT", status: state.approved ? "connected" : "not_connected", integration: "registered"}]};
    else if (endpoint === "/api/chatgpt/connect") data = {ok: true, login_id: "fixture-login", code: CODE, url: DEVICE_URL};
    else if (endpoint === "/api/chatgpt/poll") {
      if (!state.approved) state.pending++;
      data = state.approved ? {ok: true, status: "connected", message: "Connected to ChatGPT."} : {ok: true, status: "pending"};
    } else if (endpoint === "/api/chatgpt/cancel") { state.cancels++; data = {ok: true}; }
    else throw Error(`Unexpected API request: ${endpoint}`);
    await route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify(data)});
  });
  // Never contact the provider, including when testing the real new-tab anchor.
  await context.route(DEVICE_URL, route => route.fulfill({contentType: "text/html", body: "<title>Mock ChatGPT device page</title>Mock provider; no login."}));
  return state;
}

async function until(fn, message) {
  for (let i = 0; i < 100; i++) { if (await fn()) return; await new Promise(r => setTimeout(r, 100)); }
  throw Error(message);
}

(async () => {
  let browser;
  const results = [];
  try {
    await until(async () => { try { return (await fetch(`${ORIGIN}/health`)).ok; } catch { return false; } }, "QA host readiness");
    browser = await chromium.launch({headless: true, executablePath: process.env.QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"});
    for (const mode of ["mobile", "desktop"]) {
      const context = await browser.newContext({
        ...(mode === "mobile" ? {...devices["Galaxy S9+"], deviceScaleFactor: 1, viewport: {width: 412, height: 915}} : {viewport: {width: 1280, height: 915}}),
        colorScheme: "dark", permissions: ["clipboard-read", "clipboard-write"],
      });
      const state = await fixture(context), page = await context.newPage(), errors = [];
      page.on("pageerror", e => errors.push(e.message));
      await page.goto(`${ORIGIN}/qa/host.html`);
      const app = page.frames().find(f => f.url() === `${ORIGIN}/`);
      assert(app, "app iframe loaded");
      await app.waitForFunction(() => document.documentElement.dataset.appearanceSource === "transport");
      const identity = await app.evaluate(() => window.__qaIdentity = crypto.randomUUID());
      const connect = async () => {
        await app.getByRole("button", {name: "Connect", exact: true}).click();
        await app.locator("#login").waitFor({state: "visible"});
        assert(await app.locator("#code").inputValue() === CODE, "device code rendered exactly");
      };
      await connect();
      assert(JSON.stringify(await app.locator(".login-steps h3").allTextContents()) === JSON.stringify([
        "Step 1: Copy this code", "Step 2: Open this link", "Step 3: Paste the code",
      ]), "numbered instructions in time order");
      assert(await app.locator("#login-url").getAttribute("target") === "_blank", "new-tab anchor");
      assert((await app.locator("#login-url").getAttribute("rel")).includes("noopener"), "no opener access");
      const csp = (await page.request.get(`${ORIGIN}/`)).headers()["content-security-policy"];
      assert(csp.includes("script-src 'self'"), "production script CSP retained");
      for (const [skin, scheme] of [["nebula", "dark"], ["sepia", "light"], ["signal", "dark"]]) {
        await page.evaluate(s => setSkin(s), skin);
        await app.waitForFunction(s => document.documentElement.dataset.skin === s, skin);
        const layout = await app.evaluate(() => {
          const code = document.getElementById("code"), link = document.getElementById("login-url"), cancel = document.getElementById("cancel");
          const rect = code.getBoundingClientRect(), lr = link.getBoundingClientRect();
          return {width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth,
            font: parseFloat(getComputedStyle(code).fontSize), codeTop: rect.top, linkTop: lr.top,
            inputFits: code.scrollWidth <= code.clientWidth + 1, linkHeight: lr.height,
            cancelBottom: cancel.getBoundingClientRect().bottom, height: innerHeight,
            canvas: getComputedStyle(document.body).backgroundColor,
            scheme: getComputedStyle(document.documentElement).colorScheme,
            radius: getComputedStyle(link).borderRadius, identity: window.__qaIdentity};
        });
        assert(layout.width === (mode === "mobile" ? 412 : 1280), "actual CSS viewport matches device");
        assert(!layout.overflow && layout.inputFits, "no clipped code or horizontal overflow");
        assert(layout.font >= 32 && layout.codeTop < layout.linkTop, "large code above link");
        assert(layout.linkHeight >= 44 && layout.cancelBottom < layout.height, "touch controls visible without scrolling");
        assert(layout.scheme === scheme && layout.canvas === "rgba(0, 0, 0, 0)", "hosted skin and transparent canvas");
        assert(layout.identity === identity && await app.locator("#code").inputValue() === CODE, "live skin change retains document and code");
        if (skin === "signal") assert(layout.radius === "2px", "Signal shape");
        await page.screenshot({path: path.join(OUT, `chatgpt-signin-${mode}-${scheme}-${skin}.png`)});
        results.push({mode, skin, scheme, ...layout});
      }
      await app.getByRole("button", {name: "Copy", exact: true}).click();
      await app.getByRole("button", {name: "Copied", exact: true}).waitFor();
      assert(await page.evaluate(() => navigator.clipboard.readText()) === CODE, "real Clipboard API copies exact code");
      const popupPromise = page.waitForEvent("popup");
      await app.locator("#login-url").click();
      const popup = await popupPromise;
      await popup.waitForLoadState();
      assert(popup.url() === DEVICE_URL && await popup.evaluate(() => opener === null), "provider opens separately with no opener");
      await popup.close(); await page.bringToFront();
      await until(() => state.pending > 0, "pending poll observed");
      assert(await app.locator("#login").isVisible() && (await app.locator("#message").textContent()).includes("Waiting"), "pending remains visibly waiting");
      // Exercise both rejected and missing modern APIs with the real legacy copy.
      for (const missing of [false, true]) {
        await app.evaluate(missing => {
          Object.defineProperty(navigator, "clipboard", {configurable: true, value: missing ? undefined : {writeText: () => Promise.reject(Error("permission denied"))}});
        }, missing);
        await page.evaluate(() => navigator.clipboard.writeText("not-the-code"));
        await app.locator("#copy-code").click();
        await until(() => page.evaluate(() => navigator.clipboard.readText()).then(v => v === CODE), "real execCommand fallback copies exact code");
        assert(await app.locator("#copy-code").textContent() === "Copied", "fallback confirms copy");
      }
      await app.evaluate(() => { document.execCommand = () => false; });
      await app.locator("#copy-code").click();
      await app.locator("#copy-status").filter({hasText: "Couldn't copy"}).waitFor();
      assert(await app.locator("#copy-code").textContent() === "Copy", "failed copy never claims success");
      assert(await app.locator("#code").evaluate(e => e.selectionStart === 0 && e.selectionEnd === e.value.length), "manual-copy fallback selects full code");
      await app.evaluate(() => history.back());
      await app.locator("#login").waitFor({state: "hidden"});
      await until(() => state.cancels === 1, "Back cancels nested login");
      assert(await app.evaluate(() => window.__qaIdentity) === identity, "nested Back never exits app");
      await connect();
      assert(await app.locator("#copy-code").textContent() === "Copy" && await app.locator("#copy-status").textContent() === "", "fresh attempt resets copy feedback");
      await app.getByRole("button", {name: "Cancel", exact: true}).click();
      await until(() => state.cancels === 2, "Cancel sends existing cancel API");
      await connect();
      state.approved = true;
      await app.getByRole("button", {name: "Test", exact: true}).waitFor();
      assert(await app.locator("#login").isHidden() && (await app.locator("#accounts").textContent()).includes("Connected"), "approval replaces steps with Connected");
      assert((await app.locator("#accounts").textContent()).includes("Your default model is unchanged"), "default model invariant remains visible");
      await page.screenshot({path: path.join(OUT, `chatgpt-signin-${mode}-connected.png`)});
      await app.evaluate(() => history.back());
      await app.locator("#message").filter({hasText: "Press back again"}).waitFor();
      assert(await app.evaluate(() => window.__qaIdentity) === identity && errors.length === 0, "root Back stays and no JS errors");
      results.push({mode, clipboard: "native + rejected/missing API legacy fallback + failure selection", navigation: "new tab + nested/root Back + Cancel", status: "pending then Connected", pageErrors: errors});
      await context.close();
    }
    // Narrow-phone standalone dark fallback, with the same mocked API.
    const context = await browser.newContext({viewport: {width: 320, height: 915}, colorScheme: "dark"});
    await fixture(context);
    const page = await context.newPage();
    await page.goto(`${ORIGIN}/`);
    await page.getByRole("button", {name: "Connect", exact: true}).click();
    await page.locator("#login").waitFor({state: "visible"});
    assert(await page.evaluate(() => document.documentElement.scrollWidth === innerWidth && getComputedStyle(document.documentElement).colorScheme === "dark"), "320px standalone dark fallback without overflow");
    await page.screenshot({path: path.join(OUT, "chatgpt-signin-standalone-dark-320.png")});
    await context.close();
    fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({pass: true, results}, null, 2));
    console.log(JSON.stringify({pass: true, results}));
  } finally {
    if (browser) await browser.close();
    child.kill("SIGTERM");
    if (child.exitCode === null) await new Promise(resolve => child.once("exit", resolve));
    fs.writeFileSync(path.join(OUT, "server.log"), logs);
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
