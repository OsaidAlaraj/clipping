# Discovery + Design: Phase 2P - Pilot slice

## Files Found
- backend/clipping/campaigns.py (intake, confirm_rules, approve gate, register_source), pipeline.py (stage_moments uses the `moments` adapter, `_store_candidates`, `snap`, `sentence_bounds`), publish.py (approve_publish, schedule, mark_posted), ledger.py (record_views, set_payout_status, mark_disputed), adapters/vendors.py (`AzureOpenAILLM.complete_json`, `LLM_SYSTEM`), adapters/fake_llm.py (task dispatch), api.py, review.py (`CLIP_VIEW`), ops.py (`home`), sources.py (upload), web Places.tsx (New screen), Home.tsx, Feed.tsx, Queue.tsx, lib/api.ts.
- docs/code-standards.md read and applied (adapter registry for the LLM, AppError for refusals, tests with fakes, `test_N_<phase>.py`, plain-sentence copy).
- No PRODUCT.md / DESIGN.md (impeccable: scoped work on an existing UI; `globals.css` tokens and the existing classes are the visual authority; the New screen is an Operate surface).

## Current State
- A campaign needs a marketplace row, a campaign version and the owner-gated approval trigger before jobs can exist (`jobs_guard`, `campaign_gate`).
- `stage_moments` asks the active `moments` adapter (the fake, in real mode too, because no Vizard key is set), then snaps and stores. There is no LLM picker.
- Nothing in the code base makes a cover. The LLM adapter takes `task` + `payload`; the system prompt is chosen only by task.
- Candidate `reason` is stored but not shown in the feed. `render_meta` carries the hook.

## Gaps
1. **No cover exists** ("as they exist today" does not hold for DW-2P.3). Decision: the pilot cover is a still frame of the final clip (JPEG, one frame at about 1.2 s) stored as `render_meta.cover_key` and used as the feed poster. Phase 5 replaces it with the real cover. A failed cover never blocks the clip.
2. **`marketplace_id` is NOT NULL**: a `Practice` marketplace row (slug `practice`, no platforms) is added by 010b and hidden from the add-campaign picker (`/api/reference`) and refused by `intake`.
3. **Prompt language**: `LLM_SYSTEM` is per task. `moments` becomes a per-language dict (`en`, `ar`) chosen from `payload.language`; the user message stays JSON data.
4. **Cost**: `propose(...) -> list[Candidate]` cannot return a price, so `propose_with_cost` returns `(candidates, cost)` and `propose` is the thin Produces-contract wrapper.
5. **Phase 2P file scope omits `review.py`, `ops.py`**: both get a minimal edit (the feed needs `is_practice`, `moment_reason`, `cover_url`; Home must not count practice clips as "posts due" and needs the practice flag/language on the card). Reported as a deviation.
6. **`conftest._reset_fakes` is not in scope**: the new fake-LLM control (`FAKE_MOMENTS`) is reset by an autouse fixture in `test_10b_pilot.py`.
7. **The `version` argument of `propose`** is ambiguous in the plan: it is the campaign version row (what `_source_ctx` returns as `version`). The transcript is loaded from the source inside `propose`.
8. Weekly clip cap and portfolio-share rules would throttle a practice campaign and let practice clips eat a real campaign's share: practice is excluded from both.

## Code Standards
Adapter registry for every LLM call; refusals are `AppError` plain sentences; thin API handler; migration additive with RLS covered by the existing `campaigns` policies (no new table); tests are full sentences with an error path; web tests use `mockApi`; tap targets >= 44 px; tokens only; LF endings.

## Test Infrastructure
pytest + embedded Postgres per test, fakes active (`CLIPPING_USE_FAKES=1`), real FFmpeg on a 60 s fixture, `TestClient(api.app)` with `api._owner = owner`. Web: vitest + Testing Library + `mockApi`.

## DW Verification

