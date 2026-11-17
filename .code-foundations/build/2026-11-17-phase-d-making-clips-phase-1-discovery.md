# Discovery + Design: Phase 1 - Transcription upgrade

## Files Found
- backend/clipping/adapters/vendors.py: A3 `DeepgramTranscription`, `FakeTranscription`, `FAKE_SENTENCES`, `fake_words` (tests use `vendors.FAKE_SENTENCES`, `vendors.fake_words`); also owns `_http`/`_check`.
- backend/clipping/adapters/base.py: registry (cap, 3 attempts, `config.redact` on stored errors, fallback via `adapters.fallback_provider` if `(stage, fallback)` is in `PROVIDERS`).
- backend/clipping/pipeline.py: `stage_transcribe`, `_words` (reads newest `transcripts.word_json_path`), `_srt`; callers of `_words`: moments, manual candidates, hooks, captions.
- backend/migrations: 001 `transcripts` (unique source_hash, language, dialect, provider), 003 RLS pattern, 004 adapters seed (deepgram fallback `manual`), 009_live_providers.sql.
- backend/tests: conftest (`_reset_fakes`), test_1 (adapter interface test calls `adapter.cost({})`, `config()`, `health()` on every adapter row), test_3 (reads word file as a JSON list; transcript cached by source hash).
- No `key_terms` anywhere; `campaigns` has no column for them (Phase 2 adds the write endpoint).

## Current State
Deepgram sends only language, punctuate, smart_format; returns words without confidence or speaker. No Whisper adapter. One transcript row per source hash with a word file; no versions; `_words` picks the newest row. `.venv` has no `faster_whisper` yet (background pip), so the adapter loads it lazily and tests inject a fake model.

## Gaps
- Migration number: plan says 009_*; Phase 3 plans 010_*. Chosen name `009b_transcripts.sql` (sorts after `009_live_providers.sql` because `_` < `b`, and before `010_`).
- Plan has no storage for campaign key terms; added `campaigns.key_terms text[]` (additive) so Phase 2's endpoint only writes it. Key terms = owner-added first, then campaign name, then required hashtags/mentions.
- Adapters cannot import domain modules, and `transcription.py` needs `_http`/`_check` from vendors: moved to `base.py` (`http_client`, `check_response`), vendors re-imports them.
- Registry only falls back after 3 failures, not when the adapter is paused (cap reached / price unknown): `stage_transcribe` catches `AdapterPaused` and calls the local provider (declared fallback must not drop work).

## Code Standards
Adapter classes `<Provider><Stage>` + `Fake<Stage>` with module dict `FAKE_<STAGE>`; vendor failures raise `AdapterError` with status only; secrets only via `config.env`; AppError subclass for a refusal callers branch on; heavy imports inside functions; migration adds RLS in the same file; test file `test_9_transcription.py`, sentence names, at least one error-path test per feature.

## Test Infrastructure
pytest + embedded Postgres, fresh DB per test; fakes selected in conftest; `db`, `owner`, `produced`, `footage` fixtures; MockTransport-style monkeypatching of `transcription.http_client` is new (no existing http tests).

## DW Verification

