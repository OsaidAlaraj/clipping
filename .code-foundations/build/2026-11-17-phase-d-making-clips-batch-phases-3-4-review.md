# Batch review: Phase 3 (Signals) and Phase 4 (Moment engine)

## Executed results (Step 0, run once, cited by both phases)
- Backend suite: `cd backend && pytest -p no:cacheprovider -q -n auto` -> every test passed, 0 failures, 0 errors, 1 skipped (the skip is `CLIPPING_REAL_WHISPER` in test_9, unrelated). xdist printed progress to 100% and no summary line, so the count comes from the dots (no F or E in any row).
- Lint as CI: `ruff check --select E9,F63,F7,F82 backend` -> All checks passed, exit 0. Extra `F401,F841` pass over the phase files -> clean.
- Web: `npx tsc --noEmit` -> clean. `npx vitest run` -> 126 passed, 1 timed out (`transcript.test.tsx` DW-2.4, a 3000-word render with a 5 s limit) while the backend suite was using the CPU. Re-run alone on a quiet machine: `transcript.test.tsx` 43/43 passed. A load-induced timeout in an older phase's test, not a defect of these phases. `web/tests/moments.test.tsx` passed in both runs.
- Extra executions I ran (no edits, scratch files only under the scratchpad):
  - `CLIPPING_LONG_TEST=1 pytest tests/test_11_signals.py -k DW_3_3` -> a real 7200 s 720p synthetic file measured in 24.1 s (limit 180 s). The default run (360 s file, linear projection) printed "2 h projected 36s".
  - A call-recording profiler over `test_11_signals.py` and `test_12_moments.py` -> every new `def` in the phase files was executed by those tests, except the two Protocol stubs in `router.py` (`available`, `propose`).
  - Direct probes: `Profile.build` with NaN, negative, string and inverted values; `floored`; `bradley_terry` on contradictory results; `moments_llm.parse_ratings` with an injected reason, NaN, bool, extra keys, non-object input.
- Real-run evidence inspected, not re-run: `.code-foundations/build/2026-11-17-engine-run-report.md` agrees with `.data/engine-run/results.json` (Arabic 485.8 s: signals 1.3 s, moments 67.5 s, 3 LLM calls, $0.0185, 3 kept and 6 filtered, engines own 8 and topic 1, no alerts). The vendor engine stayed silent for lack of keys, as designed.

---

# Review: Phase 3 - Signals (commit 2531a74)

### DW-3.1
PREMISE:  Signals for the 60 s fixture are stored once and reused (a second run does no FFmpeg work).
EVIDENCE: backend/clipping/signals.py:236-275 (`compute`: `cached = row and row["series"] and file_hash equal`, then `return _shape(row)` when `transcript_version` is equal); stage in pipeline.py `stage_signals`; table in migrations/011_signals.sql (`source_id unique`, upsert).
TRACE:    transcribe enqueues `signals` -> `compute` finds no row -> probe plus `measure_media` (one audio pass, one video pass) -> upsert one row. Second `compute`: row cached, hash and transcript version match -> returns the stored shape; the test's FFmpeg spy sees 0 calls. After a transcript correction only `text_signals` is rebuilt from stored loudness, with no FFmpeg call.
VERDICT:  PASS. Tests: `test_DW_3_1_signals_are_stored_after_transcription_and_before_moments` (job order transcribe, signals, moments; 7 series x 60 values; chat and replay None), `test_DW_3_1_a_second_run_does_no_ffmpeg_work` (spy list empty, 1 row), `test_a_corrected_transcript_rebuilds_only_the_text_signals`. All passed.