| DW-ID | Done-When Item | Status | Test Cases |
|-------|---------------|--------|------------|
| DW-2P.1 | A practice campaign is created in one step, runs the whole pipeline, and any attempt to post, schedule or record a payout for it is refused by the API. | COVERED | test_DW_2P_1_practice_campaign_is_created_approved_by_the_owner_in_one_step; ..._api_creates_a_practice_campaign; ..._practice_footage_runs_the_whole_pipeline_to_reviewable_clips_with_cover_and_disclosure; ..._publish_approve_schedule_and_mark_posted_are_refused_for_practice (API); ..._views_and_payout_status_are_refused_for_practice (API); ..._database_trigger_blocks_a_post_row_for_practice; ..._a_worker_cannot_create_practice; web: practice badge, no posting actions |
| DW-2P.2 | With the LLM active, moments come from the transcript (sentence-snapped, inside the length range, with reasons); bad LLM output falls back to the existing fallback with an alert. | COVERED | test_DW_2P_2_moments_come_from_the_transcript_sentence_snapped_inside_the_range_with_reasons; ..._niche_changes_the_length_range; ..._long_transcript_is_chunked_with_overlap_and_pooled; ..._overlapping_duplicates_are_removed; ..._not_json / wrong shape / times outside the video / invented fields -> fallback alert and no crash; ..._arabic_transcript_uses_the_arabic_prompt; ..._transcript_text_never_reaches_the_instructions |
| DW-2P.3 | Three sample videos end to end, real transcription, reviewable clips with captions, disclosure and a cover; report lists timings and cost. | COVERED (real run, not a suite test) | `.code-foundations/build/2026-11-17-pilot-run-report.md`; suite covers the same path with fakes in DW-2P.1 |

**All items COVERED:** YES (3 of 3)

## Design Decisions
- **moments_pilot.py** (deep module, 3 public names): `propose_with_cost(conn, source, version, profile_hint=None) -> (list[Candidate], Decimal)`, `propose(...)` (the pinned contract), `length_range(profile_hint)`. Everything else is private: chunker (cuts at the longest pause near ~10 min, ~20 % overlap), sentence-line renderer, strict JSON parser, fit-to-range using `pipeline.snap` + sentence ends, de-duplication (overlap > 30 % of the shorter), count rule (3 to 8 from the length), Candidate builder.
  - Design-it-twice: (A) LLM returns sentence indexes and the code builds times; (B) LLM returns seconds, the code validates and snaps; (C) a new `moments` provider row behind the existing submit/status/clips protocol. Chosen B: seconds are what the Candidate shape needs, validation catches both hallucinated and out-of-range times, and snapping makes the cut human. C rejected: three-call protocol and a migration row for a function phase 4 replaces; A rejected: Arabic ASR sentence indexes are unstable across chunks.
  - Failure policy: a chunk that fails (adapter error, bad JSON) is skipped; a paused adapter or the kill switch aborts; zero valid moments overall raises `AppError("moments_pilot_failed")`, which `stage_moments` turns into the existing manual-selection alert. Any other exception in the pilot also degrades to the alert (never crashes the job); the kill switch propagates.
  - Prompt-injection stance: system prompt is constant per language; transcript goes only as JSON data in the user message; output is validated (numbers only for times, length-capped plain text for reason and hook, quotes in a hook must exist in the clip text), never executed or used as instructions.
- **Candidate** = exactly `start_s,end_s,variant("tight"),score,rank,reasons,features,source("pilot_llm")` plus an additive `hook` key (the owner asked for a hook line; the stored `candidates.reason` reads `reason (Hook: line)`).
- **Practice campaign**: `campaigns.is_practice`; created by the owner actor only: insert campaign, write version (rules minimal, compliance `pass`), `confirm_rules`, `approve` (real gate trigger runs). Name is prefixed `Practice · ` so every list and alert says it. Refusals: `campaigns.refuse_practice(conn, campaign_id, what)` (code `practice_no_posting`) called from publish.approve_publish/schedule/mark_posted and ledger.record_views/set_payout_status/mark_disputed; DB trigger `app.practice_guard` blocks posts, payouts and clip statuses ready_to_post/queued/posted as defense in depth.
- **Cover**: `pipeline._make_cover`, called in `stage_captions` after the final file exists.
- **Moments dispatch**: practice campaigns use the pilot picker; real campaigns keep the existing `moments` adapter path untouched (phase 4 replaces both).
- **UI**: New gets a third tab "Practice" (name, niche, language, one button, then straight to the upload form with the campaign preselected); Home card, Feed and Queue badge "Practice" and hide pay/posting; Upload shows a clear message above 10 GB and a soft warning above 1.5 GB with the link alternative.

## Prerequisites
- [x] Required files exist
- [x] Dependencies available (Azure and Deepgram keys in .env, GPU Whisper fallback)
- [ ] en_cordkillers_ep377.mp4 still downloading (curl running); wait or skip with a note

## Recommendation
BUILD
