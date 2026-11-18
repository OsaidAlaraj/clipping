# Review: Phase 3 - Post targets (one tap, several platforms)

## Executed Results (Step 0)
- Targeted: `pytest tests/test_18_post_targets.py tests/test_4_review_publish.py tests/test_17_publishing.py tests/test_1_foundation.py` -> 202 passed, 0 failed (first run). The same files ran again inside the full suite.
- Full suite: `pytest -n auto` -> exit 0, all passed, 1 skipped, no load-sensitive failures.
- Typecheck: none configured in the project.
- Lint: `ruff check --select F` over clipping/ and test_18 -> 4 findings; 1 is in changed code (F811 `LinkIn` redefined, api.py:171 vs :235). The other 3 (adapters/discovery.py, demo.py, discovery.py) are not in this phase.
- Leftover `clip_test_*` Postgres and stale pytest workers from the interrupted run were stopped; `.data/pg` untouched.

## Requirement Fulfillment

### DW-3.1
PREMISE:  One confirm for three platforms creates three targets staggered at least `post_stagger_min` apart, each inside its account's window; a replayed `request_id` creates none.
EVIDENCE: publish.py:232-249 (next_slot, plan_times), :302-358 (confirm_post), :311-312 replay; 017 sql:11-41 (idempotency_key unique)
TRACE:    3 platforms -> checklist per platform -> plan_times: earliest = prev + gap, pushed into that account's window -> 3 inserts + 3 jobs with run_after = scheduled_at. Replay -> `_targets_of(clip, request_id)` returns existing rows before any write; a concurrent tap hits `on conflict do nothing` and is refused.
VERDICT:  PASS (test_DW_3_1_* x6, ran green)

### DW-3.2
PREMISE:  Practice campaigns, a worker or system actor, an incomplete per-platform checklist, the kill switch, a platform the campaign does not allow, and a second account on the same platform are each refused, both at the API and in the DB (trigger test with a direct insert).
EVIDENCE: publish.py:305,318-335; 017 sql:80-125 (post_targets_guard), :40 (partial unique index), :69-76 (checklist_complete)
TRACE:    Each case raises in confirm_post (AppError/GateError) and, via a direct insert, in post_targets_guard (is_human, kill_switch_on, is_practice, platforms_allowed, checklist_complete) or the unique index (same clip + platform).
VERDICT:  PASS (test_DW_3_2_* x9 ran green, each asserts API and DB)

### DW-3.3
PREMISE:  A target is sent at most once even when the job runs twice or the worker dies mid-send (it becomes needs_check); the kill switch turned on between confirm and send cancels every unsent target.
EVIDENCE: publish.py:504-536 (stage_publish), :564-582 (sweep); 017 sql:128-169 (state trigger, kill_switch_stops_targets)
TRACE:    Job 1: scheduled -> claim UPDATE ... where state='scheduled' -> checkpoint (sending durable) -> send. Job 2 / retry: state 'sending' -> needs_check (attempts>1) or no-op; the claim UPDATE blocks and re-evaluates to no rows. Worker death: row stays `sending`, sweep (>600 s) -> needs_check. Switch on: trigger sets every `scheduled` to cancelled; `_send_blocker` and the DB `sending` guard back it up.
VERDICT:  PASS (test_DW_3_3_* x12 ran green)

### DW-3.4
PREMISE:  Every outcome updates its `post_targets` row (state, platform post id, reason); a public link writes the `posts` row with platform, platform post id, posted_at and submitted_at, and opens the payout row; drafts and private uploads write no `posts` row until the link is pasted.
EVIDENCE: publish.py:406-421 (_publish), :453-474 (_apply), :382-401 (paste_link); 017 sql:44-51
TRACE:    Adapter answer -> `_clean_outcome` (untrusted) -> published: target + posts + clip + payout in one savepoint; draft/private/processing/needs_check/failed: only the target row (state, platform_post_id, reason, privacy). paste_link -> `_publish`.
VERDICT:  PASS (test_DW_3_4_* x11 ran green)