### DW-3.2
PREMISE:  The heatmap is read from a saved yt-dlp metadata fixture, stored as `replay`, and None when missing; malformed metadata is ignored safely.
EVIDENCE: adapters/heatmap.py:22-46 (`heatmap_from_info` validates type, finiteness, 0 <= start < end <= 24 h, clamps value, caps 1000 segments; returns None when nothing is valid; `replay_series` returns None for a falsy heatmap); sources.py `youtube_signal_inputs`, `download_youtube(info_out)`, `stage_fetch_source` -> `signals.record_inputs`; fixtures fixtures/ytdlp/watch_info.json, watch_info_no_heatmap.json, live_chat.json.
TRACE:    saved watch_info.json -> segments -> `record_inputs` stores `heatmap` jsonb -> `compute` -> `heatmap.replay_series` -> `series["replay"]` (floats per second). No-heatmap fixture, `info=None`, a list, or heatmap="x" -> None -> stored NULL -> `series["replay"] = None` (absent, not a list of zeros). Garbage entries (strings, NaN, inf, bool, reversed times) are skipped with no raise.
VERDICT:  PASS. Tests: `test_DW_3_2_the_heatmap_is_read_from_a_saved_ytdlp_metadata_sample`, `..._no_heatmap_is_none_not_zero`, `..._malformed_metadata_is_ignored_safely` (parametrized), `..._a_stored_heatmap_becomes_the_replay_series`, `..._the_youtube_fetch_captures_the_heatmap_and_chat...`, `test_a_youtube_fetch_without_metadata_stores_none`, `test_a_direct_link_fetch_records_no_youtube_inputs`.

### DW-3.3
PREMISE:  The 2-hour timing target holds on a synthetic long file, or the measured shortfall is reported.
EVIDENCE: ffmpeg.py `audio_pass`, `video_thumbs` (keyframes only above 1200 s), signals.py `measure_media`; test `test_DW_3_3_a_synthetic_long_file_is_measured_fast_enough_for_two_hours_in_three_minutes` (opt-in `CLIPPING_LONG_TEST=1` builds the real 2 h file).
TRACE:    I ran the opt-in 7200 s case: 24.1 s measured against 180 s. The default 360 s case projects to 36 s. Real engine run: 1.3 s of signals for a 486 s interview, 16.4 s for 600 s of the English podcast.
VERDICT:  PASS. The target is met on a synthetic 2 h file. Caveat in Notes: the file is a low-motion 10 fps test pattern, and the default CI test extrapolates linearly from 6 min.

### DW-3.4
PREMISE:  Laughter, reaction words and chat bursts are detected on fixtures (a laugh clip, an Arabic and an English reaction line, a saved chat replay), and each falls back to "absent" when its input or model is missing.
EVIDENCE: audiotag.py (`pick_tagger`: SherpaTagger only when the package and both model files exist, else `BurstTagger`; a constructor failure falls back); signals.py `_reactions`, `REACTIONS`; heatmap.py `chat_counts_from_live_chat`, `read_live_chat`, `chat_series`.
TRACE:    laugh clip -> loudness-burst scorer -> laughter > 0 on the burst seconds and 0 on steady loud speech. "no way!" -> reaction[3]=1; "وَاللَّهِ مستحيل" -> reaction at 10 and 11; "يا الله" -> 20 (diacritics and alef folding handled). Saved live_chat.json -> per-second counts -> `chat` series. Missing replay -> `chat_series(None)` -> None. Missing sherpa package or model files -> BurstTagger; a broken model -> BurstTagger. No matching words -> reaction all 0.0.
VERDICT:  PASS. Tests: `test_DW_3_4_laughter_is_found_in_a_laugh_clip_by_the_loudness_burst_fallback`, `test_DW_3_4_the_model_tagger_is_used_when_installed...`, `..._a_broken_model_falls_back...`, `..._the_model_is_absent_when_the_package_or_files_are_missing`, `test_DW_3_4_reaction_words_in_english_and_arabic`, `..._reactions_are_absent_without_matching_words_or_words`, `test_DW_3_4_chat_bursts_are_counted_per_second_from_a_saved_replay`, `..._chat_is_absent_without_a_replay`.

**All Phase 3 requirements met:** YES

### Phase 3 edge cases (prompt-listed)
| Edge case | Result |
|---|---|
| 2-hour source in about 3 minutes | Handled: 24.1 s measured (above). |
| No heatmap -> None, not zero | Handled (`replay_series`, `chat_series` return None; stored NULL). |
| Silent or music-only -> speech signals zero | Handled for `speech_rate` and `turns` (masked where loudness <= -60 LUFS; traced and tested). A music-only stretch has no transcript words, so speech_rate is 0 there. See Note N3 about `reaction`. |
| Corrupted media -> AppError, job failed with alert | Handled: probe raises `FFmpegError`, which `compute` turns into `AppError(code="bad_media")`. `stage_signals` re-raises AppError, so the worker marks the job failed and raises `job_failed`. `test_a_corrupted_file_is_an_app_error_and_the_job_fails_with_an_alert` asserts status failed and the alert. |

