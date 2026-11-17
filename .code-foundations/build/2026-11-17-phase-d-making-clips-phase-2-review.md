# Review: Phase 2 - Correction screen

## Executed Results (Step 0)
- Backend: `pytest -p no:cacheprovider -q` -> all dots, 1 skipped, 0 failures (test_10_corrections.py: 105 test rows, all pass when rerun alone).
- Ruff CI selection (E9,F63,F7,F82) -> All checks passed.
- Web: `vitest run` -> 6 files, 97 tests passed. `tsc --noEmit` -> clean.

## Requirement Fulfillment

### DW-2.1
PREMISE: Editing a word, replacing a phrase and renaming a speaker each save a new version, and undo restores the previous one.
EVIDENCE: transcripts.py:230-260 (save_version/update), 364-436 (edits, revert); api.py PUT /api/transcripts/{id}; Transcript.tsx:171-202 (commit/undo).
TRACE: PUT {base_version:N, edits:[word|replace|speaker]} -> update loads, checks N, apply_edits, save_version writes `_vN+1` files + row, current_version+1 -> {"version":N+1}. Undo sends {"op":"revert","to_version":baseUsed} -> read_version restores old words, saved as a new version.
Tests: test_DW_2_1_* (word/replace/speaker/undo/PUT endpoint) and web "DW-2.1 ..." tests all pass.
VERDICT: PASS

### DW-2.2
PREMISE: The review queue lists low-confidence words and key-term mismatches first.
EVIDENCE: transcripts.py:476-500 (review_queue sort key: in-range, key_term before low_confidence, least sure), 544-548 (queue_for); api GET .../review-queue.
TRACE: words + campaign key terms -> near-miss spans (similarity >= 0.75, exact spellings excluded) + conf < 0.8 -> sorted key terms first, then least sure.
Tests: test_DW_2_2_* (unit and endpoint), web opening-the-screen test pass.
VERDICT: PASS

### DW-2.3
PREMISE: Accuracy per provider and dialect updates from corrections and shows in Settings.
EVIDENCE: transcripts.py:302-321 (wer_counts, accuracy), save_version:244-247 updates wer_errors/wer_words; migration 010; More.tsx AccuracySection in Settings.
TRACE: save -> WER of new text vs version 1 stored -> GET /api/transcripts/accuracy groups by provider and dialect -> Settings rows with percent.
Tests: test_DW_2_3_*, test_accuracy_*, web "DW-2.3 ..." pass. Route /accuracy declared before /{source_id}.
VERDICT: PASS

### DW-2.4
PREMISE: The screen works at 375 px and on desktop with no sideways scroll; keyboard shortcuts work on desktop; another owner's transcript returns 404.
EVIDENCE: globals.css tx block (minmax(0,1fr) grid, overflow-wrap anywhere, min-width 0, tabs under 900px, two columns above); Transcript.tsx:447-489 shortcuts; transcripts.NotFound via RLS.
TRACE: owner B requests owner A's source id -> RLS hides rows -> NotFound 404 on GET/PUT/queue (test_DW_2_4_another_owners_transcript_returns_404_for_every_route passes). Shortcuts covered by web DW-2.4 tests. The 375 px check is a stylesheet-rule test plus code reading of the CSS (no live render measured); I found no overflow source.
VERDICT: PASS

**All requirements met:** YES

## Edge cases
- Stale version: backend 409 {"code":"stale","version":N} (test_a_stale_put_answers_409...); client rebase merges where words still match, drops and tells the owner otherwise (web tests "merged", "no longer fits"). Handled.
- Arabic RTL mixed with English: dir="auto", bdi/unicode-bidi isolate, Arabic-fold normalise on both sides, arrow keys flip in RTL (tests for Arabic replace and web "arabic mixed with english"). Handled.
- Bulk replace: `touched > 100` needs confirm, exactly 100 does not, on server (test ...exactly_100...101) and client (web test "exactly 100"). Handled.

## Test-DW Coverage
- [x] All DW items have tests that ran in Step 0.
- [x] 100% level: every new or changed routine in transcripts.py has a unit test (clean_words/create/load were previous-phase; new ones normalise, wer_counts, accuracy, apply_edits ops, review_queue, add_key_term, detail, queue_for, save_version, update all tested). Web lib/transcript.ts functions tested; Transcript.tsx hook and components exercised. Gaps: none found.

## Dead Code
None found.

## Correctness Dimensions
| Dimension | Status | Evidence |
|---|---|---|
| Concurrency | PASS | save_version locks the transcript row (`for update`) and rechecks base_version, so two writers cannot both win; loser gets 409. |
| Error Handling | PASS | Bad edits -> 400 bad_edit with a plain message, malformed body -> 422, nothing saved (tests). Client rolls back screen on refusal and retries on network loss. |
| Resources | PASS | Temp dirs via TemporaryDirectory; timers and listeners cleaned up in effect cleanup. |
| Boundaries | PASS | Index bounds, bool-as-int rejected, MAX_EDITS 500, MAX_TEXT 200, levenshtein cap 250k, empty transcript refused. |
| Security | PASS | All routes via run(actor) under RLS; other owner -> 404; key-terms route also 404. No dangerouslySetInnerHTML/innerHTML anywhere; text rendered by React and stored as JSON data; control chars stripped. |

## Loaded-Skill Criteria
| Skill | Criterion | Status | Evidence |
|---|---|---|---|
| cc-defensive-programming | External input validated at entry | PASS | _typed/_index/clean_words/pydantic strict base_version; no assert-based validation. |
| cc-defensive-programming | No empty catch / side-effecting asserts | PASS | Client catches in refreshQueue set a fallback state; no asserts. |
| web-design-guidelines | Labels, aria-live, icon aria-labels, reduced layout shift, focus-visible global, tabular numerals, `…` in loading | PASS | Inputs labelled, toast role=status/alert, icon buttons labelled, :focus-visible global, beforeunload warns on unsaved edits. |
| web-design-guidelines | Keyboard operability | PASS (note) | Words are tabIndex=-1 buttons reached through arrow keys; Tab does not walk words. |
| impeccable | Operate-mode: instant feel, skeletons matching layout, 44px targets, no layout shift | PASS | Optimistic edits, skeleton mirrors layout, 44px controls in CSS. |

## Notes (non-blocking)
- A non-UUID id in `/api/transcripts/{x}` would reach Postgres as invalid uuid text and likely return 500, as other routes in the app do (unlisted edge case; not run live).
- If a PUT succeeds server-side but the response is lost, the retry gets stale, the rebase re-reads the saved edit, and the client reports one edit "could not be merged" although the result is correct.
- Key-term near-match scan is O(words x terms x 3) with join per candidate; fine for tests, untimed on a 1-hour transcript with 200 terms.
- `sourceId` is interpolated unencoded into API paths (owner-supplied query value only).
- Words are not Tab-focusable (arrow-key navigation instead); screen-reader users rely on the editing button row.
- Did not render at 375 px in a live browser; relied on CSS review and the stylesheet test.

## Issues (if FAIL)
None.

**Verdict: PASS**
