"use strict";
// Synthetic container only. Source bytes are baked into the candidate image.
const {chromium, devices} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const base = process.env.QA_BASE, out = process.env.QA_OUT;
const SECOND = 'acct-' + 'a'.repeat(24), THIRD = 'acct-' + 'b'.repeat(24);
fs.mkdirSync(out, {recursive: true});
(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.QA_CHROME ? {executablePath: process.env.QA_CHROME} : {})});
  const receipts = [];
  try {
    for (const mode of ['desktop', 'phone']) {
      const context = await browser.newContext(mode === 'phone' ? {...devices['Pixel 7'], viewport: {width: 412, height: 860}, colorScheme: 'dark'} : {viewport: {width: 1100, height: 1000}, colorScheme: 'dark'});
      const page = await context.newPage(), errors = [], requests = [];
      page.on('pageerror', e => errors.push(e.message));
      page.on('request', r => requests.push(r.url()));
      await page.goto(base + '/qa/host.html');
      const frame = page.frameLocator('iframe');
      await frame.locator('[data-account-id="chatgpt-subscription"] .name').filter({hasText: 'alice'}).waitFor();
      const app = page.frames().find(f => f.url() === base + '/');
      await app.waitForFunction(() => document.documentElement.dataset.appearanceSource === 'transport');
      await frame.locator('[data-account-id="chatgpt-subscription"]').getByRole('button', {name: 'Reconnect', exact: true}).waitFor();
      await frame.locator(`[data-account-id="${SECOND}"]`).getByText('ChatGPT Plus', {exact: true}).waitFor();
      await frame.locator(`[data-account-id="${THIRD}"]`).getByText('ChatGPT Pro', {exact: true}).waitFor();
      await frame.getByText('Cooling down — another connected account can be used meanwhile.', {exact: true}).waitFor();
      assert.ok((await frame.locator('#accounts').innerText()).includes('100% used · 0% left'));
      const identity = await app.evaluate(() => window.__qaIdentity ||= crypto.randomUUID());
      for (const skin of ['nebula', 'signal']) {
        await page.evaluate(s => setSkin(s), skin);
        await app.waitForFunction(s => document.documentElement.dataset.skin === s, skin);
        assert.equal(await app.evaluate(() => window.__qaIdentity), identity);
        assert.equal(await app.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        const image = path.join(out, `chatgpt-${mode}-${skin}-accounts.png`);
        await page.screenshot({path: image, fullPage: true});
        // Also capture iframe's whole scrollable account list, not just the window crop.
        const full = path.join(out, `chatgpt-${mode}-${skin}-account-list.png`);
        await frame.locator('main').screenshot({path: full});
        receipts.push({mode, skin, image, full, skinTransport: true, sameDocument: true});
      }
      // Drive every Use first button, including a cooling and a broken sign-in.
      for (const id of [SECOND, THIRD, 'chatgpt-subscription']) {
        const row = frame.locator(`[data-account-id="${id}"]`);
        if (!(await row.innerText()).includes('Tried first')) {
          await row.getByRole('button', {name: 'Use first', exact: true}).click();
          await row.getByText('Tried first', {exact: true}).waitFor();
          const response = await context.request.get(base + '/api/accounts');
          const state = await response.json();
          assert.equal(state.accounts[0].id, id); // Read back the exact changed target.
          assert.equal(state.accounts[0].preferred, true);
        }
      }
      await frame.locator('[data-account-id="chatgpt-subscription"]').getByRole('button', {name: 'Reconnect', exact: true}).click();
      await frame.locator('#code').filter({hasText: 'FAKE-1234'}).waitFor();
      await page.screenshot({path: path.join(out, `chatgpt-${mode}-reconnect.png`), fullPage: true});
      const cancelled = page.waitForResponse(r => r.url() === base + '/api/chatgpt/cancel' && r.request().method() === 'POST');
      await app.evaluate(() => history.back());
      await cancelled;
      await frame.locator('#login').waitFor({state: 'hidden'});
      const response = await context.request.get(base + '/api/accounts');
      assert.equal((await response.json()).accounts.some(a => a.signing_in), false);
      assert.deepEqual(errors, []);
      assert.ok(requests.every(u => new URL(u).origin === new URL(base).origin));
      receipts.push({mode, reconnect: 'row → fake code → Back cancels', prefer: 'every account including broken and cooling; API readback', pageerrors: errors});
      await context.close();
    }
    const context = await browser.newContext({viewport: {width: 412, height: 860}, colorScheme: 'dark'});
    const page = await context.newPage();
    await page.goto(base + '/?reconnect=1');
    await page.locator('#code').filter({hasText: 'FAKE-1234'}).waitFor();
    await page.getByRole('button', {name: 'Cancel', exact: true}).click();
    await page.locator('#login').waitFor({state: 'hidden'});
    await page.screenshot({path: path.join(out, 'chatgpt-standalone-dark.png'), fullPage: true});
    receipts.push({standalone: 'dark', deepLink: '?reconnect=1 opened broken account', syntheticOnly: true, noRealShellClaim: true});
    await context.close();
    fs.writeFileSync(path.join(out, 'chatgpt-browser.json'), JSON.stringify({pass: true, receipts}, null, 2));
    console.log(JSON.stringify({pass: true, checks: receipts.length, screenshots: out}));
  } finally {
    await browser.close();
  }
})().catch(e => {console.error(e); process.exitCode = 1;});