### Phase 3 test-coverage level (100%)
- Every new routine in signals.py, heatmap.py, audiotag.py, ffmpeg.py (`_stream`, `audio_pass`, `video_thumbs`), sources.py (`youtube_signal_inputs`, `download_youtube`) and pipeline.py `stage_signals` was executed by test_11 (profiler). Direct assertions exist for the public ones. A few small helpers (`_cosine`, `_round`, `_shape`, `_inputs_series`, `_flush`, `_reaction_phrases`) are covered through their callers, with no test naming them. I treated execution under asserting tests as meeting the level (Note N5).
- Gaps: none that is an unexecuted routine.

### Phase 3 dead code
None found. The `Decimal` import and `STEP_S` are used.

### Phase 3 correctness dimensions
| Dimension | Status | Evidence |
|---|---|---|
| Concurrency | PASS | The audio thread and the video loop are joined by `with ThreadPoolExecutor` and `result()`. The upsert is on a unique key. No shared mutable state between threads. |
| Error Handling | PASS | FFmpeg failure -> `FFmpegError` -> AppError. stderr goes to a temp file, so a chatty corrupt file cannot deadlock a pipe. An unexpected exception in `stage_signals` -> `signals_unavailable` alert, and moments continues. All parsers return None and never raise. |
| Resources | PASS | `_stream` kills the child in `finally` and closes stdout. Temp dir in a context manager. Chat file capped (100 MB, 3 M lines). Duration capped at 24 h. Long videos use keyframe-only decode. |
| Boundaries | PASS | Empty or short audio is padded or cut by `_fit`. Word spans are capped (MAX_WORD_S). A negative or out-of-range timestamp cannot index outside the series (traced in `_reactions` and `_speech_rate`). |
| Security | PASS | yt-dlp metadata and chat files are treated as untrusted. `source_signals` has RLS and force RLS with the same policies as earlier tables (test `..._hides_other_owners_rows`). Nothing user-controlled reaches a shell. The filter graph uses a fixed relative log name. |

---

# Review: Phase 4 - Moment engine (working tree)

### DW-4.1
PREMISE:  On a fixture episode the engine returns ranked, sentence-snapped, de-duplicated moments with reasons, with or without the LLM.
EVIDENCE: moments.py:262-286 (`find`), moment_rank.py (`dedupe` 470-482, `select` 539-573, `_span` snaps to sentence edges), moments.py `_candidate` (reasons).
TRACE:    transcript -> own, topic (and vendor) proposals -> `_valid` -> hard gates (including "starts in the middle of a sentence") -> `dedupe` -> features -> LLM rating and pairwise comparison -> `select` -> `_finish` -> `_store`. With the LLM paused, `_rate` catches AppError, sets `llm_state="unavailable"`, signals alone rank, a reason says so, and a `moments_signals_only` alert is raised.
VERDICT:  PASS. Tests: `test_DW_4_1_a_fixture_episode_gives_ranked_snapped_deduplicated_moments_with_reasons`, `..._without_the_llm_the_signals_alone_rank_them_and_say_so`, `..._a_long_episode_is_mapped_in_chunks_pooled_and_ranked_with_bounded_llm_calls`.

### DW-4.2
PREMISE:  Each pair is asked in both orders, and swapping the order does not change the final rank in the fake.
EVIDENCE: moments.py:211 (`for order in (pairs, [(b, a) for a, b in pairs])`), `moment_rank.bradley_terry` 493-513.
TRACE:    a first-biased fake model answers "first" to every pair -> each pair is won once and lost once -> half a win each way -> equal strengths -> normalised 0.5 for all -> the sort falls to `-signal`. I confirmed that `bradley_terry(["a","b"], [("a","b"),("b","a")])` gives `{a: 1.0, b: 1.0}`.
VERDICT:  PASS. Tests: `test_DW_4_2_every_pair_is_asked_in_both_orders`, `..._swapping_the_order_does_not_change_the_final_rank_in_the_fake`, `..._contradictory_answers_come_out_as_ties_and_the_signal_score_breaks_them`, `test_bradley_terry_ranks_by_wins_and_keeps_a_clean_sweep_finite`, `test_pair_plan_...`.

