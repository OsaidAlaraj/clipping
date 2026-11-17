"""Phase D step 2: the correction screen's backend. Owner edits (word, replace, speaker, undo), the fix queue,
word error rate per provider and dialect, campaign key terms, and the API around them. Everything runs on the fake
adapters and the embedded database; no test calls a live service."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from clipping import api, transcripts
from clipping.core import AppError
from clipping.db import Actor
from conftest import approved_campaign


# ------------------------------------------------------------------ helpers

def w(text, start, confidence=0.95, speaker="0", length=0.4) -> dict:
    return {"text": text, "start": float(start), "end": round(start + length, 3), "confidence": confidence, "speaker": speaker}


def sentence(*texts, confidence=0.95, speaker="0") -> list[dict]:
    return [w(t, i * 0.5, confidence, speaker) for i, t in enumerate(texts)]


def texts(words) -> list[str]:
    return [x["text"] for x in words]


def make_transcript(db, owner, words, provider="deepgram", language="en", language_code=None, file_hash=None, name="Founders Table"):
    """A source with a version-1 transcript, created the way the pipeline does it."""
    campaign_id = approved_campaign(db, owner, name)
    with db.session(owner) as conn:
        source = conn.execute(
            """insert into source_assets (campaign_id, owner_operator_id, stored_path, file_hash, language, duration_s, permission_ref)
               values (%s, %s, 'sources/x.mp4', %s, %s, 60, 'P') returning *""",
            (campaign_id, owner.operator_id, file_hash or "c" * 64, language)).fetchone()
        transcript = transcripts.create(conn, source, {"words": words, "provider": provider, "model": "m", "language_code": language_code})
    return SimpleNamespace(db=db, owner=owner, campaign_id=campaign_id, source=source, transcript=transcript, source_id=str(source["id"]))


def edit(made, base, *edits, confirm=False) -> int:
    with made.db.session(made.owner) as conn:
        return transcripts.update(conn, made.owner, made.source_id, base, list(edits), confirm)


def current(made) -> dict:
    with made.db.session(made.owner) as conn:
        return transcripts.load(conn, made.source_id)


HELLO = ["Hello", "there", "and", "welcome", "to", "Acme", "Cola", "today"]


@pytest.fixture
def made(db, owner):
    words = [w(t, i * 0.5, speaker="0" if i < 4 else "1", confidence=0.4 if t == "there" else 0.95) for i, t in enumerate(HELLO)]
    return make_transcript(db, owner, words)


# ------------------------------------------------------------------ text normalising and word error rate (unit)

def test_normalise_ignores_case_punctuation_and_arabic_vowel_marks():
    assert transcripts.normalise("“Hello,”") == "hello" and transcripts.normalise("Don't") == "dont"
    assert transcripts.normalise("مَرْحَبًا") == transcripts.normalise("مرحبا") == "مرحبا"
    assert transcripts.normalise("مـرحبا") == "مرحبا"   # tatweel
    assert transcripts.normalise("أحمد") != transcripts.normalise("احمد")                   # an error when counting
    assert transcripts.normalise("أحمد", fold_arabic=True) == transcripts.normalise("احمد", fold_arabic=True)   # a match when finding
    assert transcripts.normalise("Acme", keep_case=True) == "Acme" and transcripts.normalise("—") == ""


@pytest.mark.parametrize("corrected,heard,errors,words", [
    (["hello", "there"], ["hello", "there"], 0, 2),
    (["Hello,", "there."], ["hello", "there"], 0, 2),                      # case and punctuation are not errors
    (["hello", "everyone"], ["hello", "there"], 1, 2),                    # substitution
    (["hello", "big", "world"], ["hello", "world"], 1, 3),                # the provider missed a word
    (["hello"], ["hello", "um", "uh"], 2, 1),                             # the provider invented words
    ([], ["a", "b"], 2, 0),
    (["مرحبا", "بكم"], ["مَرْحَبًا", "بكم"], 0, 2),
    (["مرحبا", "hello", "world"], ["مرحبا", "yellow", "world"], 1, 3),    # Arabic mixed with English
])
def test_wer_counts_edits_against_the_corrected_words(corrected, heard, errors, words):
    assert transcripts.wer_counts(corrected, heard) == (errors, words)


def test_wer_counts_stays_fast_on_a_long_transcript_with_scattered_fixes():
    heard = [f"word{i % 300}" for i in range(6000)]
    corrected = list(heard)
    for i in range(50, 6000, 300):
        corrected[i] = "fixed"
    started = time.perf_counter()
    errors, words = transcripts.wer_counts(corrected, heard)
    assert (errors, words) == (20, 6000) and time.perf_counter() - started < 5


def test_levenshtein_gives_up_on_a_huge_unanchored_stretch_instead_of_stalling():
    assert transcripts._levenshtein(list("abc"), list("abd")) == 1
    assert transcripts._levenshtein(["a"] * 600, ["b"] * 600) == 600


# ------------------------------------------------------------------ apply_edits (unit)

def test_a_word_edit_changes_the_text_keeps_its_time_and_vouches_for_it():
    words = sentence("hello", "wrold", confidence=0.3)
    got, _ = transcripts.apply_edits(words, {}, [{"op": "word", "index": 1, "text": "world"}])
    assert got[1] == {"text": "world", "start": 0.5, "end": 0.9, "confidence": 1.0, "speaker": "0"} and got[0]["confidence"] == 0.3
    assert words[1]["text"] == "wrold"   # the caller's list is not changed


def test_a_word_edit_with_the_same_text_confirms_the_word():
    got, _ = transcripts.apply_edits(sentence("hello", confidence=0.2), {}, [{"op": "word", "index": 0, "text": "hello"}])
    assert got[0]["confidence"] == 1.0 and got[0]["text"] == "hello"


def test_a_word_edit_with_spaces_splits_into_words_sharing_the_time():
    got, _ = transcripts.apply_edits([w("acmecola", 2, speaker="1", length=1.0)], {}, [{"op": "word", "index": 0, "text": " Acme   Cola "}])
    assert [(x["text"], x["start"], x["end"], x["speaker"]) for x in got] == [("Acme", 2.0, 2.5, "1"), ("Cola", 2.5, 3.0, "1")]


def test_a_blank_word_edit_deletes_the_word_and_the_next_edit_sees_the_shorter_list():
    got, _ = transcripts.apply_edits(sentence("a", "um", "b"), {}, [{"op": "word", "index": 1, "text": "  "}, {"op": "word", "index": 1, "text": "B"}])
    assert texts(got) == ["a", "B"]


@pytest.mark.parametrize("bad", [{"index": -1, "text": "x"}, {"index": 5, "text": "x"}, {"index": True, "text": "x"}, {"index": "1", "text": "x"},
                                 {"index": 0}, {"index": 0, "text": 7}, {"index": 0, "text": "x" * 201}])
def test_a_bad_word_edit_is_refused_with_a_plain_message(bad):
    with pytest.raises(AppError) as err:
        transcripts.apply_edits(sentence("a", "b"), {}, [{"op": "word", **bad}])
    assert err.value.code == "bad_edit" and str(err.value).endswith(".")


def test_control_characters_in_typed_text_are_turned_into_spaces():
    got, _ = transcripts.apply_edits(sentence("a"), {}, [{"op": "word", "index": 0, "text": "x\x00y\x1bz\n"}])
    assert texts(got) == ["x", "y", "z"]


def test_a_replace_finds_a_phrase_ignoring_case_and_punctuation_and_keeps_the_punctuation():
    words = sentence("Visit", "acne", "Cola,", "now.")
    got, _ = transcripts.apply_edits(words, {}, [{"op": "replace", "find": "ACNE cola", "replace": "Acme Cola"}])
    assert texts(got) == ["Visit", "Acme", "Cola,", "now."]
    assert [x["start"] for x in got] == [0.0, 0.5, 1.0, 1.5] and got[1]["confidence"] == 1.0 and got[0]["confidence"] == 0.95


def test_a_replace_with_a_different_word_count_shares_the_time_of_the_matched_words():
    words = [w("acmecola", 4, length=1.0), w("rocks", 5)]
    got, _ = transcripts.apply_edits(words, {}, [{"op": "replace", "find": "acmecola", "replace": "Acme Cola"}])
    assert [(x["text"], x["start"], x["end"]) for x in got] == [("Acme", 4.0, 4.5), ("Cola", 4.5, 5.0), ("rocks", 5.0, 5.4)]
    merged, _ = transcripts.apply_edits(got, {}, [{"op": "replace", "find": "acme cola", "replace": "AcmeCola"}])
    assert [(x["text"], x["start"], x["end"]) for x in merged] == [("AcmeCola", 4.0, 5.0), ("rocks", 5.0, 5.4)]


def test_a_replace_with_nothing_removes_the_matched_words():
    got, _ = transcripts.apply_edits(sentence("so", "um", "yes", "um"), {}, [{"op": "replace", "find": "um", "replace": ""}])
    assert texts(got) == ["so", "yes"]


def test_a_replace_can_match_case_when_asked():
    words = sentence("Acme", "acme")
    assert texts(transcripts.apply_edits(words, {}, [{"op": "replace", "find": "acme", "replace": "X"}])[0]) == ["X", "X"]
    assert texts(transcripts.apply_edits(words, {}, [{"op": "replace", "find": "acme", "replace": "X", "match_case": True}])[0]) == ["Acme", "X"]


def test_a_replace_matches_arabic_with_marks_and_alef_variants_and_keeps_english_beside_it():
    words = sentence("قال", "أَحْمَد", "hello", "ثم")
    got, _ = transcripts.apply_edits(words, {}, [{"op": "replace", "find": "احمد", "replace": "محمد"}])
    assert texts(got) == ["قال", "محمد", "hello", "ثم"]


@pytest.mark.parametrize("find", ["", "   ", "—", 5, None])
def test_a_replace_without_something_to_find_is_refused(find):
    with pytest.raises(AppError) as err:
        transcripts.apply_edits(sentence("a"), {}, [{"op": "replace", "find": find, "replace": "b"}])
    assert err.value.code == "bad_edit"


def test_a_replace_touching_exactly_100_words_needs_no_confirmation_and_101_does():
    exactly = sentence(*["um"] * 100)
    assert transcripts.apply_edits(exactly, {}, [{"op": "replace", "find": "um", "replace": "uh"}])[0][0]["text"] == "uh"
    with pytest.raises(transcripts.BulkReplace) as err:
        transcripts.apply_edits(sentence(*["um"] * 101), {}, [{"op": "replace", "find": "um", "replace": "uh"}])
    assert err.value.extra == {"words": 101} and err.value.status == 409 and err.value.code == "confirm_bulk"
    confirmed, _ = transcripts.apply_edits(sentence(*["um"] * 101), {}, [{"op": "replace", "find": "um", "replace": "uh"}], confirm_bulk=True)
    assert texts(confirmed) == ["uh"] * 101


def test_a_phrase_replace_counts_every_matched_word_toward_the_limit():
    words = sentence(*["new", "york"] * 51)   # 51 matches of 2 words = 102 words
    with pytest.raises(transcripts.BulkReplace) as err:
        transcripts.apply_edits(words, {}, [{"op": "replace", "find": "new york", "replace": "NYC"}])
    assert err.value.extra["words"] == 102


def test_a_speaker_rename_changes_only_the_name_and_a_blank_name_goes_back_to_the_default():
    words = sentence("a", "b", speaker="s1")
    _, speakers = transcripts.apply_edits(words, {"s1": "Speaker 1"}, [{"op": "speaker", "speaker": "s1", "name": "  Host  "}])
    assert speakers == {"s1": "Host"}
    assert transcripts.speaker_map(words, speakers) == {"s1": "Host"}
    _, blank = transcripts.apply_edits(words, speakers, [{"op": "speaker", "speaker": "s1", "name": ""}])
    assert transcripts.speaker_map(words, blank) == {"s1": "Speaker 1"}


@pytest.mark.parametrize("edit", [{"speaker": "nobody", "name": "X"}, {"speaker": 3, "name": "X"}, {"speaker": "s1", "name": "x" * 61}, {"speaker": "s1", "name": 5}])
def test_a_bad_speaker_rename_is_refused(edit):
    with pytest.raises(AppError) as err:
        transcripts.apply_edits(sentence("a", speaker="s1"), {}, [{"op": "speaker", **edit}])
    assert err.value.code == "bad_edit"


@pytest.mark.parametrize("edits", [[], None, "word", [{"op": "dance"}], ["x"], [{"index": 0}], [{"op": "revert", "to_version": 1}]])
def test_edits_that_are_missing_unknown_or_malformed_are_refused(edits):
    with pytest.raises(AppError) as err:   # a revert needs the saved versions, which a bare call does not have
        transcripts.apply_edits(sentence("a"), {}, edits)
    assert err.value.code == "bad_edit"


def test_too_many_edits_in_one_save_are_refused():
    with pytest.raises(AppError) as err:
        transcripts.apply_edits(sentence("a"), {}, [{"op": "word", "index": 0, "text": "b"}] * (transcripts.MAX_EDITS + 1))
    assert err.value.code == "bad_edit"


@pytest.mark.parametrize("target", [0, -1, True, "1", None])
def test_a_revert_to_nothing_sensible_is_refused(target):
    with pytest.raises(AppError) as err:
        transcripts.apply_edits(sentence("a"), {}, [{"op": "revert", "to_version": target}], read_version=lambda n: ([], {}))
    assert err.value.code == "bad_edit"


# ------------------------------------------------------------------ the fix queue (unit)

def queue_words():
    return [w("Welcome", 0), w("to", 1, 0.55), w("acne", 2, 0.9), w("cola", 3, 0.7), w("and", 4, 0.99), w("say", 5, 0.5),
            w("Founders", 6), w("Table", 7), w("ok", 8, None), w("Zed", 9, 0.2)]


def test_DW_2_2_the_queue_lists_key_term_mismatches_first_then_low_confidence_words_least_sure_first():
    rows = transcripts.review_queue(queue_words(), ["Acme Cola", "Founders Table"])
    assert [(r["word_index"], r["reason"]) for r in rows] == [(2, "key_term"), (9, "low_confidence"), (5, "low_confidence"), (1, "low_confidence")]
    assert rows[0] == {"word_index": 2, "text": "acne cola", "confidence": 0.7, "reason": "key_term", "term": "Acme Cola", "span": 2}
    assert rows[1] == {"word_index": 9, "text": "Zed", "confidence": 0.2, "reason": "low_confidence"}


def test_a_key_term_spelled_right_is_not_listed_even_when_the_provider_was_unsure():
    words = [w("Acme", 0, 0.3), w("Cola", 1, 0.3), w("rocks", 2)]
    rows = transcripts.review_queue(words, ["Acme Cola"])
    assert [(r["word_index"], r["reason"]) for r in rows] == [(0, "low_confidence"), (1, "low_confidence")]


def test_a_neighbouring_word_does_not_turn_a_correct_key_term_into_a_mismatch():
    assert transcripts.review_queue(sentence("visit", "Acme", "Cola", "is", "great"), ["Acme Cola"]) == []


def test_a_key_term_with_the_right_letters_but_split_or_joined_differently_is_listed_as_one_stretch():
    rows = transcripts.review_queue(sentence("say", "acme", "co", "la", "now"), ["Acme Cola"])
    assert [(r["word_index"], r["span"], r["text"]) for r in rows] == [(1, 3, "acme co la")]
    assert [(r["word_index"], r["span"]) for r in transcripts.review_queue(sentence("say", "acmecola", "now"), ["Acme Cola"])] == [(1, 1)]
    assert transcripts.review_queue(sentence("so", "acmi", "cola"), ["acmecola"])[0]["span"] == 2
    assert transcripts.review_queue(sentence("so", "ACME", "cola,"), ["Acme Cola"]) == []   # same words, any case or punctuation


def test_short_key_terms_and_words_that_are_not_close_are_left_alone():
    assert transcripts.review_queue(sentence("a", "ai", "zed", "red"), ["AI", "Zed"]) == []
    assert transcripts.review_queue(sentence("banana", "pancake"), ["Acme Cola"]) == []


def test_words_with_no_confidence_are_never_low_and_the_threshold_itself_is_not_low():
    words = [w("a", 0, None), w("b", 1, 0.8), w("c", 2, 0.79)]
    assert [r["word_index"] for r in transcripts.review_queue(words, [])] == [2]
    assert [r["word_index"] for r in transcripts.review_queue(words, [], threshold=0.9)] == [2, 1]   # least sure first


def test_candidate_ranges_move_the_words_inside_them_to_the_front_of_the_queue():
    words = queue_words()
    plain = [r["word_index"] for r in transcripts.review_queue(words, ["Acme Cola"])]
    ranged = [r["word_index"] for r in transcripts.review_queue(words, ["Acme Cola"], ranges=[(4.9, 5.4)])]   # only word 5 ("say")
    assert plain == [2, 9, 5, 1] and ranged == [5, 2, 9, 1]
    assert transcripts.review_queue(words, ["Acme Cola"], ranges=[]) == transcripts.review_queue(words, ["Acme Cola"])


def test_the_queue_is_cut_to_the_limit_and_empty_input_gives_an_empty_queue():
    words = [w(f"w{i}", i, 0.1 + i / 1000) for i in range(30)]
    rows = transcripts.review_queue(words, [], limit=5)
    assert [r["word_index"] for r in rows] == [0, 1, 2, 3, 4]
    assert transcripts.review_queue([], ["Acme"]) == [] and transcripts.review_queue(words, [], limit=0) == []


def test_the_queue_does_not_change_the_words_it_was_given():
    words = queue_words()
    before = [dict(x) for x in words]
    transcripts.review_queue(words, ["Acme Cola"])
    assert words == before


# ------------------------------------------------------------------ saving versions (database)

def test_DW_2_1_editing_a_word_saves_a_new_version(made):
    assert edit(made, 1, {"op": "word", "index": 1, "text": "everyone"}) == 2
    got = current(made)
    assert got["version"] == 2 and texts(got["words"])[:3] == ["Hello", "everyone", "and"] and got["words"][1]["confidence"] == 1.0
    with made.db.session(made.owner) as conn:
        versions = conn.execute("select version_no, edited_by from transcript_versions order by version_no").fetchall()
    assert [v["version_no"] for v in versions] == [1, 2] and versions[0]["edited_by"] is None
    assert str(versions[1]["edited_by"]) == made.owner.operator_id


def test_DW_2_1_replacing_a_phrase_saves_a_new_version(made):
    assert edit(made, 1, {"op": "replace", "find": "acme cola", "replace": "ACME Cola"}) == 2
    assert texts(current(made)["words"])[5:7] == ["ACME", "Cola"]


def test_DW_2_1_renaming_a_speaker_saves_a_new_version(made):
    assert edit(made, 1, {"op": "speaker", "speaker": "1", "name": "Guest"}) == 2
    got = current(made)
    assert got["speakers"] == {"0": "Speaker 1", "1": "Guest"} and got["version"] == 2 and texts(got["words"]) == HELLO


def test_DW_2_1_undo_restores_the_previous_version_as_a_new_version(made):
    first = current(made)
    v2 = edit(made, 1, {"op": "word", "index": 1, "text": "everyone"}, {"op": "speaker", "speaker": "1", "name": "Guest"})
    assert v2 == 2
    assert edit(made, 2, {"op": "revert", "to_version": 1}) == 3
    undone = current(made)
    assert undone["version"] == 3 and undone["words"] == first["words"] and undone["speakers"] == first["speakers"]
    with made.db.session(made.owner) as conn:   # nothing is erased: the history keeps all three
        assert conn.execute("select count(*) as n from transcript_versions").fetchone()["n"] == 3
        assert conn.execute("select count(*) as n from audit_log where action = 'transcript.edit'").fetchone()["n"] == 2


def test_undoing_one_of_several_edits_goes_back_exactly_one_step(made):
    edit(made, 1, {"op": "word", "index": 0, "text": "Hi"})
    edit(made, 2, {"op": "word", "index": 2, "text": "or"})
    edit(made, 3, {"op": "revert", "to_version": 2})
    assert texts(current(made)["words"])[:3] == ["Hi", "there", "and"]


def test_a_revert_to_a_version_that_does_not_exist_is_refused(made):
    with pytest.raises(AppError) as err:
        edit(made, 1, {"op": "revert", "to_version": 9})
    assert err.value.code == "no_version" and current(made)["version"] == 1


def test_an_edit_made_on_an_old_version_is_stale_and_saves_nothing(made):
    edit(made, 1, {"op": "word", "index": 0, "text": "Hi"})
    with pytest.raises(transcripts.StaleTranscript) as err:
        edit(made, 1, {"op": "word", "index": 1, "text": "you"})
    assert err.value.version == 2 and err.value.status == 409 and err.value.extra == {"version": 2}
    assert current(made)["version"] == 2 and texts(current(made)["words"])[1] == "there"


def test_a_big_replace_without_confirmation_saves_nothing_until_confirmed(db, owner):
    made = make_transcript(db, owner, sentence(*["um"] * 101))
    with pytest.raises(transcripts.BulkReplace):
        edit(made, 1, {"op": "replace", "find": "um", "replace": "uh"})
    assert current(made)["version"] == 1
    assert edit(made, 1, {"op": "replace", "find": "um", "replace": "uh"}, confirm=True) == 2


def test_an_invalid_edit_saves_nothing(made):
    with pytest.raises(AppError):
        edit(made, 1, {"op": "word", "index": 0, "text": "ok"}, {"op": "word", "index": 99, "text": "bad"})
    assert current(made)["version"] == 1 and texts(current(made)["words"]) == HELLO


def test_deleting_every_word_is_refused(db, owner):
    made = make_transcript(db, owner, sentence("only"))
    with pytest.raises(AppError) as err:
        edit(made, 1, {"op": "word", "index": 0, "text": ""})
    assert err.value.code == "empty_transcript" and current(made)["version"] == 1


def test_an_unknown_source_is_not_found(db, owner):
    with db.session(owner) as conn:
        with pytest.raises(transcripts.NotFound) as err:
            transcripts.update(conn, owner, "00000000-0000-0000-0000-000000000000", 1, [{"op": "word", "index": 0, "text": "x"}])
    assert err.value.status == 404 and err.value.code == "no_transcript"


def test_the_subtitle_file_follows_the_edit(made):
    edit(made, 1, {"op": "word", "index": 1, "text": "everyone"})
    with made.db.session(made.owner) as conn:
        key = conn.execute("select srt_path from transcript_versions where version_no = 2").fetchone()["srt_path"]
        srt = transcripts.registry.get(conn, "storage").path(key).read_text(encoding="utf-8")
    assert "Hello everyone and" in srt


# ------------------------------------------------------------------ accuracy per provider and dialect

def test_DW_2_3_accuracy_updates_from_corrections_per_provider_and_dialect(db, owner):
    egypt = make_transcript(db, owner, sentence("a", "b", "c", "d"), provider="deepgram", language="ar", language_code="ar-EG", file_hash="1" * 64)
    saudi = make_transcript(db, owner, sentence("a", "b", "c", "d", "e"), provider="deepgram", language="ar", language_code="ar-SA", file_hash="2" * 64)
    local = make_transcript(db, owner, sentence("a", "b", "c", "d"), provider="faster_whisper", language="ar", language_code="ar-EG", file_hash="3" * 64)
    with db.session(owner) as conn:
        assert transcripts.accuracy(conn) == []   # nothing corrected yet: no evidence
    edit(egypt, 1, {"op": "word", "index": 0, "text": "A1"})
    edit(saudi, 1, {"op": "word", "index": 0, "text": "A1"}, {"op": "word", "index": 1, "text": "B1"})
    edit(local, 1, {"op": "word", "index": 0, "text": "A1"}, {"op": "word", "index": 1, "text": "B1"}, {"op": "word", "index": 2, "text": "C1"})
    with db.session(owner) as conn:
        rows = transcripts.accuracy(conn)
    assert rows == [{"provider": "deepgram", "dialect": "ar-EG", "wer": 0.25, "words": 4},
                    {"provider": "faster_whisper", "dialect": "ar-EG", "wer": 0.75, "words": 4},
                    {"provider": "deepgram", "dialect": "ar-SA", "wer": 0.4, "words": 5}]
    edit(egypt, 2, {"op": "word", "index": 1, "text": "B1"})   # a further correction moves the number
    with db.session(owner) as conn:
        assert transcripts.accuracy(conn)[0]["wer"] == 0.5


def test_accuracy_adds_up_the_words_of_every_corrected_transcript_for_the_same_provider_and_dialect(db, owner):
    one = make_transcript(db, owner, sentence("a", "b"), language_code="en", file_hash="1" * 64)
    two = make_transcript(db, owner, sentence("a", "b", "c", "d", "e", "f", "g", "h"), language_code="en", file_hash="2" * 64)
    edit(one, 1, {"op": "word", "index": 0, "text": "X"})
    edit(two, 1, {"op": "word", "index": 0, "text": "X"}, {"op": "word", "index": 1, "text": "Y"})
    with db.session(owner) as conn:
        assert transcripts.accuracy(conn) == [{"provider": "deepgram", "dialect": "en", "wer": 0.3, "words": 10}]


def test_going_back_to_the_providers_reading_counts_as_no_errors_and_a_confirmation_counts_as_reviewed(made):
    edit(made, 1, {"op": "word", "index": 0, "text": "Yo"})
    edit(made, 2, {"op": "revert", "to_version": 1})
    with made.db.session(made.owner) as conn:
        assert transcripts.accuracy(conn) == [{"provider": "deepgram", "dialect": "en", "wer": 0.0, "words": len(HELLO)}]


def test_accuracy_uses_the_dialect_code_then_the_dialect_then_the_language(db, owner):
    plain = make_transcript(db, owner, sentence("a", "b"), language="en", file_hash="1" * 64)
    edit(plain, 1, {"op": "word", "index": 0, "text": "X"})
    with db.session(owner) as conn:
        assert transcripts.accuracy(conn)[0]["dialect"] == "en"
        assert transcripts.load(conn, plain.source_id)["dialect"] == "en"   # the same label the screen shows


def test_accuracy_ignores_other_owners_corrections(db, owner):
    made = make_transcript(db, owner, sentence("a", "b"))
    edit(made, 1, {"op": "word", "index": 0, "text": "X"})
    with db.session(_second_owner(db)) as conn:
        assert transcripts.accuracy(conn) == []


# ------------------------------------------------------------------ key terms

def test_a_key_term_is_added_trimmed_and_a_repeat_in_any_case_changes_nothing(made):
    with made.db.session(made.owner) as conn:
        assert transcripts.add_key_term(conn, made.campaign_id, "  Acme   Cola ") == ["Acme Cola"]
        assert transcripts.add_key_term(conn, made.campaign_id, "acme cola") == ["Acme Cola"]
        assert transcripts.add_key_term(conn, made.campaign_id, "مرحبا") == ["Acme Cola", "مرحبا"]
        assert conn.execute("select key_terms from campaigns").fetchone()["key_terms"] == ["Acme Cola", "مرحبا"]


@pytest.mark.parametrize("term,code", [("", "bad_term"), ("   ", "bad_term"), (None, "bad_term"), (5, "bad_term"), ("x" * 101, "bad_edit")])
def test_a_bad_key_term_is_refused(made, term, code):
    with made.db.session(made.owner) as conn:
        with pytest.raises(AppError) as err:
            transcripts.add_key_term(conn, made.campaign_id, term)
    assert err.value.code == code


def test_a_campaign_has_a_limit_on_key_terms(made):
    with made.db.session(made.owner) as conn:
        conn.execute("update campaigns set key_terms = %s", ([f"t{i}" for i in range(transcripts.MAX_KEY_TERMS)],))
        with pytest.raises(AppError) as err:
            transcripts.add_key_term(conn, made.campaign_id, "one more")
    assert err.value.code == "too_many_terms"


def test_a_key_term_for_an_unknown_campaign_is_not_found(db, owner):
    with db.session(owner) as conn:
        with pytest.raises(transcripts.NotFound):
            transcripts.add_key_term(conn, "00000000-0000-0000-0000-000000000000", "Acme")


def test_the_screens_data_has_the_media_the_campaign_and_the_owners_key_terms(made):
    with made.db.session(made.owner) as conn:
        transcripts.add_key_term(conn, made.campaign_id, "Zed Corp")
        got = transcripts.detail(conn, made.source_id)
    assert got["source_id"] == made.source_id and got["campaign_id"] == made.campaign_id and got["campaign_name"] == "Founders Table"
    assert got["media_url"].startswith("/api/media/sources/x.mp4?exp=") and got["language"] == "en" and got["key_terms"] == ["Zed Corp"]
    assert got["version"] == 1 and got["words"][0]["text"] == "Hello" and set(got) >= {"id", "provider", "dialect", "speakers", "utterances"}


def test_the_queue_for_a_source_uses_the_owners_key_terms_the_campaign_name_and_low_confidence(made):
    with made.db.session(made.owner) as conn:
        before = transcripts.queue_for(conn, made.source_id)
        transcripts.add_key_term(conn, made.campaign_id, "Acme Cola")
        after = transcripts.queue_for(conn, made.source_id)
        capped = transcripts.queue_for(conn, made.source_id, limit=1)
    assert [r["reason"] for r in before] == ["low_confidence"] and before[0]["text"] == "there"
    assert after == before   # "Acme Cola" is spelled right in this transcript
    with made.db.session(made.owner) as conn:
        words = [w("Welcome", 0), w("to", 1), w("acne", 2), w("cola", 3)]
        other = make_transcript(made.db, made.owner, words, file_hash="d" * 64)
        transcripts.add_key_term(conn, other.campaign_id, "Acme Cola")
        rows = transcripts.queue_for(conn, other.source_id)
    assert rows[0]["reason"] == "key_term" and rows[0]["term"] == "Acme Cola" and len(capped) == 1


# ------------------------------------------------------------------ the API

def _second_owner(db) -> Actor:
    with db.session(Actor.system()) as conn:
        row = conn.execute("insert into operators (name, role) values ('Second owner', 'operator') returning id").fetchone()
    return Actor(str(row["id"]), "operator")


@pytest.fixture
def client(made):
    api._owner = made.owner
    return TestClient(api.app)


def test_the_transcript_endpoint_returns_the_load_with_the_screens_extras(made, client):
    body = client.get(f"/api/transcripts/{made.source_id}").json()
    assert body["version"] == 1 and body["provider"] == "deepgram" and len(body["words"]) == 8 and body["campaign_id"] == made.campaign_id
    assert body["speakers"] == {"0": "Speaker 1", "1": "Speaker 2"} and "media_url" in body


def test_DW_2_1_the_put_endpoint_saves_each_kind_of_edit_and_returns_the_version(made, client):
    url = f"/api/transcripts/{made.source_id}"
    assert client.put(url, json={"base_version": 1, "edits": [{"op": "word", "index": 1, "text": "you"}]}).json() == {"version": 2}
    assert client.put(url, json={"base_version": 2, "edits": [{"op": "replace", "find": "acme cola", "replace": "Zed"}]}).json() == {"version": 3}
    assert client.put(url, json={"base_version": 3, "edits": [{"op": "speaker", "speaker": "0", "name": "Host"}]}).json() == {"version": 4}
    assert client.put(url, json={"base_version": 4, "edits": [{"op": "revert", "to_version": 3}]}).json() == {"version": 5}
    body = client.get(url).json()
    assert body["version"] == 5 and body["speakers"]["0"] == "Speaker 1" and texts(body["words"])[1] == "you"


def test_a_stale_put_answers_409_with_the_current_version(made, client):
    url = f"/api/transcripts/{made.source_id}"
    client.put(url, json={"base_version": 1, "edits": [{"op": "word", "index": 0, "text": "Hi"}]})
    answer = client.put(url, json={"base_version": 1, "edits": [{"op": "word", "index": 0, "text": "Yo"}]})
    assert answer.status_code == 409 and answer.json()["code"] == "stale" and answer.json()["version"] == 2


def test_a_big_replace_answers_409_until_confirm_bulk_is_sent(db, owner):
    made = make_transcript(db, owner, sentence(*["um"] * 101))
    api._owner = owner
    client = TestClient(api.app)
    body = {"base_version": 1, "edits": [{"op": "replace", "find": "um", "replace": "uh"}]}
    refused = client.put(f"/api/transcripts/{made.source_id}", json=body)
    assert refused.status_code == 409 and refused.json()["code"] == "confirm_bulk" and refused.json()["words"] == 101
    assert client.put(f"/api/transcripts/{made.source_id}", json={**body, "confirm_bulk": True}).json() == {"version": 2}


def test_a_bad_edit_answers_400_with_a_plain_message(made, client):
    answer = client.put(f"/api/transcripts/{made.source_id}", json={"base_version": 1, "edits": [{"op": "word", "index": 99, "text": "x"}]})
    assert answer.status_code == 400 and answer.json()["code"] == "bad_edit" and "no longer" in answer.json()["error"]


@pytest.mark.parametrize("body", [{"base_version": "1", "edits": [{"op": "word", "index": 0, "text": "x"}]},
                                  {"base_version": True, "edits": []}, {"base_version": 0, "edits": []}, {"edits": []},
                                  {"base_version": 1, "edits": "word"}])
def test_a_malformed_put_body_is_refused_before_anything_is_saved(made, client, body):
    assert client.put(f"/api/transcripts/{made.source_id}", json=body).status_code == 422
    assert current(made)["version"] == 1


def test_DW_2_2_the_review_queue_endpoint_lists_the_words_to_fix(made, client):
    rows = client.get(f"/api/transcripts/{made.source_id}/review-queue").json()
    assert rows == [{"word_index": 1, "text": "there", "confidence": 0.4, "reason": "low_confidence"}]
    assert client.get(f"/api/transcripts/{made.source_id}/review-queue?limit=0").json() == rows   # the limit stays at least 1


def test_DW_2_3_the_accuracy_endpoint_is_not_taken_for_a_transcript_id(made, client):
    assert client.get("/api/transcripts/accuracy").json() == []
    client.put(f"/api/transcripts/{made.source_id}", json={"base_version": 1, "edits": [{"op": "word", "index": 0, "text": "Hi"}]})
    assert client.get("/api/transcripts/accuracy").json() == [{"provider": "deepgram", "dialect": "en", "wer": 0.125, "words": 8}]


def test_the_key_term_endpoint_adds_a_term_and_refuses_a_blank_one(made, client):
    url = f"/api/campaigns/{made.campaign_id}/key-terms"
    assert client.post(url, json={"term": "Acme Cola"}).json() == {"key_terms": ["Acme Cola"]}
    assert client.post(url, json={"term": "  "}).status_code == 400
    assert client.post("/api/campaigns/00000000-0000-0000-0000-000000000000/key-terms", json={"term": "x"}).status_code == 404


def test_DW_2_4_another_owners_transcript_returns_404_for_every_route(made):
    api._owner = _second_owner(made.db)
    client = TestClient(api.app)
    url = f"/api/transcripts/{made.source_id}"
    assert client.get(url).status_code == 404 and client.get(url + "/review-queue").status_code == 404
    answer = client.put(url, json={"base_version": 1, "edits": [{"op": "word", "index": 0, "text": "stolen"}]})
    assert answer.status_code == 404 and answer.json()["code"] == "no_transcript"
    assert client.post(f"/api/campaigns/{made.campaign_id}/key-terms", json={"term": "stolen"}).status_code == 404
    assert client.get("/api/transcripts/accuracy").json() == []
    assert current(made)["version"] == 1 and texts(current(made)["words"]) == HELLO   # nothing was written


def test_the_clip_in_review_names_its_source_so_the_screen_can_link_to_its_transcript(produced):
    from clipping import review

    with produced.db.session(produced.owner) as conn:
        clip = review.feed(conn, produced.owner.operator_id)[0]
    assert str(clip["source_id"]) == produced.source_id


def test_the_migration_adds_the_error_counts_without_a_new_table(db):
    with db.session(Actor.system()) as conn:
        columns = {r["column_name"] for r in conn.execute("select column_name from information_schema.columns where table_name = 'transcripts'").fetchall()}
    assert {"wer_errors", "wer_words"} <= columns
