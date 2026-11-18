# Discovery + Design: Phase 4 - Moment engine

## Files Found
- `backend/clipping/moments_pilot.py` (interim LLM picker: `_sentences`, `_chunks`, `_fit`, `_overlap`, `_parse`, `propose_with_cost`), `pipeline.py` (`stage_moments`, `_pilot_moments`, `_store_candidates`, `snap`, `sentence_bounds`, `stage_hooks`), `signals.py` (`load`, `compute`, `topic_segments`), `transcripts.py` (`load`, `normalise`), `adapters/fake_llm.py` + `adapters/vendors.py` (LLM prompts per task, `ContentFiltered`, `VizardMoments`/`KlapMoments`/`FakeMoments`), `review.py` (`feed`, `CLIP_VIEW`, `reject`/`accept` write `reviews`), `progress.py` (reads `candidates`), `api.py`, `web/components/Feed.tsx`, `web/lib/api.ts`, migrations 001 (`candidates`, `reviews`, `metrics`, `posts`), 003/007/011 (RLS patterns).
- Tests that touch the moment stage: `test_3_pipeline.py` (fake vendor windows, scores 0.91/0.78/0.66; paused moments adapter -> manual selection), `test_10b_pilot.py` (practice campaigns use the pilot picker; bad LLM output -> manual-selection alert), `test_10c_content_filter.py`, `test_10d_progress.py`, `test_11_signals.py`.

## Current State
- Practice campaigns: `_pilot_moments` (one LLM call per ~10 min stretch). Real campaigns: the vendor `moments` adapter (the seeded active provider is `fake`; Vizard/Klap only with keys).
- Candidates carry `provider, start_s, end_s, score, rank, reason, status`. No variants, features, profile, or filter state. Signals exist (`signals.load`, may be None). No profile, router, label or hook-pattern data.
- Review reads clips only; nothing shows a moment that was dropped.

## Gaps (plan vs reality) and how they are closed
- Plan says migration `011_*`; 011 is taken: **012_moments.sql** (brief).
- Plan file scope lists only moments.py/moments_llm.py; the plan's own Produces also needs `profiles`, `router`. Added modules (all new, nothing renamed): `profiles.py`, `router.py`, `moment_engines.py`, `moment_rank.py` (pure scoring), `hook_patterns.py`, `agents/moments_llm.py`. `moments_pilot.py` stays untouched except public aliases; it is registered as the `pilot` engine.
- `candidates.status` check has no "filtered": widened additively (filtered rows get status `filtered`, so no hooks job and progress counts ignore them).
- The brief has no structured avoid list. Sources of terms, in order: optional `rules_json.avoid_terms` / `must_include_terms` lists, then forbidden/required `content_rules` (quoted words and the phrase after "do not mention / avoid / no ..."), and for must-include also the campaign key terms. Length rules: `content_rules` of category duration (numbers + "seconds") and the settings `clip_min/max_duration_s`.
- Hook patterns: `hooks.hook_type` is a closed 5-value check, so the library is its own table and `hooks` gets an additive nullable `pattern` column; the pattern hook is inserted as one more hook of the clip by `stage_hooks`.
- Old pipeline tests that pin the pilot/vendor-only behaviour of `stage_moments` must change (intentional replacement): they are updated, not weakened (see Test Plan).
- Vendor engine availability: the active `moments` adapter, if it is real with keys (health ok), or any active adapter when `CLIPPING_USE_FAKES` is on (demo/tests: the fake vendor stands in). With fakes off and only the fake adapter, the vendor engine is silent (its rough windows must not pollute real runs).
- Cost: gpt-5-mini; the engine makes at most 1 absolute-scoring call (top 12, with hook requests) + 2 pairwise calls (one per order, <= 12 pairs each) per source; pilot/vendor engines add their own only when on the route. Cost is returned from `find` and recorded on the job.

## Code Standards
Registry for every external call; `AppError` for refusals; heavy imports inside functions; one migration with RLS in the same file; test names are sentences with DW ids; no secrets or brief text in audit/logs (audit carries ids and counts); LF endings.

## Test Infrastructure
pytest + embedded Postgres per test (`db`, `owner`, `produced`, `footage`), fakes everywhere, `fake_llm.FAKE_MOMENTS`-style module dict for the new LLM tasks, `speech()`/`practice_source()` helpers (test_10b) rebuilt in the new test file. Web: vitest `mockApi`, `feedRoutes`, `clip()`.

## DW Verification