### DW-4.3
PREMISE:  Synthetic approvals, rejections and 48 h views change the weights after 30 labels and not before.
EVIDENCE: moment_learning.py:98-116 (`learn`: `if len(labels) < MIN_LABELS: continue`), `sync_labels` 49-67, `_fit` 75-95, `weights_for` 23-27.
TRACE:    29 labels -> no `profile_weights` row -> `weights_for` returns profile defaults. 30 labels -> `_fit` moves weights toward liked-versus-disliked feature means (blend = n / (n + 30)) -> row stored -> `find` reads it.
VERDICT:  PASS. Tests: `test_DW_4_3_29_labels_change_nothing_and_30_change_the_weights`, `..._labels_come_from_reviews_and_views_and_teach_the_engine_and_the_hook_patterns`, `test_an_undone_rejection_and_other_owners_decisions_are_not_labels`. See Note N1: `learn` is not called from any production path.

### DW-4.4
PREMISE:  An avoid-list hit removes the candidate, and a must-include hit raises it.
EVIDENCE: moments.py `_gate` + moment_rank `hard_gate` (avoid hit gives layer 1 with a reason), `adjust` (+0.08 per include hit, max 0.16).
TRACE:    a window containing a forbidden term -> filtered (status 'filtered', reason names the term). A window with a must-include term gets a higher score than the same window without it.
VERDICT:  PASS. Tests: `test_DW_4_4_an_avoid_list_hit_removes_the_candidate_with_its_reason`, `..._a_must_include_hit_raises_the_score`, `test_every_candidate_on_the_avoid_list_gives_no_moments_and_an_alert`, `test_brief_terms_read_the_structured_lists_and_the_plain_rules`.

### DW-4.5
PREMISE:  Each winner has three cut variants. When a vendor adapter is active, its picks join as candidates marked with their origin.
EVIDENCE: moment_rank `make_variants` (hook_first, with_setup, tight), moments.py `_choose_cut`; moment_engines.py `VendorEngine` (source = provider name -> `origin`; `engine` = "vendor").
TRACE:    winner -> `make_variants` -> 3 spans, tight is the default if inside the campaign limits. The vendor fake returns picks -> snapped, fitted -> stored with `origin` = provider.
VERDICT:  PASS. Tests: `test_DW_4_5_each_winner_has_three_cut_variants_and_tight_is_the_default`, `test_variants_trim_filler_add_setup_and_end_after_the_laugh`, `test_DW_4_5_vendor_picks_join_as_candidates_marked_with_their_origin`, `test_the_vendor_engine_is_silent_without_keys_when_fakes_are_off_and_when_paused`.

### DW-4.6
PREMISE:  The same fixture episode run under the podcast and the stream profile gives different picks, lengths and crop styles, as the profiles define.
EVIDENCE: migrations/012_moments.sql profile rows, profiles.py `Profile`, moments.py `_source`/`_finish` (`style`, `profile.length`).
TRACE:    podcast: 30-60 s, `follow_speaker`, route own/topic/vendor. stream: 20-45 s, `facecam_over_game`, route hype/own/vendor, peaks-heavy weights. The two runs differ in length range, style and route, and the test asserts different picks.
VERDICT:  PASS. Tests: `test_DW_4_6_podcast_and_stream_profiles_give_different_picks_lengths_and_styles`, `test_DW_4_6_seven_profiles_and_a_general_one_are_seeded_as_data`, the niche parametrization (unknown niche -> general), `test_a_profile_with_missing_or_broken_fields_uses_the_defaults`, `test_the_campaign_niche_and_the_owners_per_video_choice_pick_the_profile`.

