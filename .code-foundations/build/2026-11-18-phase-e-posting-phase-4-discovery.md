# Discovery + Design: Phase 4 - Posting screen and manual path

## Files Found
- Web: `web/components/Queue.tsx` (Checklist sheet, English-only, legacy per-clip approve + Mark posted), `web/lib/api.ts` (api, outbox), `web/lib/data.ts` (SWR cache, mutate), `web/lib/toast.ts`, `web/lib/i18n.tsx`, `web/app/globals.css`, tests `pilot/screens/quality/connections.test.tsx`, helper `web/tests/helpers.tsx`.
- Backend: `publish.py` (post_options, confirm_post, post_status, cancel_target, paste_link, stage_post_status, sweep), `alerts.py` (raise_alert, evaluate), `ops.py` (housekeeping), `api.py` (two `LinkIn`), `adapters/publishing.py`.

## Current State
Phase 3 routes exist and are tested. No morning summary exists anywhere. Existing tests pin the legacy sheet ("Approve for publishing", "Mark posted") and must keep passing.

## Gaps
- Plan says "a line in the morning summary" but there is no summary: build `alerts.morning_summary` + a once-a-day send from housekeeping.
- Phase 3 `post_status` has no `submit_by`; `posts.posted_at` is the moment the link was recorded, used as the start of the 30-minute rule.
- Drafts/private raise no reminder today.

## Code Standards
Thin handlers, AppError plain sentences, full-sentence test names, `DW_4_x` names, web via `mockApi`, light tokens, logical CSS properties, tap targets >= 44 px.

## Test Infrastructure
pytest with fakes (`Posting` fixture in test_18), vitest + Testing Library + `mockApi`, CSS audit via regex on globals.css (quality.test.tsx).

## DW Verification
| DW-ID | Item | Status | Test Cases |
|---|---|---|---|
| DW-4.1 | pick -> checklist -> one tap -> per-platform states; Cancel | COVERED | web `DW_4_1 ...` (flow, optimistic, live poll, cancel, offline, refusal) |
| DW-4.2 | manual share/download, copy caption + hashtags, paste link, invalid refused | COVERED | web `DW_4_2 ...` (share, download fallback, copy, blocked clipboard, link ok / bad) |
| DW-4.3 | draft/private reminder; 30-minute countdown | COVERED | backend `test_19_*` DW_4_3 (alert, morning line, submit_by) + web countdown/reminder |
| DW-4.4 | Arabic + RTL; stylesheet audit | COVERED | web `DW_4_4 ...` (Arabic render, dir, CSS audit) |
All items COVERED: YES

## Design Decisions
- New screen = `PostPanel` (picker, one combined checklist, one confirm), `PostStatus` (live target cards, Cancel, countdown), `PostManual` (share/download, copy, paste link), `PostText` (en/ar strings). Opens inside the existing Queue sheet.
- Legacy approve/mark-posted block stays as the fallback shown only when post options cannot load (keeps pinned tests; production always loads options).
- Optimistic confirm: pending cards at once, replaced by server targets; refusal restores the picker with ticks kept. Offline confirm goes to the outbox with `request_id` as its id (decision 29).
- Status polls only while a target is scheduled/sending/processing.
- Backend: `submit_by = posts.posted_at + 30 min` on published targets; `finish_in_app` alert raised when a target becomes draft/private (and by evaluate for older ones, once); `morning_summary` line once per local day.
- Cleanup 4: claim the Instagram finish by bumping `attempts`; a second claim after a crash goes to needs_check instead of calling finish_publish again.

## Prerequisites
- [x] Phase 3 routes present; no migration needed.

## Recommendation
BUILD