| DW-ID | Done-When Item | Status | Test Cases |
|-------|---------------|--------|------------|
| DW-1.1 | Deepgram requests carry dialect code, diarize, utterances and keyterms (checked on the fake's recorded request) | COVERED | test_DW_1_1_fake_records_the_same_request_the_deepgram_adapter_sends (stage_transcribe through the fake: language_code ar-SA, diarize, utterances, keyterm list from the campaign, capped); test_DW_1_1_deepgram_adapter_sends_params_and_parses_confidence_and_speakers (MockTransport); keyterm-cap, dialect-resolution unit tests |
| DW-1.2 | Deepgram failing 3 times falls back to faster-whisper (fake); real Whisper run on 60 s fixture, model under .data/cache, int8 memory under 6 GB | COVERED | test_DW_1_2_three_deepgram_failures_fall_back_to_faster_whisper (MockTransport 500 x3 + fake model, through stage_transcribe); test_DW_1_2_cap_pause_uses_local_whisper; cuda->cpu fallback + alert tests; manual real run on fixture recorded in report (marked `real_whisper`, skipped unless `CLIPPING_REAL_WHISPER=1`) |
| DW-1.3 | Transcripts keep speakers and confidence; `load` returns latest version | COVERED | test_DW_1_3_transcript_keeps_speakers_and_confidence; test_DW_1_3_load_returns_the_latest_version; save_version stale/validation tests; old-format file still loads |
| DW-1.4 | No key/token value in DB, audit log or job errors after failed Deepgram call | COVERED | test_DW_1_4_failed_deepgram_call_leaves_no_key_anywhere (scans every table text + job errors; 401 body and transport exception both echo the key) |

**All items COVERED:** YES (4/4)

## Design Decisions

Design it twice (module: transcription):
1. One `transcription.py` with a `Provider` base doing params/parse per vendor, domain code (`transcripts.py`) owns persistence and versions. (chosen)
2. One big `TranscriptionService` class in the domain layer calling vendors directly: breaks the registry rule, no fallback chain.
3. Adapters persist their own results: leaks storage + RLS into adapters, violates dependency direction.

Interface (deep, small): adapters expose only `transcribe(audio_path, language, dialect_hint=None, duration_s=0, keyterms=())` returning `{words, utterances, provider, model, language_code, speakers?, device?, degraded?}`. Hidden: dialect validation, key-term cap (limit lives in adapter config), request params, response parsing, CUDA DLL setup, device fallback, model cache. Domain: `transcripts.create / load / save_version / key_terms_for`; `pipeline.stage_transcribe` becomes ~25 lines.

- Word shape: `{text,start,end,confidence,speaker}`; speaker is a string id ("0","1") or None; `speakers` maps id to a display name ("Speaker 1"). Word file stays a plain JSON list (old readers and test_3 keep working). Utterances and speakers live in new table `transcript_versions` (jsonb), derived from words when absent (old rows).
- Versions: `transcripts.current_version`; each version row has its own word file `..._vN.json`. `save_version` raises `StaleTranscript(AppError, code="stale")` carrying the current version when `base_version` is not current (row locked `for update`).
- `load` looks up by source_id first, else by (source hash, language), like the old `_words` (second campaign reuses the cached transcript).
- Barricade: `clean_words` in transcripts validates vendor output (text, finite times, confidence clamp 0..1) before anything is stored.
- Dialect resolution: `ar` accepts the 17 Nova-3 Arabic codes; `en` accepts `en` and the regional English codes; anything else falls back to the plain language code.
- Whisper: lazy `faster_whisper` import; model `large-v3-turbo` int8, `HF_HOME` and `download_root` under `<data_dir>/cache`; `os.add_dll_directory` + PATH for `site-packages/nvidia/*/bin`; CUDA first, CPU int8 on any CUDA failure (also lazy failures during inference), result carries `device`/`degraded`, stage raises an alert. Key terms go to Whisper as `initial_prompt`.
- Old call kept as provider `deepgram_basic` (inactive row, fallback faster_whisper).

## Prerequisites
- [x] Required files exist (or will be created)
- [x] Dependencies available (httpx present; faster-whisper being installed separately, lazy import)
- [x] faster-whisper 1.2.1, ctranslate2 4.9.0 and the nvidia wheels are now installed in .venv

## Recommendation
BUILD

## Build Log (resumed)
- transcription.py was complete; pipeline.py and conftest.py were converted from CRLF back to LF.
- Tests: backend/tests/test_9_transcription.py (all DW items plus unit tests for every new routine; the real GPU run is `CLIPPING_REAL_WHISPER=1`).
- Decision: a failed job rolls back its own transaction, so `adapter.call ok=false` audit rows of a failed job are not kept (existing behaviour); DW-1.4 scans every table plus the job error instead.
- faster-whisper is a normal requirement; the nvidia-* wheels are local only and not in requirements.txt.
- Real run (DW-1.2): large-v3-turbo int8 on the GTX 1660 Ti, CUDA, 60 s fixture, 0 words (test tone); peak GPU memory 1089 MiB of 6144 (nvidia-smi, 1 s samples); model files 1.5 GB under .data/cache/whisper.
- Found: PyAV 19 (pulled in by faster-whisper) breaks faster-whisper 1.2.1 decoding (`metadata_errors`). Fix: `read_wav` hands Whisper the ffmpeg 16 kHz wav as a numpy array, so PyAV decode is never used.
- Found: the Xet download transport stalled; `HF_HUB_DISABLE_XET=1` downloaded the model fine (local first-download note only).
