# Plan: Phase D: making clips (transcription, moment engine, render)
**Created:** 2026-11-17
**Status:** ready
**Complexity:** complex
**Review cadence:** 2
**Check-ins:** checkpoints
---
## Context
**Problem:** Choosing moments depends on outside tools (Vizard, Klap), and the transcript is basic (no speakers, no campaign terms, no correction). Each clip is encoded twice, there is no originality layer and no cover. The system must pick the right moment and produce a review-ready clip with as little of the owner's work as possible.

**Success criteria:**
1. Deepgram Nova-3 transcripts with dialect code, speakers, utterances, word confidence and per-campaign key terms; local Whisper on the GPU as the real fallback.
2. One correction screen (phone and desktop) that is fast, flexible and smart. The owner's corrections automatically compare accuracy per provider and dialect.
3. Our own moment engine:
   - **Signals:** audio, video, transcript and YouTube "most replayed".
   - **Context:** the whole episode, and what the campaign wants.
   - **Ranking:** pairwise ranking in both orders, cut variants, and duplicates removed.
   - **Second opinion:** optional, from Vizard or Klap.
   - **Learning:** from the owner's decisions and from views.
4. One encode per clip (crop, captions, disclosure); the Arabic #ad label top right.
5. An originality layer with per-account templates, plus a cover per clip.
6. A quality gate keeps weak clips out of the top of the review feed.
7. Marketplace rejection reasons become checks for that brand's future clips.
8. All tests green; the app is checked live on phone and desktop sizes.

