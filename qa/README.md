# Disposable Appearance proxy (not a live Wizard install)

Run lightweight browser QA on the browser/QA machine, not the control-plane host.
Use a work directory and TMPDIR on its external work volume. No Docker, owner
credentials or real OAuth is involved. Install Playwright in this directory:

```sh
npm install --prefix qa playwright --no-audit --no-fund
TMPDIR=<private-external-volume-scratch> node qa/check.cjs
```

On macOS the harness uses `/usr/bin/python3` and system Google Chrome. Override
`QA_PYTHON` / `QA_CHROME` on another platform. Loopback port 18841 must be free.
The process starts/stops its own fake OAuth/Codex + real app server. Screenshot,
console and JSON evidence land under ignored `qa/output/`.

`skins.json` contains exact Nebula and Signal token snapshots from the real
Wizard skin registry with its SHA-256 provenance. The same-origin parent sends
Appearance v1 messages; actual app security headers stay enabled. The test checks
transparent canvas, live skin change without losing the login code/document,
Signal square controls, Connect → nested Back → Connect → approval → Test →
Disconnect, root Back staying in-app, and no JS page errors. It runs desktop and
Pixel mobile-UA/touch profiles, then captures the standalone dark fallback.

This is **hosted-proxy coverage**, not an authenticated real-shell install or a
physical-device claim. Production promotion/image build remains outside this branch.
