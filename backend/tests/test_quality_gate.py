"""Quality gate v1: each check, the verdict, and a failed clip sinking in the feed."""
import datetime as dt

from clipping import quality_gate as g, review

WORDS = [{"text": "Hello", "start": 10.0, "end": 10.4}, {"text": "there", "start": 10.5, "end": 10.9}, {"text": "friend.", "start": 11.0, "end": 11.5}]


def test_speech_density_counts_words_inside_the_window():
    assert g.words_per_second(WORDS, 10.0, 12.0) == 1.5
    assert g.evaluate({"talking": True, "words_per_s": 0.3})["status"] == "fail"
    assert g.evaluate({"talking": False, "words_per_s": 0.3})["status"] == "pass"       # a gaming clip may be quiet


def test_face_share_warns_below_80_and_fails_below_50():
    base = {"expects_face": True}
    assert g.evaluate({**base, "face_share": 0.9})["status"] == "pass"
    assert g.evaluate({**base, "face_share": 0.7})["status"] == "warn"
    assert g.evaluate({**base, "face_share": 0.3})["status"] == "fail"
    assert g.evaluate({"expects_face": False, "face_share": 0.0})["status"] == "pass"


def test_sentence_end_and_word_start():
    assert g.sentence_end_ok(WORDS, 10.0, 11.6) and not g.sentence_end_ok(WORDS[:2], 10.0, 11.0)
    assert g.word_start_ok(WORDS, 10.0) and not g.word_start_ok(WORDS, 10.2)
    assert g.evaluate({"sentence_end": False})["status"] == "warn"


def test_loudness_language_and_disclosure():
    assert g.evaluate({"lufs": -14.0})["status"] == "pass" and g.evaluate({"lufs": -30.0})["status"] == "warn"
    assert g.evaluate({"detected_language": "en", "source_language": "ar"})["status"] == "fail"
    assert g.evaluate({"detected_language": None, "source_language": "ar"})["status"] == "pass"
    assert g.disclosure_first("#ad Check this", "#ad") and not g.disclosure_first("Check this #ad", "#ad")
    assert g.evaluate({"disclosure_first": False})["status"] == "fail"


def test_reasons_are_plain_in_both_languages_and_fails_come_first():
    result = g.evaluate({"talking": True, "words_per_s": 0.2, "lufs": -30.0})
    assert [r["level"] for r in result["reasons"]] == ["fail", "warn"]
    assert all(r["en"] and r["ar"] for r in result["reasons"]) and "0.2" in result["reasons"][0]["en"]


def test_a_failed_gate_sinks_the_clip_below_the_rest():
    now = dt.datetime.now(dt.timezone.utc)
    mk = lambda i, status, score: {"id": i, "compliance_status": "pass", "created_at": now, "priority_score": score, "render_meta": {"gate": {"status": status}}}
    clips = [mk(1, "fail", 0.9), mk(2, "pass", 0.1), mk(3, "warn", 0.2)]
    weak_first = sorted(clips, key=lambda c: (bool((c.get("quality") or {}).get("weak")) or c["render_meta"]["gate"]["status"] == "fail", -c["priority_score"]))
    assert [c["id"] for c in weak_first] == [3, 2, 1]
