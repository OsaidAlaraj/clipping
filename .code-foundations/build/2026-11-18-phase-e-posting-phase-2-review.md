# Review: Phase 2 - Publish adapters

## Executed Results (Step 0)
- Targeted: `pytest tests/test_17_publishing.py tests/test_16_connections.py tests/test_16b_oauth_provider.py tests/test_1_foundation.py` -> all pass, exit 0.
- Full backend suite `pytest -n auto` -> one failure, `test_11_signals::test_DW_3_3_a_synthetic_long_file...` (projected 223s vs 180s, load-sensitive, the known case). Re-run alone serially -> pass. No other failures.
- Typecheck: mypy not installed (N/A). Lint: no project ruff config; default ruff reports pre-existing style noise only (BLE001 on the new `except Exception`, import ordering). Not blocking.
- Leftover `clip_test_*` Postgres: none; only `.data/pg` is running (untouched).

## Requirement Fulfillment
### DW-2.1
PREMISE: Each fake platform returns the documented states, and the contract shape holds for all three.
EVIDENCE: publishing.py:455-524 (FakePublishing over fixtures), `outcome()` :46; tests test_17:110-147.
TRACE: fake youtube -> init 200 + Location -> PUT 201 -> `private` + shorts URL; instagram -> container, upload, poll FINISHED, media_publish, permalink -> `published` + URL; tiktok -> inbox init, PUT -> `draft`, url None. Every result goes through `outcome()` (the 4 keys). check_status shape tested for all three.
VERDICT: PASS

### DW-2.2
PREMISE: Ambiguous failure after upload start yields needs_check with exactly one call (no retry); pre-upload 4xx yields failed with the reason.
EVIDENCE: `_upload_started` :232 sets `_started`; `_call` :238-252; `_stop` :153; base.py `no_retry` loop (`range(1, 2 if once ...)`) and AdapterUnavailable branch.
TRACE: PUT ReadTimeout -> `_call` except -> `_stop` -> `_started` true -> needs_check; request log is exactly [POST, PUT]. 4xx on init -> `_stop` with `_why` message -> failed, one call, no quota row. Unexpected exception inside publish_video after start -> `_unsure`; registry makes one attempt, no fallback (test_DW_2_2_registry_never_retries).
VERDICT: PASS

### DW-2.3
PREMISE: YouTube private while unverified + paid flag; TikTok inbox endpoint; Instagram public with URL, paid-partnership only when config supports it.
EVIDENCE: publishing.py:272-280 (forced private, paidProductPlacementDetails), :396 (`/inbox/video/init/`), :324-328 (is_paid_partnership gated on config), tests test_17:260-343.
TRACE: unverified_project true + opts public -> body privacyStatus "private"; paid true -> flag in body and `part`. TikTok init path asserted. Instagram config false -> field absent, note appended; config true -> `is_paid_partnership=true`.
VERDICT: PASS

### DW-2.4
PREMISE: Quota exhaustion and malformed platform response are refused safely, with an alert.
EVIDENCE: `_quota` :220-230 (before token/any call; alert `publish_quota`), `_bad` :165 (alert `publish_bad_response`), `_json`/`_id` validators, host checks :284, :403.
TRACE: 7th YouTube upload (9600+1600>10000) -> failed, zero platform calls, one alert. HTML/non-dict/non-id-shaped bodies -> `_bad` -> alert + needs_check (started) or failed (not started).
VERDICT: PASS

**All requirements met:** YES

## Edge cases (listed)
Untrusted response shape (validated; PASS), timeout mid-upload (PASS), pre-upload 4xx (PASS), quota refused before calling with alert (PASS), Instagram processing polled with limit `poll_attempts` then `processing`, finish_publish publishes once (PASS), caption over limit refused before any call (PASS). All handled and tested.

## Phase constraints
Registry-only calls (tests go through `registry.call`); token only via `vault.access_token` (publishing.py:147), host-allowlisted, absent from results/audit/alerts/quota rows (test_no_token_appears...); no auto-retry after ambiguity (`no_retry`); migration additive, `publish_quota_log` has RLS enabled + forced with owner policies; adapter config contains no secrets (tested). Contract keys/states match.

## Test-DW Coverage
- [x] All DW items have tests that ran and passed.
- [x] 100% level: every new/changed routine has a direct or exercised unit test (publishing.py routines incl. `_clean`, `_why`, `_blocks`, `provider_name`, `reset_fake`, fake `_serve_*`; base `Adapter.alert`, `no_retry`, Registry bind/once path). Gaps: none found (no coverage tool installed; checked by reading tests).

## Dead Code
None found.

## Correctness Dimensions
| Dimension | Status | Evidence |
|---|---|---|
| Concurrency | PASS | Adapter instance is created per call (`Registry.get`), so `_started/_post_id/_notes` are not shared; quota check uses `pg_advisory_xact_lock` so concurrent publishes serialise. |
| Error Handling | PASS | Every external call funnels through `_call`; ambiguous vs safe split verified by trace and tests. |
| Resources | PASS | httpx client in `with`; file handles in `with`/generator. |
| Boundaries | PASS | empty file, caption at limit (2200 ok, 2201 refused), zero chunk, bad ids tested. |
| Security | PASS | Response ids regex-checked, upload URLs host-checked before token use, other-platform connection refused, RLS on quota table, reasons cleaned/redacted. |

## Loaded-Skill Criteria
| Skill | Criterion | Status | Evidence |
|---|---|---|---|
| cc-defensive-programming | External input validated at entry (platform responses, options, caption, file) | PASS | `_options`, `_caption`, `_file`, `_json`, `_id`, `_post_id_ok` |
| cc-defensive-programming | No empty catch / silent swallow | PASS | each `except` returns needs_check/raises; `_ig_permalink` returns None deliberately (post already live) |
| cc-defensive-programming | Assertions not used for runtime errors; no side-effecting asserts | PASS | none present |
| aposd-designing-deep-modules | Interface depth / information hiding | PASS | two public methods plus check_status hide three protocols; `no_retry` is a small base hook |
| aposd-designing-deep-modules | Silent failure | PASS | failures surface as state+reason and alerts |
| aposd-verifying-correctness | RC/CS/EH/RM/BC/SE dimensions | PASS | see table above |

## Notes (non-blocking)
- `_why` passes platform error text through `config.redact`, which only masks env-configured secrets, not the vault token; a platform echoing a bearer token would not be masked (undemonstrated, unlikely).
- Instagram `PUBLISHED` status in `_ig_finish`, and `_ig_status` PUBLISHED/FINISHED branches, have no dedicated test (routine-level coverage exists).
- Instagram polling sleeps up to 9 x 30s inside the registry call on the real provider; acceptable but worth knowing for worker timeouts.
- `publish_video` re-raises non-AppError exceptions before upload start; the registry turns them into AdapterUnavailable (safe).
- Not run against live services (stated by the author).

## Issues (if FAIL)
None.

**Verdict: PASS**