### DW-4.7
PREMISE:  A quiet fixture episode still yields its profile's minimum number of clips; a hard-gate hit is filtered with its reason; a rescued filtered moment is stored as a label for learning; an LLM "funny" with no laughter scores lower than the same moment with laughter.
EVIDENCE: moment_rank `select` 539-573 (top-up to `min_clips`, then `pack`), `hard_gate`, moments.py `rescue` 350-376 (inserts `moment_labels` kind 'rescue', records an engine win, enqueues hooks), moment_rank `corroboration` 389-404 and `apply_llm`.
TRACE:    quiet episode -> all scores below the cut-off -> `select` tops up to min_clips with a "Kept to reach the minimum" note. Rescue of a filtered moment -> status 'new', label row, hooks job queued. "funny" rating with the laughter series 0 -> trust 0.5 and a note -> lower merged emotion and overall than the same window with laughter 1.0.
VERDICT:  PASS. Tests: `test_DW_4_7_a_quiet_episode_still_yields_the_profiles_minimum_number_of_clips`, `..._a_hard_gate_hit_is_filtered_with_its_reason_and_layer`, `..._a_rescued_moment_is_cut_and_stored_as_a_label_for_learning`, `test_rescue_refuses_a_kept_moment_and_an_unknown_one`, `test_the_api_lists_filtered_moments_in_the_feed_and_rescues_one`, `..._an_llm_funny_with_no_laughter_scores_lower...`, `test_anger_is_not_boosted_for_a_brand_profile...`; web `moments.test.tsx` (5 tests).

### DW-4.8
PREMISE:  Two profiles take different routes; candidates from every engine on the route pass through the same filter and carry their engine as origin; recorded wins shift a profile's engine weights while every engine keeps a minimum share of tries.
EVIDENCE: router.py `route`, `shares`, `floored`, `allocate`; moments.py `_gather` (every proposal is tagged `engine`, then goes through `_gate`, `dedupe`, `_features`).
TRACE:    `floored` lifts any share below MIN_SHARE (0.10) and rescales the rest; `allocate` gives each engine at least one slot. I checked `floored({a:100,b:.001,c:.001}, .1)` -> `{a:.8,b:.1,c:.1}`. Wins raise an engine's Laplace-smoothed rate. One engine raising leaves a note and the others still run.
VERDICT:  PASS. Tests: `test_DW_4_8_two_profiles_take_different_routes`, `..._candidates_from_every_engine_pass_the_same_filter_and_carry_their_engine`, `..._recorded_wins_shift_engine_weights_and_every_engine_keeps_a_minimum_share`, `test_the_floor_holds_for_any_mix_of_results`, `test_an_engine_that_blows_up_is_noted_and_the_others_carry_on`.

### DW-4.9
PREMISE:  Hooks carry their pattern; recorded approvals and 48 h views shift pattern weights per language and profile, and every pattern keeps a minimum share; a new pattern added as data is used without a code change.
EVIDENCE: hook_patterns.py (`library` reads the DB per language and profile, `weights` with `floored(raw, 0.08)`, `pick`, `record`); pipeline.py `_with_pattern_hook` and the `hooks.pattern` column; migrations/012 seeds 7 patterns x 2 languages.
TRACE:    an approval or high-views label -> `record(... "approve"/"views_high")` -> weights are computed from stats keyed by (owner, pattern, language, profile) -> shares shift, none under 0.08. Insert a new `hook_patterns` row -> `library` returns it -> `pick` can choose it, with no code edit. `stage_hooks` stores `pattern` on the hook row.
VERDICT:  PASS. Tests: `test_DW_4_9_hooks_carry_their_pattern`, `..._the_pipeline_stores_the_pattern_on_the_clips_hook`, `..._approvals_and_views_shift_pattern_weights_per_language_and_profile`, `..._every_pattern_keeps_a_minimum_share_of_the_choices`, `..._a_new_pattern_added_as_data_is_used_without_a_code_change`, `test_a_hook_that_is_not_paid_off_in_the_clip_is_dropped_and_concreteness_is_rated`.

**All Phase 4 requirements met:** YES

