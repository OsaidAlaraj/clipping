"""Language check on thin transcripts and the sentence-end snap for clip ends."""
from types import SimpleNamespace

from clipping import language_check, moment_rank


def test_a_long_video_with_almost_no_words_is_flagged_and_a_normal_one_is_not():
    assert language_check.empty_transcript_message([{}] * 40, 6540, "ar") is not None
    assert language_check.empty_transcript_message([{}] * 18000, 6540, "en") is None
    assert language_check.empty_transcript_message([], 60, "en") is None          # short clips may be quiet


def _episode(texts):
    lines, t = [], 0.0
    for x in texts:
        lines.append({"start": t, "end": t + 8.0, "text": x}); t += 8.0
    return SimpleNamespace(lines=lines, line_ends=[l["end"] for l in lines])


def test_the_end_snaps_back_to_a_sentence_end_when_one_is_close():
    ep = _episode(["Intro here.", "More talk.", "and then he said that", "it was never"])
    assert moment_rank.sentence_end(ep, ep.lines[0], ep.lines[3]) is ep.lines[1]


def test_the_end_is_left_alone_when_no_sentence_end_keeps_the_clip_long_enough():
    ep = _episode(["Short.", "and then", "it was never"])
    assert moment_rank.sentence_end(ep, ep.lines[0], ep.lines[2]) is ep.lines[2]
    assert moment_rank.sentence_end(ep, ep.lines[0], ep.lines[0]) is ep.lines[0]


# ------------------------------------------------------------------ wrong language: warn at upload, retry once after a thin transcript

from decimal import Decimal  # noqa: E402

from clipping import pipeline  # noqa: E402


def test_the_upload_warning_names_both_languages_and_stays_quiet_when_they_agree():
    assert "'en'" in language_check.mismatch_message("ar", "en") and "'ar'" in language_check.mismatch_message("ar", "en")
    assert language_check.mismatch_message("en", "en") is None
    assert language_check.mismatch_message("en", None) is None
    assert language_check.mismatch_message("en", "fr") is None       # only en and ar are supported


class _Conn:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))


def _run_retry(monkeypatch, detected, first_words, second_words):
    calls, alerts = [], []
    monkeypatch.setattr(language_check, "detect_language", lambda *a, **k: detected)
    monkeypatch.setattr(pipeline, "raise_alert", lambda *a, **k: alerts.append(a[2]))
    monkeypatch.setattr(pipeline, "audit", lambda *a, **k: None)

    def fake_call(conn, stage, method, request, owner, provider=None):
        calls.append(request["language"])
        return {"words": [{}] * second_words, "language_code": request["language"]}, Decimal("0.5")

    monkeypatch.setattr(pipeline.registry, "call", fake_call)
    source = {"id": 1, "language": "ar", "dialect": "jordanian"}
    first = ({"words": [{}] * first_words}, Decimal("0.5"))
    result = pipeline._retry_if_thin(_Conn(), source, {"name": "C"}, "v.mp4", {"duration_s": 300.0, "language": "ar"}, first, None)
    return result, calls, alerts, source


def test_a_thin_transcript_is_retried_once_in_the_detected_language(monkeypatch):
    (transcript, cost), calls, alerts, source = _run_retry(monkeypatch, "en", 12, 850)
    assert calls == ["en"] and len(transcript["words"]) == 850 and cost == Decimal("1.0")
    assert alerts == ["transcript_too_thin"] and source["language"] == "en" and source["dialect"] is None


def test_no_retry_when_the_transcript_is_fine_or_the_detection_agrees_or_is_unsure(monkeypatch):
    for detected, first in (("en", 900), ("ar", 12), (None, 12)):
        (transcript, _), calls, _, source = _run_retry(monkeypatch, detected, first, 850)
        assert calls == [] and len(transcript["words"]) == first and source["language"] == "ar"


def test_a_retry_that_is_not_better_keeps_the_first_transcript_and_the_language(monkeypatch):
    (transcript, cost), calls, _, source = _run_retry(monkeypatch, "en", 12, 5)
    assert calls == ["en"] and len(transcript["words"]) == 12 and cost == Decimal("1.0") and source["language"] == "ar"