## Constraints
- **C1:** every external service goes through the adapter registry, and each has a fake for tests.
- **C2:** nothing is posted automatically, and every owner gate stays.
- **C3:** no secrets are stored or logged.
- **C4:** every stage has a declared fallback, so the pipeline works with no keys at all.
- **C5:** GTX 1660 Ti (6 GB) is the local GPU limit.
- **C6:** all files, caches and models live on drive L: (owner's rule).
- **C7:** older code touched on the way is brought up to `docs/code-standards.md`.
- **C8:** dialects: Deepgram detects them by default, and the owner can set one per video. Which dialects come first stays the owner's decision.
- **C9:** every new table gets RLS and policies in its migration (pattern: `007_discovery_fixes.sql`). Migrations are additive only.

## Chosen Approach
**C: combined engine.** Cheap measured signals pick about 12 candidates. An LLM then scores them with whole-episode and campaign context, and ranks them pairwise in both orders (Bradley-Terry). Weights are learned from the owner's decisions and views.

This is best with an Azure key and still works without one (the research behind it is cited in the Decision Log).

**Fallback:** with no LLM, the measured-signal ranking is used alone and the review feed says so.

## Rejected Approaches
- **A: LLM first:** costly on long episodes and dead without an Azure key, which may arrive late.
- **B: signals only:** blind to meaning; it picks loud but empty moments.

---
## Implementation Phases

### Phase 1: Transcription upgrade
**Skills:** code-foundations:aposd-designing-deep-modules, code-foundations:cc-defensive-programming
**Model:** sonnet
**Gate:** Standard
**Depends on:** none | **Unlocks:** Phase 2, Phase 3, Phase 4
**File scope:** backend/clipping/adapters/transcription.py (new: Deepgram and fake move here from vendors.py), backend/clipping/adapters/vendors.py, backend/clipping/transcripts.py (new), backend/clipping/pipeline.py, backend/migrations/009_*.sql, backend/tests/test_9_transcription.py, backend/requirements.txt
**Security-sensitive:** yes (audio goes to Deepgram; keys from env; subprocess calls)
**Rollback:** migration 009 only adds columns and tables (the existing `word_json_path` files stay readable). The old Deepgram call stays reachable as provider `deepgram_basic` until DW-1.1 to DW-1.4 pass.

**Goal:** Rich, versioned transcripts from Deepgram Nova-3, with the local faster-whisper as the declared fallback.

**Scope:**
- IN:
  - Deepgram: dialect code (`ar`, `ar-SA` and the others), diarize, utterances, word confidence, keyterms (campaign key terms first, cut to the top N);
  - the faster-whisper adapter (CUDA int8, CPU int8 fallback; the model under `.data/cache`);
  - transcript versions, speakers and confidence;
  - `stage_transcribe` rewritten to the code standards.
- OUT: the correction UI and moment logic.

**Edge cases:**
- No GPU or no CUDA DLLs → CPU, with an alert.
- Empty audio → AppError `empty_transcript`.
- A dialect code that does not fit the language → plain language code.
- Key terms over the limit → cut to the top N, with exactly N kept.

**Produces:**
- `transcripts.load(conn, source_id) -> {"id", "version", "provider", "dialect", "words":[{text,start,end,confidence,speaker}], "utterances":[{start,end,speaker,text}], "speakers":{id:name}}`;
- `transcripts.save_version(conn, actor, transcript_id, words, speakers, base_version) -> version`.

**Done when:**
- [ ] DW-1.1: Deepgram requests carry the dialect code, diarize, utterances and keyterms (checked on the fake's recorded request).
- [ ] DW-1.2: Deepgram failing three times falls back to faster-whisper (fake in tests). A real Whisper run on the 60 s fixture works on this laptop, with the model files under `L:\Projects\Clipping\.data\cache` and int8 memory use under 6 GB.
- [ ] DW-1.3: Transcripts keep speakers and confidence; `load` returns the latest version.
- [ ] DW-1.4: No key or token value appears in the database, audit log or job errors after a failed Deepgram call (test scans them).

**Difficulty:** MEDIUM
**Uncertainty:** whether CUDA DLLs for ctranslate2 load on Windows (CPU covers it).

### Phase 2: Correction screen and accuracy by provider and dialect
**Skills:** impeccable:impeccable, web-design-guidelines, code-foundations:cc-defensive-programming
**Model:** sonnet
**Gate:** Standard
**Depends on:** Phase 1 | **Unlocks:** Phase 6
**File scope:** backend/clipping/transcripts.py, backend/clipping/api.py, web/components/Transcript.tsx (new), web/app/transcript/** (new), web/components/More.tsx (Settings), web/tests/transcript.test.tsx (new), backend/tests/test_10_corrections.py
**Security-sensitive:** yes (write endpoints, owner scoping under RLS, free text from the owner)

**Goal:** One fast correction screen, the same on phone and desktop. Each correction saves a new version and updates the measured accuracy per provider and dialect.

**Scope:**
- IN:
  - **Review order:** a "fix what matters first" queue, ordered by low confidence and by key terms. Phase 6 adds weighting by clip moments.
  - **Editing:** tap or click a word to play from it; inline edit; find and replace; rename speakers; "add as key term for this campaign".
  - **Saving and undo:** edits apply on screen at once and save in the background, with undo; keyboard shortcuts on desktop.
  - **Accuracy:** WER per provider and dialect, computed from corrections and shown in Settings.
- OUT: editing clip start and end (stays in the existing review screen).

**Edge cases:**
- A stale version → 409 with the current version, and the screen merges or tells the owner.
- Arabic right-to-left mixed with English.
- A replace touching more than 100 words needs a confirm; exactly 100 does not.

**Produces:**
- `GET /api/transcripts/{id}` returns `load()`;
- `PUT /api/transcripts/{id}` with body `{"base_version":int, "edits":[{"op":"word"|"replace"|"speaker", ...}], "confirm_bulk":bool}` returns `{"version":int}`, or 409 `{"code":"stale","version":int}`;
- `GET /api/transcripts/{id}/review-queue` returns `[{word_index, text, confidence, reason}]`;
- `GET /api/transcripts/accuracy` returns `[{provider, dialect, wer, words}]`;
- `POST /api/campaigns/{id}/key-terms` with `{"term"}`.

**Done when:**
- [ ] DW-2.1: Editing a word, replacing a phrase and renaming a speaker each save a new version, and undo restores the previous one.
- [ ] DW-2.2: The review queue lists low-confidence words and key-term mismatches first.
- [ ] DW-2.3: Accuracy per provider and dialect updates from corrections and shows in Settings.
- [ ] DW-2.4: The screen works at 375 px and on desktop with no sideways scroll; keyboard shortcuts work on desktop; another owner's transcript returns 404.

**Difficulty:** MEDIUM
**Uncertainty:** None

### Phase 3: Signals
**Skills:** code-foundations:performance-optimization, code-foundations:aposd-designing-deep-modules
**Model:** sonnet
**Gate:** Standard
**Depends on:** Phase 1 | **Unlocks:** Phase 4
**File scope:** backend/clipping/signals.py (new), backend/clipping/ffmpeg.py, backend/clipping/sources.py, backend/clipping/adapters/heatmap.py (new), backend/fixtures/ytdlp/** (new), backend/migrations/010_*.sql, backend/tests/test_11_signals.py
**Security-sensitive:** yes (yt-dlp metadata is untrusted; FFmpeg parses uploaded media)
**Rollback:** migration 010 is additive; signals are optional inputs, so removing them returns ranking to text only.

**Goal:** Measure per-second signals for a source once and store them.

**Scope:**
- IN:
  - loudness (FFmpeg ebur128);
  - speech rate, pauses and speaker turns from the transcript;
  - scene changes (FFmpeg), and face presence (used for crop, cover and the score);
  - YouTube "most replayed" through yt-dlp metadata only, captured where links are handled (`sources.py`), when the brief permits;
  - topic segmentation from the transcript.
- OUT: scoring.

**Edge cases:**
- A 2-hour source must finish in about 3 minutes.
- No heatmap → the signal is absent (None), not zero.
- Silent or music-only stretches → the speech signals are zero there.
- A corrupted media file → AppError, job failed with an alert.

**Produces:** `signals.load(conn, source_id) -> {"step_s":1.0, "series":{"loudness":[float], "speech_rate":[float], "turns":[int], "scene":[float], "face":[float], "replay":[float]|None}, "topics":[{"start","end","title"}]}`.

**Done when:**
- [ ] DW-3.1: Signals for the 60 s fixture are stored once and reused (a second run does no FFmpeg work).
- [ ] DW-3.2: The heatmap is read from a saved yt-dlp metadata fixture, stored as `replay`, and None when missing; malformed metadata is ignored safely.
- [ ] DW-3.3: The 2-hour timing target holds on a synthetic long file, or the measured shortfall is reported.

**Difficulty:** MEDIUM
**Uncertainty:** FFmpeg face detection quality; a light model may follow later.

### Phase 4: Moment engine
**Skills:** code-foundations:aposd-designing-deep-modules, code-foundations:cc-pseudocode-programming, code-foundations:cc-control-flow-quality, code-foundations:aposd-verifying-correctness
**Model:** sonnet
**Gate:** Standard
**Depends on:** Phase 1, Phase 3 | **Unlocks:** Phase 5, Phase 6
**File scope:** backend/clipping/moments.py (new), backend/clipping/agents/moments_llm.py (new), backend/clipping/adapters/fake_llm.py, backend/clipping/adapters/vendors.py, backend/clipping/pipeline.py, backend/migrations/011_*.sql, backend/tests/test_12_moments.py
**Security-sensitive:** yes (LLM output parsing; prompt injection through transcript text or brief rules)
**Rollback:** `stage_moments` keeps the vendor path as a declared provider (`moments` stage `vizard`/`klap`/`fake`); migration 011 is additive.

**Goal:** Pick the best moments with signals, episode and campaign context, pairwise LLM ranking and learned weights.

**Scope:**
- **Candidates:** sentence-snapped windows of 15 to 60 s (campaign length rules win).
- **Signal score:** with weights.
- **Top 12 go to the LLM for:**
  - absolute scores (hook, self-contained, payoff, emotion), judged with the episode outline and the brief's wants and avoid list;
  - pairwise comparisons in both orders, aggregated with Bradley-Terry.
- **Cut variants:** `hook_first`, `with_setup` and `tight` per winner.
- **Duplicates:** removed by overlap and by topic.
- **Vendor second opinion:** Vizard or Klap, as extra candidates when active.
- **Learning:** the weights refit from approvals, rejections and 48 h views once there are 30 labelled clips. The labels are read (not changed) from the `reviews` and `metrics` tables.
- **Fallback:** signals only.
- OUT: rendering.

**Edge cases:**
- Exactly 3 candidates, fewer than 3, and windows at 15 s and 60 s.
- The LLM fails partway → keep the scores done so far, finish on signals.
- Contradictory pairwise answers → Bradley-Terry, with ties broken by signal score.
- Every candidate on the avoid list → none, plus an alert.
- Model output that is not JSON, or that tries to change instructions → rejected and treated as no answer.

**Produces:**
- `Candidate = {"start_s":float, "end_s":float, "variant":"hook_first"|"with_setup"|"tight", "score":float, "rank":int, "reasons":[str], "features":{name:float}, "source":"engine"|"vizard"|"klap"}`;
- `moments.find(conn, source_id, campaign_version) -> list[Candidate]`, stored in `candidates` (columns added: variant, features, reasons, origin);
- `moments.learn(conn, owner_id) -> {"weights":{...}, "labelled":int}`.

**Done when:**
- [ ] DW-4.1: On a fixture episode the engine returns ranked, sentence-snapped, de-duplicated moments with reasons, with or without the LLM.
- [ ] DW-4.2: Each pair is asked in both orders, and swapping the order does not change the final rank in the fake.
- [ ] DW-4.3: Synthetic approvals, rejections and 48 h views change the weights after 30 labels and not before.
- [ ] DW-4.4: An avoid-list hit removes the candidate, and a must-include hit raises it.
- [ ] DW-4.5: Each winner has three cut variants. When a vendor adapter is active, its picks join as candidates marked with their origin.

**Difficulty:** HIGH
**Uncertainty:** the real quality depends on the Azure key; measured only after it arrives.

### Phase 5: One encode, originality layer, covers
**Skills:** code-foundations:cc-refactoring-guidance, code-foundations:performance-optimization, impeccable:impeccable
**Model:** sonnet
**Gate:** Standard
**Depends on:** Phase 1, Phase 2, Phase 3, Phase 4 (Phase 2 listed so the shared api.py edits stay in order) | **Unlocks:** Phase 6
**File scope:** backend/clipping/adapters/media.py, backend/clipping/ffmpeg.py, backend/clipping/templates.py (new), backend/clipping/pipeline.py, backend/clipping/api.py, backend/migrations/012_*.sql, backend/tests/test_13_render.py, web/components/Places.tsx, web/tests/round2.test.tsx
**Security-sensitive:** yes (hook and template text go into ASS and FFmpeg filter arguments)
**Rollback:** the `captions` stage stays registered as a no-op passthrough for jobs already queued; migration 012 is additive.

**Goal:** Render each clip in one FFmpeg encode with the account's template, and make a cover.

**Scope:**
- IN:
  - one encode: crop or reframe, captions (ASS), disclosure overlay;
  - the originality layer: the account template's caption style, title card, progress bar and emphasis zooms;
  - the Arabic #ad label top right;
  - per-account templates editable on Profile;
  - a cover: a sharp face frame plus the hook text;
  - NVENC when free, x264 otherwise;
  - the old render and captions stages folded into one.
- OUT: new caption languages.

**Edge cases:**
- RTL Arabic with English words.
- Very long Arabic hook text → wrapped and shrunk.
- No face → centre crop.
- NVENC busy or missing → x264.
- A template missing fields → defaults.
- Template text with ASS or filter control characters → escaped.

**Produces:**
- `media.render_clip(src, clip, words, template, label, out) -> {"encodes":1, "encoder", "cover_path"}`;
- `GET` and `PUT /api/accounts/{id}/template` with `{"font", "primary", "accent", "caption_position":"lower"|"middle", "title_card":bool, "progress_bar":bool, "zooms":bool}`;
- `clips.render_meta{template_id, encoder, encodes:1}` and `clips.cover_path`.

**Done when:**
- [ ] DW-5.1: A clip is produced with exactly one encode (asserted from the FFmpeg calls), with captions and the disclosure burned in.
- [ ] DW-5.2: The Arabic disclosure sits top right; English stays where it was.
- [ ] DW-5.3: Two accounts with different templates give measurably different frames.
- [ ] DW-5.4: Each clip has a cover PNG with the hook text.
- [ ] DW-5.5: The owner edits an account's template on Profile and the next clip uses it.

**Difficulty:** MEDIUM
**Uncertainty:** None

### Phase 6: Quality gate, brand checks, end-to-end
**Skills:** code-foundations:cc-defensive-programming, code-foundations:aposd-verifying-correctness, impeccable:impeccable
**Model:** sonnet
**Gate:** Standard
**Depends on:** Phase 2, Phase 4, Phase 5 | **Unlocks:** none
**File scope:** backend/clipping/review.py, backend/clipping/agents/compliance.py, backend/clipping/brand_checks.py (new), backend/clipping/transcripts.py, backend/clipping/demo.py, backend/clipping/api.py, backend/migrations/013_*.sql, backend/tests/test_14_quality.py, web/app/review/**, web/components/Feed.tsx, web/components/Queue.tsx, web/tests/**, docs/**
**Security-sensitive:** yes (free-text rejection reasons become checks)
**Rollback:** brand checks only add warnings or blocks to future clips; disabling them is a settings change. Migration 013 is additive.

**Goal:** Weak clips stay out of the top of review; rejection reasons protect future clips; the whole flow runs end to end, including with no keys.

**Scope:**
- IN:
  - **Chance of clearing the minimum:** worked out per clip (moment score percentile plus the account's view stats), then used to order and badge the review feed.
  - **Correction queue:** weighted by clip moments.
  - **Brand checks:** marketplace rejection reasons recorded per brand and turned into extra compliance checks.
  - **Demo and docs:** the demo, the docs and the open items updated.
  - **Live check:** on phone and desktop.
- OUT: posting (phase E).

**Edge cases:**
- No view history → assumed stats, labelled.
- A rejection reason too vague to check → kept as a reminder, not a block.
- Kill switch on during the demo → the demo stops cleanly.

**Produces:**
- `GET /api/feed` items gain `quality: {"p_clear": float|null, "label": str, "basis": "your posts"|"assumed"}`;
- `brand_checks` rows `{brand, rule, quote, severity}`, applied by `compliance.run`;
- `POST /api/clips/{id}/rejection` with `{"reason"}`;
- `transcripts.review_queue(conn, transcript_id, moments: list[Candidate] | None = None) -> [{word_index, text, confidence, reason}]`, which reads the Phase 4 `candidates` rows for the source.

**Done when:**
- [ ] DW-6.1: Clips under the quality threshold sort below the others and carry a label saying why.
- [ ] DW-6.2: A rejection recorded for brand X adds a check that flags the same issue on X's next clip.
- [ ] DW-6.3: `python run.py demo` runs transcription → signals → moments → one-encode render → review with fakes. With `CLIPPING_USE_FAKES=0` and no keys, the same flow completes on the declared fallbacks (local Whisper, signals-only ranking).
- [ ] DW-6.4: The review and correction screens are checked live at 375 px and on desktop.
- [ ] DW-6.5: The correction queue puts words inside likely clip moments first.

**Difficulty:** MEDIUM
**Uncertainty:** None

---
## Test Coverage
**Level:** 100%: a unit test for every new or changed routine in the phase's implementation files, plus the integration and manual items below.

## Test Plan
- [ ] Per-DW tests: DW-1.1 to DW-6.5, one behavioural test each.
- [ ] Dirty, phase 1:
  - empty audio;
  - Deepgram returns no words;
  - a dialect code that does not match the language;
  - no GPU (CPU path);
  - the key-term list over the limit;
  - the secrets scan after a failure.
- [ ] Dirty, phase 2:
  - a stale version (409);
  - a replace of 101 words without confirm;
  - Arabic and English mixed;
  - another owner's transcript.
- [ ] Dirty, phase 3: a silent or music-only source; no heatmap; malformed yt-dlp metadata; a corrupted media file.
- [ ] Dirty, phase 4:
  - the LLM fails mid-tournament;
  - pairwise answers that contradict each other;
  - fewer than 3 candidates;
  - every candidate on the avoid list;
  - non-JSON or instruction-injecting model output.
- [ ] Dirty, phase 5:
  - no face;
  - NVENC missing or busy;
  - a template missing fields;
  - very long Arabic hook;
  - RTL mixed with English;
  - control characters in template text;
  - `PUT /api/accounts/{id}/template` with a bad `caption_position` or an oversized font → 400.
- [ ] Dirty, phase 6: no account stats; a vague rejection reason; the kill switch on during the demo.
- [ ] Boundary:
  - exactly 3 candidates;
  - 15 s and 60 s windows;
  - exactly 100 and 101 words in a replace;
  - exactly N key terms;
  - 29 and 30 labels for learning.
- [ ] Integration: a fixture episode through every stage to a review item, with fakes and with no keys.
- [ ] Manual:
  - the correction screen and the review feed on the phone and desktop sizes;
  - one real Whisper run on the GPU;
  - a real Deepgram run once the key exists.

## Assumptions
| Assumption | Confidence | Verify Before Phase | Fallback If Wrong |
|---|---|---|---|
| ctranslate2 CUDA works on the GTX 1660 Ti under Windows | MED | 1 | CPU int8 (slower) |
| Deepgram keyterms accepted for the Arabic codes | HIGH | 1 | send without keyterms |
| yt-dlp `heatmap` present for popular videos | HIGH | 3 | the signal is absent |
| The Azure key arrives later | MED | 4 | signals-only ranking |

## Decision Log
| Decision | Alternatives Considered | Rationale | Phase |
|---|---|---|---|
| Combined engine (C) | LLM first, signals only | best quality with the key, still works without it | 4 |
| Both-order pairwise + Bradley-Terry, keep absolute scores | pointwise only | pairwise wins best-of-N; position bias and distractors handled | 4 |
| Whisper inside the app (faster-whisper) | whisper.cpp exe, a second venv | wheels exist for Python 3.14; one process | 1 |
| Heatmap via metadata only | scraping the watch page | yt-dlp exposes `heatmap` without a download | 3 |
| Correction queue by confidence now, by moments in phase 6 | move phase 2 after phase 4 | corrections early improve moment choice | 2, 6 |
| Laughter and applause cues left out | include | no success criterion needs them yet | 3 |
| Skeleton and confirm merged into one check-in | two separate stops | the owner asked to keep going | all |

Research cited for this plan:
- Rhapsody (2025): podcast highlights benefit from audio plus transcript, and YouTube "most replayed" as labels.
- TF-SELECTOR (2026): segment-based LLM scoring with transcripts and audio volume works on long video.
- PRP (2024), Best-of-N (2026), Coin Flip Judge (2026), Pairwise or Pointwise (2025): pairwise ranking wins for picking the best, but has position bias and is vulnerable to distractors.

---
## Notes
- The correction screen must feel instant:
  - edits apply locally first and save in the background;
  - the queue shows only what matters;
  - nothing blocks while saving.
- Whisper and model downloads go to `L:\Projects\Clipping\.data\cache` (HF_HOME).
- Each phase report names the older code it brought up to the standards (C7).
---
## Decided During Build
| # | Phase | Decision or question | Source | Status |
---
## Execution Log
_To be filled during /code-foundations:build_