### Phase 4 edge cases (prompt-listed)
| Edge case | Result |
|---|---|
| Exactly 3, fewer than 3 candidates; windows at 15 s and 60 s | Handled: `test_exactly_three_fewer_than_three_and_tiny_episodes_do_not_crash` (60 s, 20 s, 7 s), `test_windows_at_the_15_and_60_second_edges`. |
| LLM fails partway -> keep scores so far, finish on signals | Handled: `_rate` and `_compare` catch AppError and LLMAnswerError, set `llm_state` partial or unavailable, keep earlier scores. `test_the_llm_failing_partway_keeps_the_scores_so_far`. |
| Contradictory pairwise answers -> Bradley-Terry, ties by signal | Handled (traced above, tested). |
| Every candidate on avoid list -> none plus an alert | Handled: `_alerts` raises `moments_all_avoided` when `gated == pooled`. Tested. |
| Non-JSON or instruction-injecting model output -> rejected, no answer | Handled: `parse_ratings` raises `LLMAnswerError` for a non-dict, extra keys, injected phrases (EN and AR regex), and no valid rating. My probes confirm. `test_model_output_that_is_not_json_or_tries_to_change_instructions_counts_as_no_answer`, `test_a_transcript_that_gives_orders_stays_data`. |
| Unknown niche -> general; profile with missing fields -> defaults | Handled (probe: `clips="abc"`, NaN weights, inverted lengths, `route="own"`, `min_clips=None` all fall back). |

### Phase 4 security-sensitive items
- Transcript text and brief rules cannot change LLM instructions: instructions are fixed per language in `vendors.LLM_SYSTEM` (moment_score and moment_compare), and transcript, avoid and include terms, and patterns travel only in the JSON user payload. PASS.
- Model output validated strictly: exact key set, ids must be ones we sent, numbers finite and in 0..1 (bool rejected), text control-stripped and capped, quotes in hooks must appear in the clip text. PASS.
- Model never sets times: `parse_ratings` ignores any `start_s`/`end_s` (probe shows they are dropped); proposals from engines go through `_valid` (clamped to the duration, minimum 3 s, finite) and the vendor path through `snap` and `fit`. PASS.
- RLS: engine_stats, hook_pattern_stats, moment_labels and profile_weights have enable plus force RLS and the owner policies. content_profiles and hook_patterns are readable by all and writable by admin only. PASS (test hides other owners' rows for signals; policies match 003).
- Rescue owner-scoped: `rescue` requires an operator actor, then runs `select ... from candidates where id` under the actor's RLS session, so another owner's candidate is not found. PASS by RLS. There is no test with a second owner (Note N4).

### Phase 4 older tests changed (weakening check)
- test_3_pipeline.py: the exact fake scores `[0.91, 0.78, 0.66]` became `sorted descending, in (0,1], rank 1..3, origin and profile "podcast"`. The old fake vendor path no longer produces those scores, so a replacement exists; it is weaker on exact values, but the engine's own tests pin the scores. The Arabic caption check went from "contains one of four named words" to "contains an Arabic letter in Events" (weaker, but the text now depends on the engine's windows). The banned-words list was widened so every hook trips compliance (still tests the fail path). `test_moment_adapter_failure_falls_back_to_manual_selection` was split into a paused-vendor test (engine carries on, no manual alert) and a whole-engine-failure test (manual alert kept).
- test_10b_pilot.py: `len(feed) == 2` -> 3 and `20 <= duration <= 45` -> `8 <= ... <= 45` (the 60 s source shrinks the range, per `length_for`). The bad-LLM-output test changed from "manual alert and zero candidates" to "job still done, no pilot candidates, at least one non-filtered candidate". Paused LLM and failing adapter now assert `moments_signals_only` plus at least one 'new' candidate. The bug-in-engine test still asserts the manual alert. None lost its failure-mode coverage; each has a replacement for the new behavior.

