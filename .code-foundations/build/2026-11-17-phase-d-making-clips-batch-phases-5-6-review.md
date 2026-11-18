# Review: Batch, Phase 5 (d8c3be2) and Phase 6 (e994700)

## Executed Results (Step 0)
- Backend: `pytest -p no:cacheprovider -q -n auto` -> exit 0, every dot green, 1 skipped (the live-NVENC encode, conditional on a free GPU).
- Lint as CI: `ruff check --select E9,F63,F7,F82 backend` -> All checks passed (exit 0).
- Web: `vitest run` -> 11 files, 183 tests passed (an earlier run, concurrent with the backend suite, timed out the 3000-word transcript test at 5 s; it passes alone). `tsc --noEmit` -> exit 0.
- `python run.py demo` -> exit 0, all six sections, "Render: 3 clip(s), one encode each: yes".
- `python run.py demo --no-keys` -> exit 0 (fakes off, keys blank), "Render: 2 clip(s), one encode each: yes".
- Real-check frames in `.data/tmp/p5/` inspected: Arabic label top right, English top left, hook on a colour band, wrapped Arabic hook, cover PNG with a shortened hook.

## Phase 5: One encode, originality layer, covers

DW-5.1 A clip is produced with exactly one encode, captions and disclosure burned in.
- EVIDENCE: adapters/media.py:377-414 (`render_clip`, one `ass=` graph, one `_encode`); pipeline.py `stage_render`; test_13 `test_DW_5_1_...` (Spy counts `-c:v` calls from `ffmpeg.run` == 1) and `test_every_clip_of_a_full_pipeline_run_is_exactly_one_encode` (3 clips, 3 encodes, no `captions` job).
- TRACE: clip + words + label -> ASS file (captions, label, title, bar) -> one filter_complex -> one x264/NVENC encode -> `encodes: 1`. Cover is a one-frame PNG, not a video encode.
- VERDICT: PASS

DW-5.2 Arabic disclosure top right; English where it was.
- EVIDENCE: media.py:253-254 (Label style alignment 9 for rtl, 7 otherwise); test `test_DW_5_2_...` measures pixel deviation in the left and right halves of the top strip; frames ar_A_0.6.png and en_A_0.6.png show it.
- VERDICT: PASS

DW-5.3 Two accounts with different templates give measurably different frames.
- EVIDENCE: `test_DW_5_3_...` (same template ~0 difference, different template > 3 and > 10x), and `test_DW_5_5_two_accounts_..._render_differently` through the pipeline.
- VERDICT: PASS

DW-5.4 Each clip has a cover PNG with the hook text.
- EVIDENCE: media.py:416-428 `_cover`, pipeline writes `cover_key` and `clips.cover_path`; `test_DW_5_4_...` checks PNG signature, size, hook in cover ASS and pixels changed only in the lower band; cover frames viewed.
- VERDICT: PASS (the cover carries the hook cut at 60 characters with an ellipsis, by design R7)

DW-5.5 Owner edits an account's template on Profile and the next clip uses it.
- EVIDENCE: Look.tsx (PUT `/api/accounts/{id}/template`), api.py:621-627, templates.edit_primary marks the edited template `fresh`; web `look.test.tsx` "applies an edit on screen at once and saves only what changed"; backend `test_DW_5_5_the_owner_edits_the_account_template_and_the_next_clip_uses_it`.
- VERDICT: PASS

DW-5.6 Versioned templates; a new template via the API is used next with no code change; rotation; revert restores.
- EVIDENCE: templates.py `add/update/revert/pick` (fresh first, then least recently used), 013 migration, immutable-version trigger; tests `test_DW_5_6_a_new_template_added_through_the_api_is_next_in_the_rotation`, `..._rotates_them_across_consecutive_clips`, `..._reverting_a_version_restores_the_previous_look_as_a_new_version`, `test_DW_5_6_the_next_render_uses_a_template_added_through_the_api_and_a_revert_restores_the_look`, `test_template_versions_are_immutable_in_the_database`.
- VERDICT: PASS

Edge cases (all handled, each with a test): RTL Arabic with English words (`test_captions_keep_arabic_with_english_words_...`); very long Arabic hook wrapped and shrunk (`fit_size`, `..._very_long_arabic_hook_...`; I also ran `fit_size` on 300 Arabic characters: 37 px from 69); no face -> centre crop (`..._crop_follows_the_person_and_falls_back_to_the_centre...`); NVENC busy or missing -> x264 in the same render, x264 failure not retried; template missing fields -> defaults (verified: `{"font_size":"9","accent":"zz"}` normalises to defaults); ASS and filter control characters (verified: `{\an1}`, `\N`, U+2028 and a font of `Arial\n[Events]` are neutralised; user text never enters the filter graph, only `final.ass`); PUT with bad caption_position or font_size 99 -> 400 (`AppError.status` 400; verified refusals for position, size, bool size, font with `;`, colour).

