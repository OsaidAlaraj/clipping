# Discovery + Design: Phase 6 - Quality gate, brand checks, end-to-end

## Files Found
- backend/clipping/review.py (feed, rank_feed, _decorate, CLIP_VIEW), agents/compliance.py (deterministic checks, run, overall),
  pipeline.run_compliance (the only caller of compliance.run), transcripts.py (pure `review_queue(words, key_terms, ranges)`,
  `queue_for(conn, source_id, limit, ranges)`), signals.py (`load`, `compute`, `text_signals`), demo.py, ranking.py
  (`account_stats`), api.py (`/api/feed`, `/api/clips/...`), migrations 001-013, web Feed.tsx / Queue.tsx / lib/i18n.tsx.
- New: brand_checks.py, 014_quality.sql, tests/test_14_quality.py, web/tests/quality.test.tsx.

## Current State
- Feed ranks by priority weights only; no per-clip quality. `account_stats` gives median/sigma/basis ("your posts"|"assumed").
- `review_queue` already takes `ranges` (boolean "inside a moment"); the API never passes moments.
- `signals.compute` already rebuilds only the text part on a newer transcript version, but `signals.load` (used by moments and
  render) returns the stale row (N2). `reaction` is not zeroed in silent seconds (N3).
- `python run.py demo` uses `.env` (USE_FAKES=0 and real keys), so today it runs against live vendors and ends with
  "the demo produced no clips" (observed: 1 job ran). With `CLIPPING_USE_FAKES=1` it passes (15 jobs, 3 clips). With
  USE_FAKES=0 and keys blanked it already completes on the fallbacks (11 jobs, 2 clips).

## Gaps (plan vs reality)
1. Produces says `transcripts.review_queue(conn, transcript_id, moments)`, but `review_queue(words, ...)` is the pure function
   that 14 anchored tests call. Resolution: one entry point, two call shapes (a list of words = pure ranking; a connection =
   the Produces shape). Reversible.
2. "Candidate" is a dict (start_s, end_s, score, ...) in this codebase; there is no class.
3. `python run.py demo` must force fakes (DW-6.3 "with fakes") and offer a no-keys mode; run.py is outside the stated file scope
   but DW-6.3 cannot be met without a two-line change there.
4. "Brand" has no column: it is `listings.brand` (via `campaigns.listing_id`), else the campaign name.

## Code Standards
AppError for refusals; thin API handlers via `run(actor, fn, ...)`; new tables RLS with the 012 policy block; audit rows carry
ids and counts only; adapters never imported from domain modules; heavy imports inside functions; no secrets.

## Test Infrastructure
pytest + embedded Postgres (`db`, `owner`, `produced`, `made`-style fixtures), fakes on; vitest + RTL with `mockApi`, `feedRoutes`.

## DW Verification

| DW-ID | Done-When Item | Status | Test Cases |
|-------|---------------|--------|------------|
| DW-6.1 | Clips under the quality threshold sort below the others and carry a label saying why | COVERED | test_DW_6_1_weak_clips_sort_below_and_say_why (db feed), pure-ordering test, label/basis tests, no-history assumed test; web: weak badge + Arabic label |
| DW-6.2 | A rejection recorded for brand X adds a check that flags the same issue on X's next clip | COVERED | test_DW_6_2_a_rejection_flags_the_same_issue_on_the_next_clip_of_the_brand; other brand unaffected; vague = reminder not block; API route test; web Queue form |
| DW-6.3 | `run.py demo` runs transcription -> signals -> moments -> render -> review with fakes; no keys completes on fallbacks | COVERED | test_DW_6_3_demo_runs_every_stage_with_fakes (demo.run on test db), kill-switch stop test, subprocess no-keys run (manual, throwaway dir) |
| DW-6.4 | Review and correction screens checked at 375 px and desktop | COVERED (component tests + stylesheet/no-overflow audit; live check by orchestrator) | web/tests/quality.test.tsx audit tests |
| DW-6.5 | Correction queue puts words inside likely clip moments first | COVERED | test_DW_6_5_words_inside_clip_moments_come_first (conn call shape, stored candidates), score-weighted order, API route |

**All items COVERED:** YES (5 of 5)

## Design Decisions
- **Chance of clearing** (`review.py`): per clip, `q` = mid-rank percentile of the clip's moment score among its source's
  stored candidates (0.5 when unknown); account stats from `ranking.account_stats` (cached per account in one feed call;
  no account -> assumed stats). Views ~ LogNormal(ln median + 0.35*sigma*z(q), sigma*sqrt(1-0.35^2)); `p_clear = P(views >= min)`.
  0.35 = share of a clip's spread the moment's quality explains (documented constant, easy to tune). No minimum on the
  campaign -> `p_clear = null`, never weak. Threshold = new setting `quality_min_p_clear` (0.25, operator scope).
  `quality = {p_clear, label, basis}` plus additive `weak`, `min_views`, `moment_percentile` so the web can build the Arabic text.
- **Order**: `rank_feed` sorts `(weak, -priority_score, created_at)`: weak clips sink, nothing is hidden.
- **Brand checks** (`brand_checks.py`, table `brand_checks`): `record_rejection` stores the marketplace's words (`quote`),
  interprets them deterministically (no LLM, works with no keys): a banned phrase ("do not say X", "no X allowed") -> kind
  `banned_phrase`, severity `block`; a required tag/hashtag/text ("missing #ad", "must tag @x") -> `require_text`, `warn`;
  anything else (vague) -> `reminder`, which never changes the status. `compliance.run(..., brand_rules)` appends one
  deterministic row per brand check (`brand_<kind>`), so the existing alert/edit paths show them. `overall` now treats a
  deterministic `review` as review. Duplicate (brand, kind, pattern) rows are not added twice.
- **Review queue**: `rank_words` weights each span by the best score of the moments it overlaps (1.0 when only ranges are
  given), so words inside the strongest moments come first; then key terms; then least sure. `queue_for` and the API read the
  source's kept candidates by default.
- **Signals**: `load` refreshes the text part (via `compute`, which reuses stored media) when the stored transcript version is
  older than the newest one and the file hash still matches; `reaction` is zeroed in silent seconds.
- **Demo**: `run.py demo` forces fakes unless `--no-keys` (fakes off, key variables blanked); the demo prints one line per
  stage with the provider actually used, and stops cleanly when the kill switch is on.
- **UI**: quality chip on the card (weak = amber badge with the reason), brand reminders/flags in the compliance sheet,
  "Marketplace rejected this clip" form in the Queue sheet (posts the reason; optimistic confirmation, Arabic labels).

## Prerequisites
- [x] Phases 2, 4, 5 outputs exist
- [x] Embedded Postgres + fakes available
- [x] 014 is the next migration

## Recommendation
BUILD