### Phase 4 test-coverage level (100%)
- Profiler run: every new `def` in profiles, router, moment_engines, moment_rank, moments, moment_learning, hook_patterns, moments_llm and the new fake_llm functions was executed by test_11/test_12 (only the Protocol stubs were not). The changed pipeline routines (`stage_moments`, `_schedule_candidates`, `_with_pattern_hook`, `stage_hooks`), api routes (feed `filtered`, rescue, profiles, set profile), `moments_pilot.propose_with_cost(length=)` and `progress.overview` run in test_12/test_3/test_10b/test_12 API tests. Web: Feed `FilteredSheet`, `rescue` and the empty-feed entry are covered by `moments.test.tsx`.
- Routines with no test naming them and covered only through `find` or `learn`: moment_rank `percentile`, `dedupe`, `pack`, `reasons_for`, `loud_norm`, `lines_in`, `next_word_start`; moment_engines `top_windows`, `scan`, `hype_map`; router `owner_of`; moment_learning `sync_labels`, `_insert`; moments `set_profile`, `_gather`, `_valid`, `_choose_cut`; profiles `for_source`, `listing`. Their behavior is asserted through the DW tests (for example dedupe in DW-4.1, min-clips and pack in DW-4.7, hype in DW-4.6 stream). I did not count that as a gap (Note N5).

### Phase 4 dead code
None blocking. `moments.py` re-exports `learn` with `# noqa: F401`, and `moments_pilot.propose` (the pinned contract) is now only used by tests. Both are intentional.

### Phase 4 correctness dimensions
| Dimension | Status | Evidence |
|---|---|---|
| Concurrency | PASS | The engine runs inside one worker transaction. Stats updates use `on conflict ... do update x = x + excluded.x` (atomic increments). `_store` deletes and re-inserts the uncut candidates in one session (`test_finding_again_replaces_the_uncut_moments...`). |
| Error Handling | PASS | Every engine call is wrapped (one failing engine is noted, the others run). LLM errors degrade with an alert. KillSwitchOn is re-raised on purpose. `stage_moments` falls back to the manual-selection alert on any exception. |
| Resources | PASS | LLM calls bounded (1 rating call, at most 2 compare calls x 12 pairs), stored filtered moments capped at 25, the pool bounded by `router.POOL_SLOTS` and `4 x target`. The vendor poll loop is bounded (120 x 5 s). |
| Boundaries | PASS | Probes: `Profile.build` garbage -> defaults; `floored` with extreme shares; `_valid` rejects NaN, negative and sub-3 s windows and clamps the end to the duration; 3 / <3 candidates and 15 s / 60 s windows tested. |
| Security | PASS | See the security list above. SQL in `hook_patterns.record` interpolates the column name from a fixed dict (`OUTCOMES`), never from input. |

---

# Loaded-skill criteria (both phases)

| Skill | Criterion | Status | Evidence |
|---|---|---|---|
| performance-optimization | Measure before claiming a target; I/O batched; keep hot paths linear | PASS | DW-3.3 was measured (24.1 s for 2 h, real file). Engine run timed per stage with the real providers. FFmpeg is decoded once per medium, in two passes run side by side, with keyframes only above 20 min. LLM cost is bounded and measured ($0.018 per source). |
| performance-optimization | No unmeasured micro-optimizations; algorithmic choices sound | PASS | `scan` is bounded by the max window length; `topic_segments` is O(n^2) in blocks (about 1500 blocks for 2 h, trivial); `pack` is O(count x n). |
| aposd-designing-deep-modules | Narrow interface hiding a large implementation | PASS | `signals.compute/load`, `moments.find/rescue`, `router.register/route`, `audiotag.pick_tagger` hide the models, FFmpeg and the engines. Callers never import sherpa-onnx. |
| aposd-designing-deep-modules | No information leakage or silent failure | PASS | A missing signal is None ("absent"); `llm_state`, `notes` and alerts surface degradation. Minor leakage in Notes N6. |
| cc-pseudocode-programming | Routines have one clear purpose and intent-level docs | PASS | Docstrings state purpose, inputs, outputs and fallbacks (`compute`, `find`, `select`, `corroboration`). |
| cc-control-flow-quality | Nesting <= 3, guard clauses, no unreachable code | PASS | No nesting beyond 3 in the new code that I traced; guard clauses used in `rescue`, `_valid`, parsers. `brief_terms` and `make_variants` are above the soft McCabe threshold (Note N6); a matter of degree, not a demonstrated defect. |
| aposd-verifying-correctness | Requirements mapped, errors, resources, boundaries, security | PASS | Sections above (RC, EH, RM, BC, SE all traced). Concurrency: no shared mutable state beyond DB upserts. |

---