**All Phase 5 requirements met: YES**

Test-DW coverage: every DW has a named test that ran. 100% level: new or changed routines in templates.py, media.py (crop, zoom, ASS, cover, encode), ffmpeg.py (`frame_raw`, `skin_mask`, `nvenc_ready`, `video_encoder`, `encoder_args`), pipeline (`_assign_account`, `_render_request`, `stage_render`, `stage_captions`), api template routes and Look.tsx all have direct tests in test_13_render.py, test_3_pipeline.py and look.test.tsx. Gaps: none found.

## Phase 6: Quality gate, brand checks, end-to-end

DW-6.1 Clips under the threshold sort below the others and carry a label saying why.
- EVIDENCE: review.py `chance_to_clear`, `_annotate_quality`, `rank_feed` sort key `(weak, -score, created_at)`, `quality_label` ("Weak: ... Shown last."); Feed.tsx renders `.quality.weak`; tests `test_DW_6_1_a_weak_clip_sorts_below_every_other_clip_whatever_it_pays`, `..._the_weakest_clip_goes_last_even_when_its_hook_would_put_it_first`, `..._the_threshold_is_a_setting`; demo output shows the third clip "Weak: about 16% ... Shown last." in last place.
- VERDICT: PASS

DW-6.2 A rejection for brand X adds a check that flags the same issue on X's next clip.
- EVIDENCE: brand_checks.py `interpret/record_rejection/evaluate/for_campaign`; compliance.run appends `brand_checks.evaluate`; pipeline.run_compliance passes `for_campaign`; tests `test_DW_6_2_a_rejection_recorded_for_a_brand_flags_the_same_issue_on_the_next_clip`, `..._a_banned_phrase_blocks_the_clip...`, `..._a_vague_reason_is_a_reminder_that_never_blocks`, brand isolation and owner isolation tests. Demo: "Must contain #sampleadtag" check recorded.
- VERDICT: PASS

DW-6.3 `python run.py demo` runs transcription -> signals -> moments -> one-encode render -> review with fakes; with fakes off and no keys the same flow completes on the declared fallbacks.
- EVIDENCE: demo.py `stage_lines`, `signals_only_check`; run.py `demo_env`, `--no-keys`; tests `test_DW_6_3_the_demo_runs_...` (stage order asserted), `test_DW_6_3_with_fakes_off_and_no_keys_the_demo_still_completes` (a real subprocess); my runs of both commands exit 0.
- FINDING (deviation, reported plainly): in `demo --no-keys` the stage line reads "Transcription: fake fake-1, version 1". Local Whisper does NOT run; the keyless adapters stay on the seeded stand-ins. Signals-only ranking is only exercised by the rolled-back check inside the demo ("AI state unavailable", 2 to 3 moments kept), and the keyless main flow itself ranks on whatever the stand-in gives. The deviation is documented honestly in docs/open-items.md ("The no-keys demo runs on the seeded stand-ins ...") with the reason (1.6 GB model download per throwaway directory), and the demo's own output states which provider ran, so it is not hidden. The flow completes. Verdict: the clause "local Whisper" is not met literally; I judge the flow completing plus the documented deviation as PASS-with-deviation, as instructed, and flag it for the owner.
- VERDICT: PASS (with the deviation above)

DW-6.4 Review and correction screens work at 375 px and desktop (component tests and stylesheet audit).
- EVIDENCE: quality.test.tsx "stylesheet audit" (logical properties, AA contrast, no fixed width above 375 px, no sideways page scroll), the card and rejection form tests, Arabic/RTL tests; all 183 web tests green. Live check is out of scope per the brief.
- VERDICT: PASS

DW-6.5 Correction queue puts words inside likely clip moments first.
- EVIDENCE: transcripts.py `rank_words` (sort key `-weight` first), `source_moments`, `queue_for`, `review_queue` dual shape; tests `test_DW_6_5_words_inside_likely_clip_moments_come_first`, `..._a_strong_moment_beats_a_weak_one...`, `..._the_review_queue_endpoint_uses_the_moments`, filtered/discarded moments do not weight.
- VERDICT: PASS

Edge cases: no view history -> assumed stats and label "assumed: no view history yet" (demo output and `test_no_view_history_uses_assumed_stats_and_the_label_says_so`); vague rejection -> reminder, verdict unchanged (`overall` ignores "note"); kill switch before and during the demo stops cleanly (`test_DW_6_3_the_demo_does_not_start...`, `..._stops_cleanly_when_the_kill_switch_goes_on_during_production`).

