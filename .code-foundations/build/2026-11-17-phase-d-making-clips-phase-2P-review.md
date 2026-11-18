# Review: Phase 2P - Practice campaigns and pilot run

## Executed Results (Step 0)
- Backend: `pytest -p no:cacheprovider -q` (full suite) -> exit 0, all passed, 1 skipped. `tests/test_10b_pilot.py` alone: 60 passed.
- Web: `npx vitest run` -> 7 files, 106 passed. `npx tsc --noEmit` -> clean.
- Lint (CI set): `ruff check --select E9,F63,F7,F82 backend` -> All checks passed.

## Requirement Fulfillment

### DW-2P.1
PREMISE:  A practice campaign is created in one step, runs the whole pipeline, and any attempt to post, schedule or record a payout for it is refused by the API.
EVIDENCE: campaigns.py create_practice / refuse_practice; api.py POST /api/campaigns/practice; publish.py approve_publish, schedule, mark_posted; ledger.py record_views, set_payout_status, mark_disputed; migrations/010b_pilot.sql practice_guard.
TRACE:    POST /api/campaigns/practice -> create_practice (owner-only, validates name/niche/language) -> campaign + version -> confirm_rules -> approve -> approved campaign. Footage then runs through stage_moments (practice branch) to the review feed. Publish, schedule, mark-posted and ledger entry points call refuse_practice first -> AppError practice_no_posting. Direct SQL (update clips to ready_to_post, insert payouts) raises "practice campaigns cannot be posted" (tests).
          Tests: test_DW_2P_1_* (a, API, whole pipeline, refusals, ledger refusals, database trigger).
VERDICT:  PASS

### DW-2P.2
PREMISE:  With the LLM active, moments come from the transcript (sentence-snapped, inside the length range, with reasons); bad LLM output falls back to the existing fallback with an alert.
EVIDENCE: moments_pilot.py propose_with_cost / _parse / _fit; pipeline.py _pilot_moments, _manual_selection_alert.
TRACE:    transcript words -> sentence lines -> chunks -> LLM call (data-only JSON user message) -> _parse (strict types, range, window) -> _fit (snap to sentence ends, niche length range) -> candidates with reasons. Non-JSON, wrong shape, out-of-video times, NaN/inf, bool, bad score -> AppError moments_pilot_failed -> _pilot_moments raises the manual_selection alert, job done, manual pick still works (17 parametrised bad-output cases plus fallback tests).
VERDICT:  PASS

### DW-2P.3
PREMISE:  Sample videos go through end to end on this laptop with real transcription, each yields reviewable clips with captions, disclosure and a cover; the report lists timings and cost (scope: 8-min Arabic interview and 10-min English cut).
EVIDENCE: .code-foundations/build/2026-11-17-pilot-run-report.md; .data/pilot-run/storage/clips/*/ (6 clip folders), results-*.json.
TRACE:    Report lists per-stage timings (209 s and 166 s), Deepgram minutes and cost ($0.094) and LLM cost estimate, providers (deepgram nova-3, azure_openai). All 6 listed clip folders contain final.mp4, cover.jpg, final.ass. ffmpeg stream report: 1080x1920 h264 + aac, 36.5 s (Arabic clip) and 34.5 s (English clip); sizes 7 to 11.7 MB. Frames extracted into .data/tmp: Arabic clip shows burned Arabic caption and the Arabic disclosure label; English clip at 12.5 s shows burned caption "There's an obscure area." and "#ad". final.ass has 26 to 60 Dialogue lines per clip. I did not re-run the paid run.
VERDICT:  PASS

**All requirements met:** YES

## Test-DW Coverage
- [x] All DW items have corresponding tests (DW_2P_1_*, DW_2P_2_* in test_10b_pilot.py; DW-2P.3 by observed artifacts as it needs real providers)
- [x] Coverage at 100% level: every new or changed routine has a test (create_practice, refuse_practice, register_source default, intake refusal, _pilot_moments, _manual_selection_alert, _make_cover, _drop_fake_transcript, ledger refusals, publish refusals, moments_pilot helpers, fake_llm moments, price estimate, api endpoint/reference, ops home). Web: uploadProblem, PracticeForm, UploadVideo, Home card, Feed, Queue/Checklist covered in pilot.test.tsx. Gaps: none found.

## Dead Code
None found.

## Correctness Dimensions
| Dimension | Status | Evidence |
|-----------|--------|----------|
| Concurrency | N/A | No new shared state; worker loop unchanged |
| Error Handling | PASS | Bad model output never crashes; KillSwitchOn/AdapterPaused handled; cover failure leaves clip without cover |
| Resources | PASS | No new handles beyond ffmpeg call in _make_cover |
| Boundaries | PASS | Times validated against duration and sent window; chunker loop guarded (test: never loops) |
| Security | PASS | Transcript travels only as JSON user message, system prompt fixed per language (test checks transcript never touches instructions); hook quotes validated; DB trigger blocks posts/payouts/ready_to_post updates; practice goes through confirm_rules and approve (owner gate not bypassed); fake transcript discarded when a real provider is active (test) |

## Loaded-Skill Criteria
| Skill | Criterion | Status | Evidence |
|-------|-----------|--------|----------|
| cc-defensive-programming | External input validated at entry (LLM output, API body, upload size) | PASS | _parse strict validation; create_practice validates; client-side 10 GB block matches server MAX_SOURCE_BYTES |
| cc-defensive-programming | No empty catch / silent failure | PASS | _pilot_moments broad except raises alert; _make_cover swallow returns None by design, documented |
| cc-defensive-programming | Security-critical path re-validated (defense in depth) | PASS | App-level refusals plus DB trigger |
| aposd-designing-deep-modules | Narrow interface, hidden detail | PASS | propose(...) one call hides chunking, snapping, validation; propose_with_cost is a small extension |
| impeccable | Operate-mode UI: clear messages, no regressions | PASS | Practice labelled consistently; size messages plain; tests pass. Not visually inspected in browser |

## Notes (non-blocking)
- practice_guard has no INSERT trigger on clips, so a direct insert of a clip row already in status ready_to_post/posted would not be blocked; posts and payouts inserts are blocked, so nothing can be posted or paid. Optional hardening.
- Chunked (multi-call) LLM path was only unit-tested with synthetic transcripts, as the report states (scope agreed).
- English clip frame at 8 s had no caption simply because no word was spoken then.
- moments_pilot imports pipeline while pipeline lazily imports it: a circular dependency handled by lazy import; acceptable.

## Issues (if FAIL)
None.

VERDICT: PASS
