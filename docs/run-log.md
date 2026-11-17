# Run log

Date: 2026-11-16. Machine: Windows 11, Python 3.14.6, Node 26.5.1, embedded Postgres 16.15, FFmpeg 9.0.2.
Every vendor is a fake in these runs. FFmpeg and Postgres are real. Nothing was posted anywhere.

## Commands

```bash
python run.py test
```

```bash
python run.py demo
```

## Test results

| Suite | Result |
|---|---|
| Backend (pytest) | 140 passed, 0 failed, 0 skipped |
| Web (vitest) | 38 passed, 0 failed, 0 skipped |
| TypeScript check | pass |
| Production build of the web app (`next build`) | pass, 11 routes |

By build step:

| File | Tests | Covers |
|---|---|---|
| `backend/tests/test_1_foundation.py` | 35 | Schema, row-level security, audit log, settings, kill switch, adapter interfaces and fakes |
| `backend/tests/test_2_intake.py` | 21 | Six sample briefs, Agent A, hard blocks, approval gate, rule-change detection |
| `backend/tests/test_3_pipeline.py` | 31 | Deterministic checks, Agents B and C, boundary snapping, the full pipeline on fixture footage in English and Arabic |
| `backend/tests/test_4_review_publish.py` | 26 | Feed priority, accept, reject, Undo, Review Later, edits, sessions, checklist, gates |
| `backend/tests/test_5_tracking.py` | 27 | Amounts, ledger order, net pay per hour, CSV, alerts, keep-alive, MVP acceptance, API flow |
| `web/tests/gestures.test.tsx` | 3 | Swipe directions, tap, the 20 px left-edge rule |
| `web/tests/feed.test.tsx` | 22 | Feed card, accept and reject gestures, reason required, Undo, session end and summary, keyboard, offline, right-to-left |
| `web/tests/screens.test.tsx` | 13 | Home, campaign swipes, Queue and publish checklist, Review Later, Settings |

## Spec section 15.5, item by item

| Spec 15.5 test | Result | Where |
|---|---|---|
| Transcription bake-off per dialect | Not run: blocked by open item O2 (needs the owner's real material and vendor keys). Modern Standard Arabic is the default until then. | `docs/open-items.md` |
| Golden sets: 10 briefs (Agent A), 20 episodes (Agent B), 50 clips (Agent C) | Not run at that size: they need real briefs, episodes and labelled clips, and a real LLM. The agents' validation is tested on 6 sample briefs and synthetic clips with the fake LLM. | `test_2_intake.py`, `test_3_pipeline.py` |
| Deterministic: duration, aspect, caption starts with disclosure, overlay present, banned words | Pass | `test_3_pipeline.py` |
| Rules engine: blocked categories fail; `unclear` never passes | Pass | `test_2_intake.py` |
| Ledger: state changes only in allowed order | Pass | `test_5_tracking.py` |
| Settings: values stay within bounds; each change writes an audit row | Pass | `test_1_foundation.py` |
| Kill switch: no new jobs and no ready packages while active | Pass | `test_1_foundation.py`, `test_4_review_publish.py` |
| UI: accept and reject gestures; reason required on reject; Undo restores the clip within 10 seconds; session ends at its length and shows the summary | Pass | `web/tests/feed.test.tsx`, `test_4_review_publish.py` |

## Spec section 15.6, MVP acceptance

Shown by `test_mvp_acceptance_on_the_seeded_sample_campaign` and by the demo run below.

| Acceptance point | Result |
|---|---|
| A campaign goes from intake to approval, produces a packaged clip, is accepted in the feed, is posted through the checklist, and has views and payout logged | Pass |
| The audit log records each step | Pass (steps checked in order; each gate row carries the owner as actor) |
| The kill switch stops all new jobs within one minute | Pass (immediate: the database refuses new jobs) |
| Median net pay per hour is calculated from the ledger | Pass |

The money figures in the demo come from made-up view counts and minutes. They show the arithmetic, not an earnings estimate.

## Demo run output

```
Applied migrations: 001_schema.sql, 002_rules.sql, 003_rls.sql, 004_seed.sql
Sample-campaign demo
1. Intake and approval
  - Brief stored as version 1 (snapshot 1223aeba9165), status pending_approval, rules status pass
  - A gambling brief was hard-blocked on intake: status rejected
  - Owner confirmed the extracted rules and approved the campaign (gate 1)
2. Production
  - Fixture footage registered (60 s, hash f0018414d426)
  - 17 pipeline jobs ran; 3 packaged clips are in the feed
  - Clip 14.4 s, tiktok, compliance pass: Top-paying campaign
  - Clip 14.36 s, instagram_reels, compliance pass: Top-paying campaign
  - Clip 14.4 s, youtube_shorts, compliance pass: Top-paying campaign
3. Review
  - Accepted the top clip (gate 2); hook recorded: “Ignore this and it costs you: that is completely wrong”
  - Rejected the second clip with reason “weak hook”, then used Undo: it is back in the feed
  - Moved the third clip to Review Later
  - Session summary: 2 reviewed, 1 accepted, 0 rejected, 1 posts due
4. Posting
  - Brief re-checked before the batch: unchanged
  - Publish checklist complete and approved (gate 3)
  - Owner recorded the post link (placeholder link; nothing was posted)
5. Tracking and money
  - Views logged at 48 h (21,000) and 7 d (64,000). Payout: gross $96.00, fee $9.60, net $86.40, status paid
  - Net pay per hour: $144.0 over 0.6 h (confidence low); weekly median $144.0
6. Kill switch and audit log
  - Kill switch on in 0.00 s: 0 jobs ran, ready-to-post packages shown: 0
  - Audit log holds 279 rows. Gate and state-change rows:
  -     campaigns.status:reading>pending_approval × 1
  -     campaigns.status:reading>rejected × 1
  -     rules.confirmed × 1
  -     campaigns.status:pending_approval>approved × 1
  -     gate.campaign_approval × 1
  -     jobs.status:queued>running × 17
  -     jobs.status:running>done × 17
  -     candidates.status:new>hooked × 3
  -     candidates.status:hooked>rendered × 3
  -     clips.status:rendering>ready_for_review × 3
  -     clips.status:ready_for_review>accepted × 1
  -     gate.clip_acceptance × 1
  -     clips.status:ready_for_review>rejected × 1
  -     clips.status:rejected>ready_for_review × 1
  -     clips.status:ready_for_review>review_later × 1
  -     brief.recheck × 1
  -     clips.status:accepted>ready_to_post × 1
  -     gate.publish_approval × 1
  -     clips.status:ready_to_post>posted × 1
  -     post.recorded × 1
  -     payouts.status:submitted>tracking × 1
  -     payouts.status:tracking>approved × 1
  -     payouts.status:approved>held × 1
  -     payouts.status:held>paid × 1
```