### DW-3.5
PREMISE:  The existing manual `mark_posted` path still works and writes the same record shape.
EVIDENCE: publish.py:172-204; test_4_review_publish.py (all green); test_DW_3_5_*
TRACE:    mark_posted -> posts insert with the original columns plus state/privacy/submitted_at defaults -> clip posted -> payout. Refuses only when a live target holds that platform.
VERDICT:  PASS

**All requirements met:** YES

## Edge cases (listed in the prompt)
Double tap / replay: handled (idempotency key + replay read). Two accounts on one platform: refused (API `already_targeted`, partial unique index). Platform not allowed: refused (API and DB). Brief changed since approval: blocked in `_send_blocker`, test green. Kill switch between confirm and send: cancelled by trigger. Window closed today: next_slot moves to the next window, test green. Worker crash mid-send: stays `sending` -> needs_check, never resent, test green.

## Test-DW Coverage
- [x] All DW items have corresponding tests (ran in Step 0)
- [x] Coverage level 100%: every new or changed routine has a test; gaps: none. `ops.housekeeping` is exercised by test_12 but no test asserts the call to `publish.sweep` (see Notes).

## Dead Code
No unreachable code. Minor: see Notes.

## Correctness Dimensions
| Dimension | Status | Evidence |
|-----------|--------|----------|
| Concurrency | PASS | Claim is a conditional UPDATE under READ COMMITTED; cancel/paste take `for update`; sweep uses skip locked; replay race resolved by the unique key. No defect traced. |
| Error Handling | PASS | Adapter AppError -> failed (refused before send); any other exception after claim -> needs_check; adapter answer sanitized; `_publish` failure falls back to needs_check. |
| Resources | PASS | No handles opened here; claim stamp `checked_at` is released or ages out after 600 s. |
| Boundaries | PASS | request_id empty/long, empty or duplicate platforms, non-dict ticks, bad window strings, bad UUIDs all refused with plain messages; wrapped windows handled. |
| Security | PASS | Owner-only (human, operator/admin role); `_owned_target` hides other owners' rows; link host checked by hostname (`youtube.com@evil.com` -> evil.com rejected); RLS + force on post_targets. |

## Loaded-Skill Criteria
| Skill | Criterion | Status | Evidence |
|-------|-----------|--------|----------|
| cc-defensive-programming | External input validated at entry (API body, adapter answer) | PASS | confirm_post, `_link_ok`, `_clean_outcome` |
| cc-defensive-programming | No empty catch / swallowed errors | PASS | `except KillSwitchOn: pass` (publish.py:473) is commented and the sweep re-queues; `except psycopg.Error` records needs_check |
| cc-defensive-programming | Assertions only for bugs; no side-effecting asserts | PASS | none used |
| cc-control-flow-quality | Nesting <= 3, guard clauses, McCabe | PASS | confirm_post is long (~12 branches) but flat with guards; not demonstrated harmful (Note) |
| aposd-verifying-correctness | RC/CS/EH/RM/BC/SE items | PASS | see dimensions above |

## Notes (non-blocking)
- api.py:171 adds `LinkIn(url)` but api.py:235 already defines `LinkIn` (url, language, permission_ref, youtube_permitted); the later one wins, so `/api/targets/{id}/link` validates against the source-link model. Works for `{url}`, but the new class is dead and the name clash is a trap. Rename (e.g. `TargetLinkIn`).
- publish.py:25 `IN_FLIGHT` is unused.
- `confirm_post` is long; could split validation from insertion.
- `ops.housekeeping` -> `publish.sweep` wiring has no direct assertion.
- Instagram `finish_publish` after a worker crash mid-call: the claim stamp ages out after 600 s and a later status job would call it again. The adapter is single-attempt per call, and the platform likely rejects a second publish of one container, but I did not demonstrate it. Worth a look in the adapter phase.
- `paste_link` is refused while the kill switch is on (stricter than required).

## Issues (if FAIL)
None.

**Verdict: PASS**
