# Device-code sign-in UI evidence

All codes and connection states shown here are fixtures. No owner account,
credential, real OAuth approval, live box or core Hermes was used or changed.

- `mobile-before-after.png`: Samsung mobile-UA/touch Chromium, 412×915 browser
  viewport, original UI at `10887eb` on the left and the numbered-step UI on the
  right. Both use the same full-width proxy frame and Nebula tokens. The original
  app's production assets are unchanged in the baseline; only the QA frame was
  made full width for an equal-size comparison.
- `mobile-light-sepia.png`: same candidate and viewport, Sepia light token transport.
- `desktop-dark-nebula.png`: 1280×915 browser viewport, centered single-column UI.

Executed on the configured macOS browser worker using system Chrome and
Playwright 1.64.1, with private external-volume scratch. The hosted proxy reserves
40px for its label, so the app frame is 412×875 (mobile) or 1280×875 (desktop).

`node qa/signin.cjs` passed:

- Exact numbered copy → link → paste/return order; large unclipped code.
- Real Clipboard API copy/readback; real legacy copy/readback when the Clipboard
  API is rejected or missing; full selection and honest feedback if both fail.
- The provider link opens a separate tab with no opener (provider page mocked).
- Waiting persists on pending polls, then the existing poll renders Connected.
- Cancel and nested/root Back retain the app; fresh attempts clear copy feedback.
- Nebula, Sepia and Signal transport changes keep the same document and code;
  transparent hosted canvas, matching colour scheme and Signal square controls.
- 320px standalone dark fallback has no horizontal overflow; no page JS errors.

`node qa/check.cjs` passed its existing fake OAuth/Codex hosted desktop/mobile
Connect → Back → Connect → approval → Test → Disconnect path and root Back.

Also passed: `python3 test-package.py`, `python3 test-chatgpt.py`,
`python3 test-inference.py -v`, `python3 mcp/inline.py --check`, JS syntax checks,
and `git diff --check`.

These are isolated browser/proxy results, not physical Samsung/Brave, full skin
package, authenticated Wizard shell, deployment or real OAuth acceptance claims.