# Cross-Phase Coherence

- Phase 4 consumes Phase 3 as actually exposed: `signals.load` returns None when no row or no series exist, and `Episode.__init__` takes `(signals_ or {}).get("series") or None`. `has()` returns False for missing or None series (`chat` and `replay` are None when absent), `hype_map` coerces `v or 0`, `_peaks` and `corroboration` skip absent series. `test_signals_missing_the_engine_still_works` and the engine-run (signals present) both pass. `Episode.topics` falls back to `signals.topic_segments` when none are stored; `STOPWORDS` and `topic_segments` are used as exposed.
- Pipeline order transcribe -> signals -> moments -> hooks holds: `stage_transcribe` enqueues `signals`, `stage_signals` enqueues `moments`, `stage_moments` -> `_schedule_candidates` -> `hooks`. `test_pipeline_produces_packaged_compliant_clips` asserts the job set including `signals`, and `test_DW_3_1` asserts order[:3].
- Regressions: whole backend suite green, including phases 1-3, 2P (test_10b) and the pipeline tests (changed on purpose, reviewed above). Lint and tsc clean. The single vitest timeout is load-induced and passes alone.
- Contradictions: none. `signals_unavailable` (Phase 3 alert) and `moments_signals_only` (Phase 4 alert) do not overlap; a signals failure leaves `load` None and Phase 4 degrades correctly.
- One seam observation (not a defect against a listed requirement): see N2.

# Notes (non-blocking)

- N1. `moment_learning.learn()` (and so `sync_labels`) has no caller in production code: `moments.py` only re-exports it, and no worker, schedule or API route invokes it. Result: in the running system `profile_weights` is never written, and approvals, rejections and 48 h views are never turned into labels or pattern and engine stats (only `rescue` and `try` counts update live). DW-4.3 and DW-4.9 are phrased as behaviour on synthetic labels and pass, but the owner-visible learning loop is not wired. Worth confirming that wiring is planned in a later phase.
- N2. `signals.load` does not check `transcript_version` or `file_hash`. Phase 3 rebuilds text signals only when `compute` is called, and only the pipeline calls it. If an owner corrects the transcript and moments are re-run without a new signals job, Phase 4 reads stale `reaction`, `speech_rate`, `turns` and `topics`.
- N3. In silence (loudness <= -60 LUFS) `reaction` is not masked, unlike `speech_rate` and `turns`. Probe: a "wow" word at 3 s with loudness -70 gives `reaction[3] == 1.0` and `speech_rate == 0`. I read "speech signals" in the edge case as `speech_rate` and `turns` (the module's own definition, and what the test asserts), and music-only stretches have no transcript words, so this only matters for a hallucinated word in dead silence. If the intent includes `reaction`, mask it the same way.
- N4. There is no test where a second owner tries `POST /api/candidates/{id}/rescue` or `PUT /api/sources/{id}/profile` on another owner's row. Scoping rests on RLS, which I read and which is the same mechanism as the rest of the system.
- N5. Direct unit tests are missing by name for the helpers listed under Phase 3 and Phase 4 coverage; they are exercised and asserted only through the public routines. If "100%" is meant as one named test per routine, that is the list to add to.
- N6. Design: `moment_rank` and `moment_engines` use private-looking names across modules (`rank._span`, `moments_pilot` aliases `sentences, chunks, fit ...` assigned from underscore names); `VendorEngine` imports `pipeline` lazily to avoid a cycle. `brief_terms` (about 20 branches) and `make_variants` (long compound conditions on single lines, with odd run-on indentation at moment_rank.py:609 and 619) are the highest-complexity routines.
- N7. DW-3.3 caveats: the synthetic file is a low-motion 10 fps 720p test pattern; real 1080p 30 fps would decode slower, though keyframe-only decoding above 20 min offsets much of that. The default CI test extrapolates linearly from 6 min with a single-file timing, so it can flake on a loaded machine. The commit message quotes 83 s for 2 h; my run measured 24.1 s. Both are far under 180 s.
- N8. `FilteredSheet` copy ("Nothing is hidden...", layer labels) is hard-coded English; only the button labels are in the i18n table.

# Issues
None blocking.

VERDICT: PASS
