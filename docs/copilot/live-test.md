# Live test — 2026-11-19

## Owner instance (read-only)

- `GET http://127.0.0.1:8787/api/health` returned `200` with `{"ok":true}`.
- `GET http://localhost:3000/signin` returned `200`; the response rendered an English, left-to-right sign-in document.
- No authenticated request, mutation, upload, sign-in, or browser interaction was made against the owner instance.
- A headless Chrome screenshot was attempted, but the installed Chrome reported that multiple targets are not supported in headless mode. No screenshot was saved.

## Throwaway instance

- Started an isolated API with `AUTH_MODE=local`, `CLIPPING_USE_FAKES=1`, `CLIPPING_API_PORT=8788`, and a worktree-local data directory.
- Seeded the sample campaign with `python -m clipping demo`; the demo completed and exercised intake, production, review, posting gates, tracking, and kill-switch paths.
- `GET http://127.0.0.1:8788/api/health` returned `200`.
- The Next.js UI could not be started because this checkout had no cached `node_modules` and `npm install --offline` failed with an uncached registry response. A normal install was attempted after that dependency failure but did not complete before the live-test window; no UI claims are made.

## Findings

| Severity | Finding | Reproduction |
|---|---|---|
| Medium | Full phone/desktop, English/Arabic UI verification is blocked by unavailable web dependencies and the installed Chrome headless limitation. | From `web/`, `npm install --offline` fails because `@simplewebauthn/browser` is not cached; Chrome headless reports “Multiple targets are not supported in headless mode.” |

No UI fix was made because the UI could not be run safely and there is no verified visual defect to change.
