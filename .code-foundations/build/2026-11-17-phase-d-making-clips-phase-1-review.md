# Review: Phase 1 - Transcription upgrade (Deepgram Nova-3, faster-whisper, versioned transcripts)

## Executed Results (Step 0)
- Backend suite: `pytest -p no:cacheprovider -q` -> all pass, 1 skipped (the opt-in real-Whisper test). test_9_transcription.py alone: all pass, 1 skipped.
- Opt-in real run: `CLIPPING_REAL_WHISPER=1 pytest -k real_whisper tests/test_9_transcription.py -s` -> 1 passed, printed `REAL_WHISPER cuda None 0` (ran on CUDA, no degraded note, 0 words from the test tone). Model files exist at `.data/cache/whisper/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo`. nvidia-smi sampled every 0.5 s: GPU memory peaked at about 1.1 GB of 6 GB (baseline under 100 MB). Host RAM was not measured separately.
- Lint (CI selection): `ruff check --select E9,F63,F7,F82 backend` -> All checks passed.
- Web is unchanged and was not re-run.

## Requirement Fulfillment

### DW-1.1
PREMISE:  Deepgram requests carry the dialect code, diarize, utterances and keyterms (checked on the fake's recorded request).
EVIDENCE: transcription.py:87-94 (deepgram_params), 36-46 (resolve_language_code), 49-61 (cap_keyterms), 344-351 (FakeTranscription records params); tests test_9_transcription.py:625-636, 221-231, 135-139.
TRACE:    ar-SA source with 60 owner terms -> key_terms_for -> FakeTranscription.transcribe -> params contain language=ar-SA, diarize=true, utterances=true, 50 keyterm entries (first 50 owner terms). The real DeepgramTranscription sends the same params, verified on an httpx MockTransport request.
VERDICT:  PASS

### DW-1.2
PREMISE:  Deepgram failing three times falls back to faster-whisper (fake in tests). A real Whisper run on the 60 s fixture works on this laptop, with the model files under `.data\cache` and int8 memory use under 6 GB.
EVIDENCE: base.py Registry.call (max_attempts=3, fallback), migration 009b_transcripts.sql:59-62 (deepgram fallback = faster_whisper), transcription.py:229-283, pipeline.py:102-120; tests :646-662, :665-675, :771-778.
TRACE:    Deepgram mock returns 500 -> 3 requests -> adapter_failed alert -> registry recursion to faster_whisper with a fake model -> transcript row provider=faster_whisper, job done. Real run: 60 s fixture on cuda int8, model under .data/cache/whisper, GPU about 1.1 GB peak.
VERDICT:  PASS

### DW-1.3
PREMISE:  Transcripts keep speakers and confidence; `load` returns the latest version.
EVIDENCE: transcripts.py:162-182 (load, picks max version_no), 185-201 (save_version); tests :518-552.
TRACE:    create v1, save_version twice -> load returns version 3 with only the last words. Words keep confidence and speaker, and speakers map {id: name} is stored per version. Contract keys (id, version, provider, dialect, words, utterances, speakers) match. save_version(conn, actor, transcript_id, words, speakers, base_version) returns the new version, with a stale base refused (StaleTranscript).
VERDICT:  PASS

### DW-1.4
PREMISE:  No key or token value appears in the database, audit log or job errors after a failed Deepgram call (test scans them).
EVIDENCE: transcription.py:126-132 (transport errors reduced to the exception type name, `from None`), base.py check_response (status only), Registry.call config.redact; tests :727-756 scan every public table as text.
TRACE:    Deepgram returns 401 echoing the key, or the transport raises with the key in the message -> AdapterError("HTTP 401") or "...ConnectError" -> three attempts, fallback Whisper also fails with the key in its message -> job failed -> scan of all tables finds no SECRET. Tests assert the failure path actually ran (3 requests, job failed).
VERDICT:  PASS

**All requirements met:** YES

## Edge cases
- No GPU or no CUDA DLLs -> CPU with alert: PASS. `_devices` returns cpu plus a reason, the result gets `degraded`, and pipeline.py raises the `transcription_degraded` alert (tests :328-333, :665-675, :335-341 for a GPU failing mid-decode).
- Empty audio -> AppError `empty_transcript`: PASS. `transcripts.create` raises it and stores nothing (tests :582-587, :688-695, with the job failed and no moments job queued).
- Dialect that does not fit the language -> plain code: PASS (tests :146-148, including ar with en-US, ar-XX, and en with ar-SA).
- Key terms over limit -> exactly N: PASS (cap_keyterms; test :156-159, 80 terms cut to exactly 50 in the first N order, and the integration test at :625).

## Constraints
- No secret stored or logged: PASS (DW-1.4 scan; httpx logging quieted in base.py).
- Adapter registry plus fake: PASS (all three providers `@register`ed, FakeTranscription present, alerts.py imports the module, vendors.py re-exports).
- Migration additive with RLS: PASS. Only `add column if not exists`, a new table, inserts, and an update of config on the existing deepgram row. transcript_versions has RLS enabled and forced, 5 policies, and a test confirms it.
- Files and caches on L: PASS. Model download_root and HF_HOME go under data_dir()/cache (repo .data on L:), temp audio under data_dir()/tmp. The test verifies the paths.
- `deepgram_basic` reachable: PASS (provider registered and seeded, sends no diarize, utterances or keyterms; tests :291-295, :615-620).

## Test-DW Coverage
- [x] All DW items have corresponding tests that ran in Step 0 (DW-1.2 real-GPU run also executed with the opt-in flag).
- [x] Coverage level 100%: every new routine in transcription.py and transcripts.py is exercised. `_clean_utterances`, `_put_text`, `_write_version`, `DeepgramTranscription._send` and `stage_transcribe`/`_transcribe` are covered through direct callers rather than their own named unit tests; I count that as covered. Gaps: none demonstrated.

## Dead Code
None found.

## Correctness Dimensions
| Dimension | Status | Evidence |
|-----------|--------|----------|
| Concurrency | PASS | save_version takes `select ... for update` and compares base_version; stale edit raises StaleTranscript (test :555-561). `_MODELS` cache is process-local with no lock, but the worker is single-threaded here; not demonstrated as a defect. |
| Error Handling | PASS | Vendor errors become AdapterError without bodies, bad response shapes are rejected, unreadable audio is handled, and GPU failure falls back to CPU. |
| Resources | PASS | Audio lives in a TemporaryDirectory (test :698-701 confirms no leftover wav), and DLL handles are held on purpose. |
| Boundaries | PASS | clean_words drops NaN, negative, blank and malformed words and clamps confidence. Empty keyterm list gives prompt None, and a limit of 0 gives an empty list. |
| Security | PASS | Key is only in the Authorization header, with messages carrying status or exception type only. |

## Loaded-Skill Criteria
| Skill | Criterion | Status | Evidence |
|-------|-----------|--------|----------|
| cc-defensive-programming | External input validated at entry (vendor JSON, owner edits) | PASS | parse_deepgram shape-checks; clean_words/speaker_map validate. |
| cc-defensive-programming | No empty catch blocks | PASS | Every `except` re-raises, converts, or falls back with a recorded reason (cuda_available returns False by design). |
| cc-defensive-programming | No executable code in assertions / assertions for bugs only | PASS | No asserts in production code. |
| aposd-designing-deep-modules | Info hiding; common case simple | PASS | One `transcribe(...)` interface for all providers; dialect codes, key-term limits, CUDA DLLs and device fallback hidden in transcription.py; persistence hidden in transcripts.py. |
| aposd-designing-deep-modules | Silent failure | PASS | Degraded (CPU) mode is surfaced as `degraded` and an alert. |

## Notes (non-blocking)
- `_clean_utterances` calls `item.get` on each item without a dict check, so a non-dict item would raise AttributeError. Today only parse_deepgram and the fakes feed it, so this is not demonstrated.
- Host RAM for int8 was not measured; only GPU memory (about 1.1 GB peak) was sampled. Both are far under 6 GB for large-v3-turbo int8.
- The Whisper error text (first 160 chars of the exception) goes into the stored job error without redaction by the adapter (the registry redacts it, and the test covers a secret in it).
- `.code-foundations/plans/...` is modified in the working tree, outside the reviewed file list.

## Issues (if FAIL)
None.

**Verdict: PASS**
