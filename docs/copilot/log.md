# Copilot maintenance log

## 2026-11-19 — live-test

What changed: Added `docs/copilot-live-test.md` documenting read-only checks of the owner instance and an isolated fake-backed API demo. No production code changed. The isolated web UI was not tested because dependencies were unavailable offline and headless Chrome could not capture a page.

Test result: Owner health and public sign-in probes passed (`200`); isolated API health passed (`200`); isolated `python -m clipping demo` completed. Full UI live test is blocked and recorded honestly in the report.

Branch: `osaidalaraj-live-test`

## 2026-11-19 — repeat round 1

What changed: Repeated the owner-instance read-only probes and ran the full web suite plus the backend suite. No application changes were made during the repeat checks. The no-key demo failure was investigated with throwaway data only; the direct subprocess reproduction outside pytest passed, but the pytest case still fails while trying to write its fake transcript file, so it remains an environment-sensitive open item. The backup alert test passed in isolation.

Test result: Owner API health and public sign-in both returned `200`; web Vitest passed 281 tests and TypeScript passed; backend excluding the two repeat failures passed with one expected skip. The full backend run had two failures: `tests/test_14_quality.py::test_DW_6_3_with_fakes_off_and_no_keys_the_demo_still_completes` and `tests/test_16_backup.py::test_failed_backup_raises_alert_and_housekeeping_continues`; the latter passed alone. Temporary debug edits and performance build output were removed.

Branch: `osaidalaraj-live-test`

## 2026-11-19 — web-perf

What changed: Added the report-only web performance review. It records a successful isolated production build, route HTML sizes, largest client chunks, public asset sizes, and layout-shift/bundle recommendations. No application code changed.

Test result: `web/npm run build` passed with `NEXT_DIST_DIR=.next-copilot-perf`; temporary build output was removed afterward. Existing generated `web/tsconfig.json` and `web/next-env.d.ts` changes were restored.

Branch: `osaidalaraj-live-test`

## 2026-11-19 — deps-audit

What changed: Added the report-only dependency audit. No packages were installed or upgraded; the report records the single outdated tool (`pip`), no npm outdated results, and zero cached npm audit vulnerabilities.

Test result: `pip list --outdated` completed; `npm outdated` completed with no results; `npm audit --offline --json` completed with zero advisories.

Branch: `osaidalaraj-live-test`

## 2026-11-19 — docs-sync

What changed: Synced the Arabic usage guide and follow-up plan with the implemented commands and current phase status: corrected the backup path, clarified Tailscale account wording, removed the undocumented Cloudflare workflow, and marked the fake-data pilot as ready while keeping real-service checks open.

Test result: Documentation-only change; no test run required.

Branch: `osaidalaraj-live-test`

## 2026-11-19 — lint-types

What changed: Installed Ruff in the isolated worktree because the validation tool was missing. Extended CI’s correctness rule set from `E9,F63,F7,F82` to `E7,E9,F63,F7,F82,F401`; fixed ambiguous names, a semicolon statement, and unused imports without changing behavior. Broader style rules remain disabled because the repository intentionally enforces correctness-only linting and would create unrelated churn.

Test result: Expanded Ruff check passed. The added backend failure and coverage-helper tests passed: 12 tests.

Branch: `osaidalaraj-live-test`

## 2026-11-19 — coverage

What changed: `pytest-cov` is not installed, so no package was added. Reading the test imports showed small helper modules (`router.py` and `language_check.py`) had comparatively narrow direct coverage; added three behavior-preserving tests for share floors/allocation, transcript-thinness detection, and language mismatch handling.

Test result: `backend/tests/test_21_coverage_helpers.py` passed: 3 tests. Exact numeric coverage was unavailable because pytest rejected the `--cov` options.

Branch: `osaidalaraj-live-test`

## 2026-11-19 — ui-strings

What changed: Added stable English/Arabic i18n keys for the Review sheets and replaced their hardcoded user-facing labels, explanations, and rule-status copy. Kept non-review screens English as specified by decision 30 and preserved logical RTL layout rules. No owner decision is newly required; the remaining English outside navigation/review is an existing documented scope decision.

Test result: `web/npx tsc --noEmit` passed; `web/npx vitest run tests/feed.test.tsx` passed (22 tests); the long transcript test passed alone. The full web suite had one known load-sensitive timeout in `tests/transcript.test.tsx`, while 280 other tests passed.

Branch: `osaidalaraj-live-test`

## 2026-11-19 — failure-tests

What changed: Added `backend/tests/test_20_failure_paths.py` covering invalid/replayed OAuth callbacks, expired and revoked tokens, transient refresh failure, publish HTTP timeout/network failure, practice-campaign refusal, missing owner confirmation, kill switch, and duplicate confirm taps. Production code was unchanged.

Test result: `backend/tests/test_20_failure_paths.py` passed: 9 tests.

Branch: `osaidalaraj-live-test`

## 2026-11-19 — review

What changed: Added `docs/copilot-review.md` for the requested read-only posting/security review. No production code changed and no findings were identified.

Test result: Security review completed; no high, medium, or low vulnerabilities reported.

Branch: `osaidalaraj-live-test`