Two fixes:
- Signals text series and topics refresh when the transcript version changes, without FFmpeg: signals.load -> `compute` only when `transcript_version` differs and `file_hash` is the same; `test_N2_load_rebuilds_...` spies `probe/audio_pass/video_thumbs` and asserts `calls == []`. PASS.
- `reaction` zeroed in silent seconds: signals.py `text_signals`; `test_N3_...` PASS.

**All Phase 6 requirements met: YES** (DW-6.3 with the documented Whisper deviation)

Test-DW coverage: every DW has a test that ran. 100% level: new routines in brand_checks.py, review.py (`moment_percentile`, `chance_to_clear`, `quality_label`, `_annotate_quality`), transcripts.py (`rank_words`, `moment_ranges`, `source_moments`, `current_version`, `_queue_for_transcript`), demo.py (`stage_lines`, `signals_only_check`, `_failed_jobs`, kill-switch paths), run.py `demo_env`/`KEY_NAMES`, api routes and Feed/Queue components are each covered in test_14_quality.py and quality.test.tsx. Gaps: none found.

## Cross-Phase Coherence
- Phase 6's `_decorate` reads `render_meta.cover_key`, which Phase 5's `stage_render` writes (`None` when the cover failed, handled by the `if cover` guard); `cover_url` signs the PNG key. Verified by the green feed tests and the demo.
- Pipeline order holds: demo and the full-pipeline test show transcribe, signals, moments, hooks, render (one stage, no `captions` job), compliance, package. Compliance now includes Phase 6 brand rows beside the Phase 5-era disclosure and caption checks.
- Phase 6 signals refresh feeds Phase 5's `_render_request` (`signals.load`) so emphasis zooms follow corrected transcripts; no regression. Earlier suites (test_3, test_4, test_10b) are green.
- No regressions in the full suite.

## Dead Code
None that blocks. Note: `Actor` is imported but unused in `backend/clipping/demo.py:11` (F401; the import predates these commits and is not in the CI rule set).

## Correctness Dimensions
| Dimension | Status | Evidence |
|-----------|--------|----------|
| Concurrency | PASS | Rotation state is DB rows; `pick` updates `fresh`/`last_used_at` in the same transaction; `_nvenc` cache is a benign module dict. No demonstrated race. |
| Error Handling | PASS | NVENC failure falls back once to x264, x264 failure surfaces; cover failure never blocks; bad template at render takes defaults; bad owner input -> 400 with a sentence. |
| Resources | PASS | No handles left open; ASS and cover files live beside the output; templates capped at 12 per account, rejection reason 500 chars, 6 rules per reason, queue limit. |
| Boundaries | PASS | Empty hook, 300-char Arabic hook, no signals, no minimum views, percentile clamped, tiny sigma, empty moments all tested. |
| Security | PASS | ASS text escaped, only a fixed file name in the filter graph, font regex rejects `;`/newline, parameterised SQL, RLS on new tables, marketplace words kept out of the audit log. |

## Loaded-Skill Criteria
| Skill | Criterion | Status | Evidence |
|-------|-----------|--------|----------|
| aposd-verifying-correctness | RC requirements coverage | PASS | Every DW mapped to code and a test above. |
| aposd-verifying-correctness | CS/EH/RM/BC/SE items | PASS | See dimensions table; no failing trace found. |
| cc-defensive-programming | External input validated at entry | PASS | Template fields, version, active flag, rejection reason, check id validated before use. |
| cc-defensive-programming | No empty catch / assertions for bugs | PASS | `_cover` catches broadly by design and returns None (cover is optional, documented); no side-effecting asserts. |
| cc-refactoring-guidance | Fix and refactor kept apart | PASS | Phase 5 replaced stages deliberately; `skin_mask` extraction in signals is behavior-preserving and covered by test_11. |
| performance-optimization | Measure-first / no hot-loop waste | PASS | Account stats computed once per account and moment scores once per source per feed; no unmeasured micro-optimisation. |
| impeccable | Operate-surface checks (touch size, RTL, contrast, overflow) | PASS | 44 px controls in Look.tsx, logical properties, AA contrast and no horizontal overflow asserted by the stylesheet audit. Look.tsx labels are English-only (see Notes). |

## Notes (non-blocking)
1. DW-6.3 deviation as above: the no-keys demo does not run local Whisper; keep the open-items entry visible until a Whisper demo is run once with the model present.
2. Look.tsx copy is English only, unlike the Feed/Queue additions that have Arabic strings.
3. `text_signals` zips loudness with the word series, so a loudness list shorter than the duration would shorten `reaction` (same pre-existing pattern as `speech_rate` and `turns`); not demonstrated.
4. `AppError("... not found")` returns 400 rather than 404 across the API; existing convention.
5. Brand reminders ("note" rows) are shown in the compliance sheet but never change the verdict, as specified.

## Issues
None.

VERDICT: PASS
