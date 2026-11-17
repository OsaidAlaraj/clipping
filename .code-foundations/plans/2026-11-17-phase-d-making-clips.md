# Plan: Phase D: making clips (transcription, moment engine, render)
**Created:** 2026-11-17
**Status:** in-progress
**Started:** 2026-11-17 19:40
**Current Phase:** 2P
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

## Research rules (peer-reviewed, 2026-11-17)
Each rule is a default the learning loop may adjust per profile once real results exist. Phases 4 and 5 implement them.
- R1 Emotion: high-arousal emotions (awe, amusement, excitement, surprise, being moved) score above low-arousal ones
  (sadness, calm information); discrete emotions are scored separately, not just positive vs negative (Berger &
  Milkman 2011; Nelson-Field 2013; Paletz 2023). Anger/outrage is not boosted for brand campaigns (brand safety).
- R2 Brand and information: purely informational moments score lower unless the brief asks for information; for brand
  campaigns the brand is not forced into the first seconds unless the brief requires it (Tellis 2019).
- R3 First seconds and length: most viewers skip early and length is the strongest predictor of finishing, so the
  `tight` variant is the default when it keeps the payoff, and the hook must land in the first 3 s (Zhao 2025; Vieira 2026).
- R4 Hook wording: hooks with "just the right amount" of information beat both vague teasers and spoilers; every hook
  is rated on a concreteness scale and must be paid off inside the clip (Aubin Le Quere 2025; Qiu 2024).
- R5 Laughter: real laughter makes moments funnier to viewers, so cuts end after the laugh, not on the punchline, and
  moments with genuine (group) laughter rank higher (Cai 2019, 2026; Cacciatore 2020).
- R6 Captions: social-style captions (light punctuation, selective emoji, key words emphasised) for TikTok/Reels,
  always accurate (Duraj 2025; Gernsbacher 2015).
- R7 Covers: an expressive face frame plus a short positive hook line; strong sentiment belongs in the image, not in
  long cover text (Cui 2024; Oh 2025).
- R8 Fit beats formula: audience-by-content fit explains most of what lands, so weights are learned per profile and per
  account audience, never one global formula (Rosenbusch 2022).

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

