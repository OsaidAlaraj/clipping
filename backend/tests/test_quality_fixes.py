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
