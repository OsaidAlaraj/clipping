# Discovery + Design: Phase 3 - Post targets, one-tap confirm and post records

## Files Found
- backend/clipping/publish.py (checklist, approve_publish, mark_posted, cap_warnings, _posting_window), pipeline.py (HANDLERS, process_one, pick_account), core.py (enqueue, kill switch), api.py (publish routes ~line 925), ops.py (housekeeping), connections.py (for_account), adapters/publishing.py (publish_video / finish_publish / check_status, outcome shape).
- migrations 002 (post_gate, clip_gate), 010b (practice_guard on clips/posts/payouts), 015, 016; tests test_4 (gate tests), test_16 (connect helper), test_17 (fake publishing controls).

## Current State
Posting is manual: `approve_publish` (gate 3, one platform = the clip's own account) then `mark_posted`. `posts` has unique(clip_id, account_id), `payouts` unique(clip_id). `post_gate` and `clip_gate` demand a human, so the worker cannot record a post or mark a clip posted. `enqueue` has no delay. No post_targets table.

## Gaps
- A worker-written `posts` row and a worker `clip -> posted` update are refused by the old gates: 017 replaces both functions (same rules) with a path for a published, owner-confirmed target.
- `enqueue` needs `run_after` to stagger (core.py, additive param).
- `checklist()` only knows the clip's own account: gets an optional `platform`.
- A job handler runs in one session, but a send must commit `sending` before the first byte: the stage uses `Database.checkpoint`.
- Nothing recovers a worker that died mid-send: `publish.sweep` (called from housekeeping) turns a stale `sending` into needs_check.
- No periodic status check for `processing`: `post_status` jobs queued by the sweep, one per target per 5 minutes.

## Code Standards
Thin API handlers via `run(actor, fn, ...)`; domain function does the work; AppError for refusals; adapters via registry.call with provider=publishing.provider_name(); additive migration; audit via triggers plus `audit()`; test names are sentences, DW tests named test_DW_3_x.

## Test Infrastructure
pytest, embedded Postgres cloned per test, `produced` fixture (3 packaged clips, accounts own_tt/own_ig/own_yt), `connect()` from test_16 for fake OAuth, `publishing.FAKE_PUBLISHING` modes (refuse, timeout, processing).

## DW Verification

| DW-ID | Done-When Item | Status | Test Cases |
|-------|---------------|--------|------------|
| DW-3.1 | One confirm for three platforms creates three staggered targets inside each window; replay creates none | COVERED | test_DW_3_1_* (stagger, windows, next-day, setting, replay, jobs queued) |
| DW-3.2 | Refusals at the API and in the DB | COVERED | test_DW_3_2_* (practice, worker/system, checklist, kill switch, not allowed, second account) each via function/API and via direct insert |
| DW-3.3 | At most once; mid-send death -> needs_check; kill switch cancels unsent | COVERED | test_DW_3_3_* |
| DW-3.4 | Outcomes update targets; public link writes posts + payout; drafts/private do not | COVERED | test_DW_3_4_* |
| DW-3.5 | mark_posted unchanged shape | COVERED | test_DW_3_5_* plus the existing test_4 tests |

**All items COVERED:** YES

## Design Decisions
- `post_targets` holds every state; `manual` (bool) marks targets the owner posts by hand (account not connected or needs_reconnect): never enqueued, linked by paste.
- Guards live in DB triggers (`post_targets_guard` on insert: human, switch off, practice, owner's active account of the right platform, campaign allows it, checklist complete; `post_targets_state`: legal moves, who may make them; kill switch trigger cancels scheduled targets). Unique partial index (clip, platform) where state not in (cancelled, failed) = one account per platform per clip.
- Replay: idempotency key = hash(clip, platform, request_id); a repeated request_id returns the existing targets before any other check.
- Stagger: each target's earliest = previous + `post_stagger_min` (new operator setting, default 20, bounds 10 to 240), then moved to its own account's window (`next_slot`).
- Worker: stage `publish` marks `sending` and checkpoints before the adapter call; unknown exceptions -> needs_check, AppError -> failed with reason, send-time blockers (brief changed, file changed, practice, account/connection gone, kill switch) -> cancelled. Outcome mapping in `_apply`; adapter answers are validated; a public link writes target + posts + clip + payout in one savepoint (fallback needs_check + paste prompt).
- `post_gate` target path lets the worker record an API publish that already happened, even if the switch went on meanwhile (a fact, not a new post).

## Prerequisites
- [x] Migration 016 applied, vault + fakes in tests
- [x] Phase 2 adapters

## Recommendation
BUILD