### Phase 2P: Pilot slice (owner priority, 2026-11-17)
**Skills:** code-foundations:aposd-designing-deep-modules, code-foundations:cc-defensive-programming, impeccable:impeccable
**Model:** sonnet
**Gate:** Standard
**Depends on:** Phase 1, Phase 2 | **Unlocks:** Phase 3
**File scope:** backend/clipping/campaigns.py, backend/clipping/pipeline.py, backend/clipping/publish.py, backend/clipping/ledger.py, backend/clipping/moments_pilot.py (new), backend/clipping/adapters/vendors.py, backend/clipping/api.py, backend/migrations/010b_pilot.sql, backend/tests/test_10b_pilot.py, web/components/**, web/app/**, web/tests/**
**Security-sensitive:** yes (LLM output parsing; uploads)

**Goal:** The owner can run a no-money, no-posting pilot from the phone as soon as possible: upload a video to a practice
campaign and get real, reviewable clips.

**Scope:**
- IN: practice campaigns (one tap, no marketplace, clearly labelled; posting, scheduling, payouts and ledger entries
  are refused for them in code and in the UI); real transcription (phase 1); an interim LLM moment picker
  (gpt-5-mini on the transcript, sentence-snapped, profile length range, reasons; the full engine in phase 4 replaces
  it behind the same interface; the fake stays for tests); render, captions, disclosure and cover as they exist;
  review in the existing feed; a run on the three sample videos in `.data/samples` (English podcast, Arabic interview,
  Arabic lecture) with a short report of what broke and what was fixed.
- OUT: the full moment engine, signals, templates (later phases).

**Edge cases:** an upload larger than the phone allows (clear message, resumable where possible); an LLM answer that is
not valid JSON or points outside the video; a practice campaign asked to post (refused); Arabic and English captions.

**Produces:** `campaigns.create_practice(conn, actor, name, niche, language) -> campaign`; `moments_pilot.propose(conn,
source, version, profile_hint) -> list[Candidate]` with the phase 4 Candidate shape.

**Done when:**
- [ ] DW-2P.1: A practice campaign is created in one step, runs the whole pipeline, and any attempt to post, schedule or
  record a payout for it is refused by the API.
- [ ] DW-2P.2: With the LLM active, moments come from the transcript (sentence-snapped, inside the length range, with
  reasons); bad LLM output falls back to the existing fallback with an alert.
- [ ] DW-2P.3: The three sample videos go through end to end on this laptop with real transcription, and each yields
  reviewable clips with captions, disclosure and a cover; the report lists timings and cost.

### Phase 3: Signals
**Skills:** code-foundations:performance-optimization, code-foundations:aposd-designing-deep-modules
**Model:** sonnet
**Gate:** Standard
**Depends on:** Phase 1, Phase 2P | **Unlocks:** Phase 4
**File scope:** backend/clipping/signals.py (new), backend/clipping/ffmpeg.py, backend/clipping/sources.py, backend/clipping/adapters/heatmap.py (new), backend/fixtures/ytdlp/** (new), backend/migrations/010_*.sql, backend/tests/test_11_signals.py
**Security-sensitive:** yes (yt-dlp metadata is untrusted; FFmpeg parses uploaded media)
**Rollback:** migration 010 is additive; signals are optional inputs, so removing them returns ranking to text only.

**Goal:** Measure per-second signals for a source once and store them.

**Scope:**
- IN:
  - loudness (FFmpeg ebur128);
  - speech rate, pauses and speaker turns from the transcript;
  - scene changes (FFmpeg), and face presence (used for crop, cover and the score);
  - laughter and applause from a small local audio-tagging model (sherpa-onnx), with a loudness-burst
    heuristic as the fallback when the model is not installed;
  - reaction words per language (English and Arabic lists, e.g. "no way", "والله", "مستحيل") and fast talk;
  - stream chat replay when available (YouTube live chat through yt-dlp), as chat-burst counts per second;
  - YouTube "most replayed" through yt-dlp metadata only, captured where links are handled (`sources.py`), when the brief permits;
  - topic segmentation from the transcript.
- OUT: scoring.

**Edge cases:**
- A 2-hour source must finish in about 3 minutes.
- No heatmap → the signal is absent (None), not zero.
- Silent or music-only stretches → the speech signals are zero there.
- A corrupted media file → AppError, job failed with an alert.

**Produces:** `signals.load(conn, source_id) -> {"step_s":1.0, "series":{"loudness":[float], "speech_rate":[float], "turns":[int], "scene":[float], "face":[float], "laughter":[float], "reaction":[float], "chat":[float]|None, "replay":[float]|None}, "topics":[{"start","end","title"}]}`.

**Done when:**
- [ ] DW-3.1: Signals for the 60 s fixture are stored once and reused (a second run does no FFmpeg work).
- [ ] DW-3.2: The heatmap is read from a saved yt-dlp metadata fixture, stored as `replay`, and None when missing; malformed metadata is ignored safely.
- [ ] DW-3.3: The 2-hour timing target holds on a synthetic long file, or the measured shortfall is reported.
- [ ] DW-3.4: Laughter, reaction words and chat bursts are detected on fixtures (a laugh clip, an Arabic and an English
  reaction line, a saved chat replay), and each falls back to "absent" when its input or model is missing.

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

**Goal:** Pick the best moments with a workflow per content type and a strong but forgiving filter: signals,
episode and campaign context, pairwise LLM ranking and learned weights.

**Scope:**
- **Workflows by content type** (owner, 2026-11-17: "podcast is completely different from lifestyle"), one profile each
  for podcast/interview, streams/gaming, sports, film & TV, music, brand ads/UGC/promo and lifestyle/vlog/behind the
  scenes. Each profile sets: clip count and length (e.g. podcast 6 x 30-60 s, stream 5 x 20-45 s, promo 4 x 15-30 s),
  signal weights, what counts as a moment (podcast: standalone opinions, surprising facts, stories with a payoff,
  confessions, banter; stream: clutch plays, fails, loud reactions, chat explosions; promo: main benefit, demo, strong
  claim, testimonial in hook -> proof -> CTA; lifestyle: transformations, reveals, relatable moments), the crop style
  (follow speaker / facecam over game / centre), audio (speech-tuned / natural) and edit style (dead air out / quiet
  parts kept). Picked from the campaign's niche, changeable per video. Profiles live in data, not code, so new ones
  need no release.
- **Map -> pool -> rank** for long episodes: chunks cut at long pauses with 20% overlap so nothing is lost in the middle;
  standalone moments are collected per chunk, then pooled and ranked together.
- **Candidates:** sentence-snapped windows inside the profile's length range (campaign length rules win), with start and
  end allowed to move a few seconds to include the setup or the payoff.
- **Signal score:** with weights.
- **A three-layer filter, strong but never dumb:**
  1. Hard gates, only for real deal-breakers: the brief's avoid list, length outside the campaign rules, starts
     mid-sentence or leans on earlier context ("as I said"), the same moment already published (podcli), silence or
     broken audio.
  2. Soft score per profile with auditable subscores (hook in the first 3 s, standalone, payoff, emotion, value,
     visual energy, signal peaks) and the evidence behind each. Cross-modal corroboration (publikclip): an LLM "funny"
     with no laughter in the audio, or "hype" with no loudness or chat burst, is discounted.
  3. Top candidates go to the LLM for pairwise comparisons in both orders, aggregated with Bradley-Terry.
  The threshold is relative to the episode (top share by percentile, with a floor), so a quiet episode still yields its
  best few moments and a strong one is not capped too early; every profile keeps a minimum clip count unless a hard
  gate removed everything.
- **Nothing is hidden silently:** filtered moments keep their reason and show under "Filtered" in review; the owner
  can rescue one, and rescues and rejections teach the profile.
- **Cut variants:** `hook_first`, `with_setup` and `tight` per winner.
- **Duplicates:** removed by overlap and by topic.
- **Moment router (owner, 2026-11-17: "a gateway that picks the best one, with defined routes, not one fixed thing"):**
  moment finding is a set of interchangeable engines behind one interface, and a router picks the route per content
  type. Engines: our own engine (the default), a topic-segmentation engine (TextTiling, the approach ClipsAI uses), a
  hype-map engine for streams and sports (loudness, laughter, reaction words, chat, as in sharkyclips), and the vendor
  APIs (Vizard, Klap) when their keys exist. Each profile names its route (which engines run and how their candidates
  are merged); all candidates go through the same three-layer filter, so engines only propose and never decide. The
  router keeps per-profile win rates (which engine's picks the owner approves and which earn views) and shifts weight
  toward the winners over time, while still trying the others now and then (a simple bandit), so no single engine
  becomes the fixed answer. Code is borrowed from other repositories only under MIT, BSD or Apache licences, with the
  licence and source recorded; anything GPL or AGPL is re-implemented from the idea, not copied.
- **Hook pattern library (owner, 2026-11-17: "hooks and accounts should keep up, not just cut right"):** hooks are
  written in named patterns (question, shocking number, "nobody tells you...", bold opinion, story opening, ...) per
  language and profile; each pattern tracks how often it was used, approved and how its clips did at 48 h; winners
  are used more and weak ones less, never to zero, so new patterns keep being tried. Patterns are data, so new ones
  are added without a release.
- **Research rules R1 to R5 and R8** (see "Research rules") are the profile defaults for emotion, information, length,
  hook wording and laughter.
- **Learning:** each profile's weights refit from approvals, rejections, rescues and 48 h views once it has 30 labelled
  clips (before that, the profile's defaults). The labels are read (not changed) from the `reviews` and `metrics` tables.
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
- `moments.find(conn, source_id, campaign_version, profile=None) -> {"kept": list[Candidate], "filtered": list[{"candidate": Candidate, "reason": str, "layer": 1|2|3}]}`, stored in `candidates` (columns added: variant, features, reasons, origin, profile, filtered_reason);
- `profiles.get(conn, niche_or_name) -> Profile` and a `content_profiles` table seeded with the seven profiles;
- `router.route(conn, profile) -> [engine names]` and `router.record(conn, profile, engine, outcome)`; engines implement
  `propose(source_ctx, profile) -> list[Candidate]` and register like adapters (fakes for tests);
- `moments.learn(conn, owner_id) -> {"weights":{...}, "labelled":int}`.

**Done when:**
- [ ] DW-4.1: On a fixture episode the engine returns ranked, sentence-snapped, de-duplicated moments with reasons, with or without the LLM.
- [ ] DW-4.2: Each pair is asked in both orders, and swapping the order does not change the final rank in the fake.
- [ ] DW-4.3: Synthetic approvals, rejections and 48 h views change the weights after 30 labels and not before.
- [ ] DW-4.4: An avoid-list hit removes the candidate, and a must-include hit raises it.
- [ ] DW-4.5: Each winner has three cut variants. When a vendor adapter is active, its picks join as candidates marked with their origin.
- [ ] DW-4.6: The same fixture episode run under the podcast and the stream profile gives different picks, lengths and
  crop styles, as the profiles define.
- [ ] DW-4.9: Hooks carry their pattern; recorded approvals and 48 h views shift pattern weights per language and
  profile, and every pattern keeps a minimum share; a new pattern added as data is used without a code change.
- [ ] DW-4.8: Two profiles take different routes; candidates from every engine on the route pass through the same
  filter and carry their engine as origin; recorded wins shift a profile's engine weights while every engine keeps a
  minimum share of tries.
- [ ] DW-4.7: A quiet fixture episode still yields its profile's minimum number of clips; a hard-gate hit is filtered with
  its reason; a rescued filtered moment is stored as a label for learning; an LLM "funny" with no laughter scores lower
  than the same moment with laughter.

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
  - a living creative library (owner, 2026-11-17): templates (caption style, fonts and colours, title card, progress
    bar, zooms, cover style) are versioned data that can be added or edited without a release; each account rotates
    through its own set so clips never look identical (Instagram demotes repetitive aggregators), while keeping a
    recognisable look;
  - an account persona per account: its look, suggested posts per week and best posting hours (from its own results
    once they exist), shown on Profile;
  - a cover: a sharp, expressive face frame plus a short hook line (rule R7);
  - caption style per platform (rule R6);
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
- [ ] DW-5.6: Templates are versioned data: a new template added through the API is used by the next render with no
  code change; an account with several templates rotates them across consecutive clips; reverting a template
  version restores the previous look.

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

### Phase 7: Social-media smoothness
**Skills:** impeccable:impeccable, web-design-guidelines, code-foundations:performance-optimization
**Model:** sonnet
**Gate:** Standard
**Depends on:** Phase 6 | **Unlocks:** none
**File scope:** web/**, backend/clipping/api.py
**Security-sensitive:** no

**Goal:** The whole app feels as smooth as a social-media app on the phone and the laptop (owner, 2026-11-17: "very,
very smooth, like social media sites").

**Scope:**
- IN: instant tab and screen changes (prefetch and cached data, no blank screens: skeletons that match the layout);
  optimistic actions with undo (approve, reject, save, dismiss) that never wait on the network; the review feed preloads
  the next clips and swipes at 60 fps; images and covers sized and lazy; no layout shift; fewer and smaller API calls
  (batched endpoints where screens need several); offline-tolerant retries with a quiet "saved" state.
- OUT: new features.

**Edge cases:** slow 4G (throttled), a failed optimistic action (rolls back with a clear note), a very long feed.

**Produces:** no new interfaces; measured numbers in the phase report.

**Done when:**
- [ ] DW-7.1: On the phone size with 4G throttling, every main screen shows content or a matching skeleton within
  200 ms of a tap, and interaction to next paint stays under 200 ms (measured in the browser).
- [ ] DW-7.2: Approve, reject, save and dismiss update the screen at once and roll back cleanly when the server refuses.
- [ ] DW-7.3: The review feed plays the next clip with no visible load after a swipe, and layout shift stays under 0.05.

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
- [ ] Dirty, phase 4 workflows: an unknown niche falls back to a general profile; a profile with missing fields uses
  defaults; every candidate filtered by hard gates returns an empty kept list with reasons and an alert.
- [ ] Dirty, phase 7: an optimistic approve refused by the server rolls back; the feed on throttled 4G.
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
| Content-type workflows as data profiles | one engine for all | owner: podcast and lifestyle differ completely; clippy shows per-type presets work | 4 |
| Three-layer filter with relative threshold and rescue | fixed score cut-off | strong but not dumb: hard gates only for deal-breakers, nothing hidden silently | 4 |
| Social-media smoothness as its own phase | fold into phase 6 | owner asked for it explicitly; it touches every screen | 7 |
| Creative library and hook patterns as versioned, learned data | fixed templates and prompts | owner wants accounts and hooks to keep up; no release needed for new looks | 4, 5, F |
| Pilot slice before signals and the full engine | finish all phases first | owner wants to test with real videos as soon as possible, without money or posting | 2P |
| Moment router over several engines with learned per-profile routes | one fixed engine; one merged mega-repo | owner asked for routes, not one fixed thing; engines stay swappable; licences respected | 4 |

Research cited for this plan:
- Open-source clippers read on 2026-11-17: clippy (per-type presets), publikclip (auditable scores, laughter
  corroboration), reelsaw (map -> pool -> rank with overlap, decision log), sharkyclips (hype map: loudness, laughter,
  reaction words, chat), podcli (no re-suggesting published moments), video-podcast-clipper (8-dimension rubric,
  adaptive threshold).
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
- Phase F (learning) carries the weekly creative refresh: re-rank templates, hook patterns and posting hours from real
  results, with a short "what changed and why" report and a one-tap revert; trends from allowed sources only (YouTube
  Data API, ClippingAlpha market data, the owner's own results, trends the owner adds by hand; no scraping where terms
  forbid it).
- Smoothness is the owner's top priority (2026-11-17: "most important, a smooooth system"). Every UI phase (2, 5, 6)
  follows phase 7's bar from the start: instant feedback, no blank screens, optimistic actions, no layout shift;
  phase 7 then measures and tunes the whole app.
---
## Decided During Build
| # | Phase | Decision or question | Source | Status |
|---|---|---|---|---|
| 1 | 1 | Whisper gets the decoded 16 kHz wav as an array (PyAV 19 breaks faster-whisper 1.2.1 path decoding) | build agent | assumed |
| 2 | 1 | No pin on `av`; the array path avoids the PyAV issue | build agent | assumed |
| 3 | 1 | DW-1.4 scans every table and the job error (a failed job rolls back its own audit rows, existing behaviour) | build agent | assumed |
| 4 | 1 | Migration 009b_transcripts.sql: campaigns.key_terms, transcripts.current_version and language_code, transcript_versions (RLS), providers faster_whisper and deepgram_basic, Deepgram falls back to faster_whisper | build agent | assumed |
| 5 | 1 | Phase 2 writes campaign key terms to campaigns.key_terms | build agent | assumed |
| 6 | 1 | Local Whisper model: large-v3-turbo int8 on CUDA (peak 1089 MiB of 6144) | build agent | assumed |
| 7 | 2 | Transcript routes take the SOURCE id; load()["id"] stays the transcript id | build agent | assumed |
| 8 | 2 | Undo is an extra edit op {"op":"revert","to_version":n} over append-only versions | build agent | assumed |
| 9 | 2 | 409 stale body {"error","code":"stale","version"} via an optional extra dict on the error handler | build agent | assumed |
| 10 | 2 | Migration 010 keeps running WER counts on transcripts (wer_errors, wer_words vs version 1), no new table | build agent | assumed |
| 11 | 2 | GET transcript adds source_id, campaign_id, campaign_name, language, media_url, key_terms; CLIP_VIEW gains source_id | build agent | assumed |
---
## Execution Log
_To be filled during /code-foundations:build_

### Phase 1: Transcription upgrade (Gate: Standard, security-sensitive)
- [x] BUILD: Discovery + design + implementation (stub → implement → validate) complete
- [x] REVIEW: Verification passed
- [x] Committed
Commit: (see git log, phase 1/7)
Summary: Transcripts now come from Deepgram Nova-3 with dialect code, speakers, utterances, word confidence and campaign key terms (cut to 50), versioned in transcript_versions via transcripts.load/save_version; local faster-whisper large-v3-turbo int8 on CUDA (CPU fallback with alert) is the declared fallback, and no key reaches the database or job errors.

### Phase 2: Correction screen and accuracy by provider and dialect (Gate: Standard, security-sensitive)
- [x] BUILD: Discovery + design + implementation (stub → implement → validate) complete
- [x] REVIEW: Verification passed
- [x] Committed
Commit: (see git log, phase 2)
Summary: A correction screen at /transcript (reached from the campaign page and Review) shows key-term mismatches and low-confidence words first, applies edits instantly with background save, undo (revert op) and stale-merge (409), handles Arabic RTL with English, and keeps running WER per provider and dialect shown in Settings; routes take the source id.