| DW-ID | Done-When Item | Status | Test Cases |
|-------|---------------|--------|------------|
| DW-4.1 | Ranked, sentence-snapped, de-duplicated moments with reasons, with or without the LLM | COVERED | test_DW_4_1_a_fixture_episode_gives_ranked_snapped_deduplicated_moments_with_reasons; ..._without_the_llm_the_signals_alone_rank_them_and_say_so; ..._long_episode_uses_chunks |
| DW-4.2 | Each pair asked in both orders; swapping order does not change the final rank in the fake | COVERED | test_DW_4_2_every_pair_is_asked_in_both_orders; ..._swapping_the_order_does_not_change_the_rank; ..._contradictory_answers_fall_back_to_signal_score; bradley_terry unit tests |
| DW-4.3 | Synthetic approvals/rejections/48 h views change weights after 30 labels, not before | COVERED | test_DW_4_3_29_labels_change_nothing_30_change_the_weights (accept, reject, h48 views, rescue) |
| DW-4.4 | Avoid-list hit removes the candidate; must-include hit raises it | COVERED | test_DW_4_4_an_avoid_list_hit_is_filtered_with_its_reason; ..._must_include_hit_raises_the_score; every candidate on the avoid list -> none plus an alert |
| DW-4.5 | Each winner has three variants; a vendor adapter's picks join marked with their origin | COVERED | test_DW_4_5_each_winner_has_three_cut_variants; ..._tight_is_default; ..._vendor_picks_join_with_their_origin |
| DW-4.6 | Podcast vs stream profile on one fixture: different picks, lengths, crop styles | COVERED | test_DW_4_6_podcast_and_stream_profiles_give_different_picks_lengths_and_styles; unknown niche -> general; missing fields -> defaults |
| DW-4.7 | Quiet episode keeps the minimum; hard-gate hit filtered with reason; rescue stored as a label; "funny" without laughter scores lower | COVERED | test_DW_4_7_a_quiet_episode_still_yields_the_minimum; ..._a_hard_gate_hit_is_filtered_with_its_reason; ..._a_rescue_is_stored_as_a_label; ..._funny_without_laughter_scores_lower |
| DW-4.8 | Two profiles take different routes; every engine's candidates pass the same filter and carry their engine; wins shift engine weights, minimum share kept | COVERED | test_DW_4_8_two_profiles_take_different_routes; ..._every_engine_goes_through_the_same_filter; ..._wins_shift_engine_weights_and_every_engine_keeps_a_minimum_share |
| DW-4.9 | Hooks carry their pattern; approvals/views shift pattern weights per language and profile; minimum share; new pattern as data used | COVERED | test_DW_4_9_hooks_carry_their_pattern; ..._approvals_and_views_shift_pattern_weights; ..._every_pattern_keeps_a_minimum_share; ..._a_new_pattern_added_as_data_is_used |

**All items COVERED:** YES (9/9)

## Design Decisions (design it twice)

### Approaches
- **A: one big `moments.find` that does everything inline** (engines, features, LLM, filter).
- **B: layered, data-driven (chosen):** `profiles` (data) -> `router` (which engines, how many slots each, bandit) -> `moment_engines` (each only proposes) -> `moment_rank` (pure functions: gates, features, soft score, Bradley-Terry, selection, variants) -> `moments` (orchestration, LLM calls through `agents/moments_llm`, storage, rescue, labels, learning).
- **C: a rules engine in the database** (gates and scores as rows). Rejected: too much indirection; weights and profile settings are data, the algorithms are code.

| Criterion | A | B | C |
|---|---|---|---|
| Interface (callers) | 1 function, huge | `find`, `rescue`, `learn`, `profiles.get`, `router.route/record` | many |
| Information hiding | none | engines hide how they propose; rank hides math; moments hides LLM+DB | leaks SQL everywhere |
| Testability of each rule | poor | each pure function tested alone | poor |
| New engine/profile/pattern without release | no | yes (register; rows) | yes |

Depth: `find(conn, source_id, version, profile=None)` hides ~10 decisions behind one call; engines expose only `propose`.

