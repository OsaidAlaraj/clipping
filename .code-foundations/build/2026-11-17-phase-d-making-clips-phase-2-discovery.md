# Discovery + Design: Phase 2 - Correction screen and accuracy by provider and dialect

## Files Found
- backend/clipping/transcripts.py (Phase 1: clean_words, speaker_map, key_terms_for, create/load/save_version, StaleTranscript)
- backend/clipping/api.py (thin handlers, `run(actor, fn, ...)`, `app_error` handler returns only {error, code})
- backend/clipping/review.py (`CLIP_VIEW` has candidate_id but no source_id)
- backend/migrations/009b_transcripts.sql (transcript_versions with RLS, campaigns.key_terms, transcripts.current_version)
- backend/tests/test_9_transcription.py (fixtures `stored`, helpers), conftest (`db`, `owner`, `approved_campaign`)
- web: components/Explore.tsx (design language), More.tsx (Settings, CampaignDetail), Feed.tsx (EditSheet, toast/undo), Shell.tsx, lib/api.ts, tests/helpers.tsx (`mockApi`), globals.css tokens
- docs/code-standards.md read; no PRODUCT.md/DESIGN.md (impeccable: scoped work on an existing UI, token file globals.css is the visual authority)

## Current State
Transcripts are versioned and loadable, but no edit API, no queue, no accuracy, no screen. `load(conn, source_id)` is keyed by SOURCE id and `save_version` by TRANSCRIPT id. Another owner's rows are invisible under RLS, but `load` raises a 400 `no_transcript` (needs 404).

## Gaps (plan vs reality)
1. Contract says `{id}`; `load()` takes a source id. Decision: `{id}` in all three transcript routes is the SOURCE id (what the screen is linked from). `load()["id"]` stays the transcript id.
2. Contract 409 body is `{"code":"stale","version":int}` but the handler drops extra fields: handler now merges an optional `extra` dict from the exception; `StaleTranscript.status = 409`.
3. Screen needs the campaign id (key terms), the media to play from, and the key terms: `GET` returns `load()` plus `source_id, campaign_id, campaign_name, language, media_url, key_terms` (additive).
4. Undo with append-only versions: added a fourth edit op `{"op":"revert","to_version":n}` (new version with the content of version n). Additive to the pinned op union.
5. Clips need `source_id` for the Review link: `CLIP_VIEW` gets `source_id` (one scalar subquery).
6. Migration 010_corrections.sql: `transcripts.wer_errors/wer_words` (running counts vs version 1, written by save_version). No new table, so no new RLS.
7. CORS allows GET/POST only, but the web app uses the same-origin proxy (PUT /api/settings already works that way). Unchanged.

## Code Standards
Thin handlers; AppError for refusals (plain sentences); domain logic in transcripts.py; migration additive; tests `test_10_corrections.py` full-sentence names; web tests vitest + mockApi; US English copy; tap targets >= 44 px; tokens only.

## Test Infrastructure
pytest with embedded Postgres per test (`db`, `owner`), `stored` fixture pattern from test_9; `TestClient(api.app)` with `api._owner = owner` for API tests. Web: vitest + Testing Library, `mockApi`, next/link and next/navigation mocked per file.

## DW Verification

| DW-ID | Done-When Item | Status | Test Cases |
|-------|---------------|--------|------------|
| DW-2.1 | Editing a word, replacing a phrase and renaming a speaker each save a new version, and undo restores the previous one. | COVERED | test_DW_2_1_word_edit_saves_a_new_version, ..._replace_phrase_..., ..._rename_speaker_..., ..._undo_restores_previous_version (domain + API); web: test_DW_2_1 edit applies at once and saves in the background, undo reverts at once and sends revert |
| DW-2.2 | The review queue lists low-confidence words and key-term mismatches first. | COVERED | test_DW_2_2_queue_lists_key_term_mismatches_before_low_confidence_words, ..._queue_candidate_ranges_..., endpoint test; web: queue renders in server order with the reason |
| DW-2.3 | Accuracy per provider and dialect updates from corrections and shows in Settings. | COVERED | test_DW_2_3_accuracy_updates_from_corrections_per_provider_and_dialect (+ wer unit tests); web: Settings shows the accuracy rows |
| DW-2.4 | 375 px and desktop with no sideways scroll; keyboard shortcuts on desktop; another owner's transcript returns 404. | COVERED | test_DW_2_4_another_owners_transcript_is_404 (GET, PUT, queue); web: test_DW_2_4 shortcuts (space, arrows, Enter, Ctrl+Z, n), no fixed-width/overflow elements asserted via style audit; real 375 px and desktop check in the running dev server (overflow measured with JS) |

**All items COVERED:** YES (5 DW rows in the prompt: DW-2.1 to DW-2.4 = 4 items; table has 4)

## Design Decisions
- Edit ops (server, pure `apply_edits`): `word {index,text}` (blank text deletes; spaces split into words with time shared; same text = "confirm", confidence 1.0), `replace {find,replace,match_case?}` (token match ignoring case, punctuation, Arabic marks and alef variants; punctuation around the match is kept; touched words = words matched; more than 100 and not confirm_bulk raises 409 `confirm_bulk`), `speaker {speaker,name}`, `revert {to_version}`. Indexes refer to the list after the previous edit in the same request. Edited words get confidence 1.0 (owner verified), so they leave the queue.
- Queue (`review_queue(words, key_terms, ranges=None, limit)`): groups = key-term near-misses (fuzzy, ratio >= 0.75, not equal, windows of k-1..k+1 words) then low confidence (< 0.8, ascending); words inside candidate `ranges` come first in each group. Row: `{word_index,text,confidence,reason}` plus additive `term`, `span`.
- Accuracy: WER = (substitutions+deletions+insertions)/corrected words, normalised tokens (case, punctuation, marks), difflib anchors + Levenshtein on gaps. Stored as running counts on transcripts; aggregated per (provider, dialect). A transcript counts once it has any saved version after the first (confirming words counts as reviewed).
- Key terms: POST trims, collapses spaces, 1..100 chars, case-insensitive dedupe, max 200 terms; returns the list.
- Web: one `Transcript.tsx` screen (queue tab = "Fix first", transcript tab); optimistic local apply (mirror of the server ops), a sequential save queue with base-version tracking, undo = cancel unsent edit or enqueue revert, rollback + message on refusal, retry on network loss, merge-or-tell on 409 stale (re-apply pending edits on the fresh words when the target word still matches). Paragraph components memoised with content-visibility for long transcripts; playback highlight set on the DOM without re-render. RTL: paragraphs dir="auto", words isolated.
- Route: `/transcript?id=<source id>` (same pattern as /campaign?id=).

## Prerequisites
- [x] Required files exist
- [x] Dependencies available (no new packages)

## Recommendation
BUILD

## Resume review (second build agent)
Reviewed the partial work against the design above: every route, op, queue rule, accuracy path and screen behavior is implemented and tested (backend test_10_corrections.py: 105 tests; web transcript.test.tsx: 97 web tests in the suite). No code changes were needed. Verified: full backend suite green (1 skipped as before), ruff CI selection clean, vitest green, tsc clean, production build of /transcript succeeded (done in a temp copy with --webpack so the running dev server's .next was not touched; the temp copy was removed). All touched files are LF.
