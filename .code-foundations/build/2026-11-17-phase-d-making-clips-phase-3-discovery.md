# Discovery + Design: Phase 3 - Signals

## Files Found
- backend/clipping/ffmpeg.py (probe, silences, make_fixture), sources.py (link handling, `download_youtube`, `stage_fetch_source`), pipeline.py (`stage_transcribe` -> `moments`), transcripts.py (`load`, `normalise`), adapters/transcription.py (pattern for adapters and fakes), migrations 009b (RLS pattern for owner tables).
- New: clipping/signals.py, adapters/heatmap.py, adapters/audiotag.py, migrations/011_signals.sql, fixtures/ytdlp/*, tests/test_11_signals.py.

## Current State
No signals exist. `stage_transcribe` enqueues `moments` directly. `download_youtube` discarded yt-dlp's metadata. numpy 2.5 and onnxruntime are present (faster-whisper dependencies); OpenCV, yt-dlp and sherpa-onnx are not.

## Gaps (plan vs reality)
- Plan says migration `010_*`; 010 and 010b are taken, the brief says 011_signals.sql (used).
- sherpa-onnx has no wheel for Python 3.14 (`pip install` fails) -> optional, not in requirements; the loudness-burst heuristic is the tested path. The wrapper is written against the sherpa-onnx audio-tagging API and tested with a stub module; it has not run against the real library.
- yt-dlp is not installed (already optional in sources.py); the yt-dlp path is tested with a stub and saved metadata samples.
- No real laugh recording is available licence-free; the laugh clip is synthetic (pulsed noise after a quiet tone).
- Fixture `footage60.mp4` in backend/fixtures is 20 s; the 60 s fixture is the generated `footage` pytest fixture (used).

## Code Standards
Registry for external services (yt-dlp metadata is read from the object yt-dlp already returns; no new vendor call, so no registry row); numpy imported inside functions; AppError for expected refusals; migrations additive with RLS in the same file; tests named as sentences with DW-ids.

## Test Infrastructure
pytest with embedded Postgres per test (`db`, `owner`, `footage` fixtures), real FFmpeg, fakes for vendors.

## DW Verification

| DW-ID | Done-When Item | Status | Test Cases |
|-------|---------------|--------|------------|
| DW-3.1 | Signals for the 60 s fixture stored once and reused (a second run does no FFmpeg work) | COVERED | test_DW_3_1_signals_are_stored_after_transcription_and_before_moments, test_DW_3_1_a_second_run_does_no_ffmpeg_work |
| DW-3.2 | Heatmap from saved yt-dlp metadata, stored as `replay`, None when missing; malformed ignored | COVERED | test_DW_3_2_the_heatmap_is_read_from_a_saved_ytdlp_metadata_sample, ..._no_heatmap_is_none_not_zero, ..._malformed_metadata_is_ignored_safely (17 cases), ..._a_stored_heatmap_becomes_the_replay_series, ..._the_youtube_fetch_captures_the_heatmap_and_chat_where_links_are_handled |
| DW-3.3 | 2-hour timing target on a synthetic long file, or report the shortfall | COVERED | test_DW_3_3_... (6-min synthetic, linear projection; CLIPPING_LONG_TEST=1 builds the true 2 h file); measured manually on real 59-min and 2 h files (see report) |
| DW-3.4 | Laughter, reaction words, chat bursts on fixtures; each falls back to "absent" | COVERED | test_DW_3_4_laughter_is_found_in_a_laugh_clip..., test_DW_3_4_reaction_words_in_english_and_arabic, test_DW_3_4_chat_bursts_are_counted_per_second_from_a_saved_replay, test_DW_3_4_chat_is_absent_without_a_replay, test_DW_3_4_reactions_are_absent_..., model-present/absent/broken tests |

**All items COVERED:** YES

## Design Decisions (design it twice)
Approaches for the signals module: (A) one function per signal, each decoding the file itself; (B) one pass over the media (audio once, tiny frames once) feeding every signal, behind `compute`/`load`; (C) a registry adapter per signal. Chosen B: decoding is the cost (measured: audio 59 min in 24 s, full 1080p video decode 109 s), so passes are shared and run side by side in two threads; callers see only `load` and `compute`. C rejected: local measurements have no vendor, cost cap or fallback chain to manage.
- `signals.load(conn, id)` returns the contract dict, or **None** when nothing is stored (so moments can run without it). `signals.compute(conn, id)` is get-or-compute; the `signals` job calls it.
- Media series are cached per file hash; text series and topics are tied to the transcript version and rebuilt (no FFmpeg) after a correction.
- Loudness: ebur128 momentary (100 ms readings, max per second) via `ametadata`, in the same decode that feeds laughter; floor -70 LUFS.
- Scene/face: one 64x36 RGB frame per second from FFmpeg; scene = mean colour change / 50; face = share of skin-toned pixels / 0.10 (a stand-in for a face detector, no OpenCV). Videos over 20 min decode keyframes only (`-skip_frame nokey`), 2.4x faster.
- Speech rate = words per second spread over each word's span (0 = pause); zeroed where loudness <= -60 LUFS. Fast talk = a high speech_rate (no separate series). Turns need speaker labels (0 otherwise).
- Reaction = phrase hits per second, English and Arabic lists both always applied, normalised (case, punctuation, Arabic marks, alef/ya).
- Topics: TextTiling over blocks of 12 content words, 6 blocks each side, depth cutoff mean + 0.5 sd, minimum topic 60 s; title = three most common content words. Re-implemented, no ClipsAI code.
- Heatmap/chat parsed in `adapters/heatmap.py` (never raise, bounded); captured in `sources.stage_fetch_source` only on the YouTube path (already gated by the owner's confirmation that the brief permits it); `download_youtube` gained an optional `info_out` parameter (backwards compatible) and asks yt-dlp for the live-chat replay file.

## Prerequisites
- [x] Files exist / new files created  - [x] numpy available (added to requirements)  - [ ] sherpa-onnx/yt-dlp not installable here (optional by design)

## Recommendation
BUILD (done).
