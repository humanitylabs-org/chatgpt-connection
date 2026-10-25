# Disposable Appearance proxy (not a live Wizard install)

Run lightweight browser QA on the browser/QA machine, not the control-plane host.
Use a work directory and TMPDIR on its external work volume. No Docker, owner
credentials or real OAuth is involved. Install Playwright in this directory:

```sh
npm install --prefix qa playwright --no-audit --no-fund
TMPDIR=<private-external-volume-scratch> node qa/check.cjs
TMPDIR=<private-external-volume-scratch> node qa/signin.cjs
```

On macOS the harness uses `/usr/bin/python3` and system Google Chrome. Override
`QA_PYTHON` / `QA_CHROME` on another platform. Loopback port 18841 must be free.
The process starts/stops its own fake OAuth/Codex + real app server. Screenshot,
console and JSON evidence land under ignored `qa/output/`.

`skins.json` contains exact Nebula, Signal and Sepia token snapshots from the real
Wizard skin registry with its SHA-256 provenance. The same-origin parent sends
Appearance v1 messages; actual app security headers stay enabled. The test checks
transparent canvas, live skin change without losing the login code/document,
Signal square controls, Connect → nested Back → Connect → approval → Test →
Disconnect, root Back staying in-app, and no JS page errors. It runs desktop and
Pixel mobile-UA/touch profiles, then captures the standalone dark fallback.

`qa/signin.cjs` adds the numbered-step regression against mocked device-code API
responses while serving the real app's HTML, scripts, styles and CSP. It runs a
Samsung mobile-UA/touch profile at 412×915 and a 1280×915 desktop viewport; the
full-width hosted app is 412×875 below the proxy's 40px label. It captures Nebula
(dark), Sepia (light) and Signal (dark) token transport, checks large unclipped code
above the link, real Clipboard API readback, real `execCommand` readback after
missing/rejected Clipboard API, honest manual-copy selection on failure, a
separate no-opener provider tab, pending → Connected, fresh-attempt feedback reset,
Cancel, and nested/root Back. A 320px standalone dark capture checks narrow layout.
It never contacts OpenAI. `QA_OUT` overrides its default `qa/output/signin/` evidence
directory. These are token-proxy and Chromium checks, not full skin-package,
physical Samsung, Brave, or real OAuth acceptance.

This is **hosted-proxy coverage**, not an authenticated real-shell install or a
physical-device claim. Production promotion/image build remains outside this branch.