### Pseudocode of `moments.find`
```
find(conn, source_id, version, profile=None):
  ctx   = context(conn, source_id, version)          # words, sentences, signals-or-None, brief terms, campaign length rules
  prof  = profile or source override or campaign niche -> profiles.get   (unknown -> general, missing fields -> defaults)
  names = router.route(conn, prof)                    # profile route, minus engines not available (no keys, no signals)
  pool  = for each engine in names: engine.propose(ctx, prof)   # failure of one engine is noted, never fatal
          each candidate is fitted to sentences and the length range; the pool takes each engine's share of slots
          (bandit weights, every engine at least MIN_SHARE); then duplicates (overlap, topic words) are removed
  layer 1: hard gates -> filtered(layer 1, reason)
  layer 2: features + soft score (signals and text heuristics, learned weights once >= 30 labels, must-include boost)
           top ~12 -> ONE LLM call: absolute subscores + emotion + hook in a requested pattern; LLM "funny"/"hype" is
           discounted without laughter / loudness-chat-reaction evidence; any LLM failure -> signals only
  layer 3: top ~6-8 -> pairwise, every pair in BOTH orders (two calls), Bradley-Terry; ties by signal score
  select: threshold = max(floor, percentile) of the episode; cap = profile target; keep >= min clips unless gates removed all
          rest -> filtered(layer 2 or 3, reason)
  variants: hook_first / with_setup / tight (tight default); laughter tail added to the end (R5)
  store: candidates rows (kept: status new; filtered: status filtered); return {"kept","filtered", "profile","llm","cost","notes"}
```
Every engine proposes; only this function decides (plan: "engines only propose and never decide").

### Key choices
- **Features** (0..1, each with evidence text): hook (first 3 s: question/number/claim/short + concreteness "just right" R4 + opening loudness), standalone, payoff, emotion (R1: arousal classes; anger not boosted for promo), value (R2, penalty for purely informational unless the brief/profile wants information), energy (scene + face), peaks (loudness/chat/replay/reaction vs episode), laughter (R5), length_fit. A feature that needs a missing signal is absent and the weights renormalise over what exists.
- **Soft score** = weighted mean (profile weights, or learned) blended 50/50 with the LLM's overall when present; discounts per corroboration rule.
- **Bradley-Terry** by MM iterations with a 0.5 pseudo-win per pair for stability; final score of the top set = 0.5 soft + 0.5 normalised BT; ordering by score, ties (within 1e-6) by signal score.
- **Selection**: threshold = max(0.35, 55th percentile of soft scores); target = clamp(profile.clips * duration / full_episode, min_clips, max_clips); fewer than min_clips above the threshold -> the best remaining are kept with "kept to reach the minimum".
- **Short sources**: if the video cannot hold `min_clips` windows of the profile's minimum length, the length range shrinks proportionally (floor 8 s) so a 60 s fixture still yields 3 moments.
- **Own engine**: map = chunks (`moments_pilot._chunks`, 10 min, 20% overlap, cut at long pauses); per chunk windows from every sentence start with up to 6 end options, cheap score from the same features, non-max suppression; pool = union; rank = the shared pipeline. Zero LLM cost.
- **Topic engine**: `signals.topics` (else `signals.topic_segments` on the words): per topic the opener window and the best-scoring window. **Hype engine**: smoothed per-second hype (loudness above median, laughter, reaction, chat, replay) with profile weights; peaks -> windows with setup before and laughter tail after; needs signals. **Pilot engine**: `moments_pilot.propose_with_cost`. **Vendor engine**: the old `moments` adapter submit/poll/clips code, snapped.
- **Router**: `engine_stats(owner, profile, engine, wins, losses)`; share = Laplace win rate normalised, floored at MIN_SHARE 0.10 (iterated); slots = max(1, round(pool * share)). `record` also logs a try. Labels (`moment_labels`: accept, reject, rescue, views_high, views_low; unique per candidate and kind) are synced from `reviews` and `metrics` by `learn` (read only) and each new label updates engine stats and hook-pattern stats once.
- **Learning**: per profile, once >= 30 labels, delta of feature means (positive minus negative, or positive minus all candidates when one class), `w = max(default + 0.5 * delta, 0.02)` normalised, blended with the defaults by `n / (n + 30)`; stored in `profile_weights`; before 30 the defaults apply and nothing is stored.
- **Hook patterns**: `hook_patterns` (name, language, profiles[], description, template, hook_type, active) + `hook_pattern_stats`. Weight = Laplace rate of approvals+high views vs rejections+low views, floored at 0.08, normalised per (language, profile); `pick` samples deterministically (seeded) without replacement. The LLM call is told which pattern to write each hook in; the fake renders the pattern's template; the hook is validated (no invented quote, length, injection) and rated for concreteness (R4) and payoff (paid off in the clip, else dropped).
- **Security**: transcript, brief terms and model output are data. Prompts are fixed per language and live in `vendors.LLM_SYSTEM`; the user message is JSON. Answers: JSON object only, known keys, ids must be the ids sent, numbers finite and in range, text stripped of control characters and capped, any string matching an instruction-injection pattern rejects the whole answer, all times come from our own windows (the model never supplies times).

## Prerequisites
- [x] Phases 1-3 outputs exist (`transcripts.load`, `signals.load`, `ContentFiltered`) - [x] numpy available (not required) - [x] migrations 009b-011 applied in test template

## Recommendation
BUILD
