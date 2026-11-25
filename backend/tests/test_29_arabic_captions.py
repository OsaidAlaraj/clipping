"""Arabic captions: word order under per-word colour tags, lone punctuation and cut-off articles from the transcript, dialect labels."""
from __future__ import annotations

import re
import subprocess

import numpy as np
import pytest

from clipping import ffmpeg
from clipping.adapters import media, transcription
from clipping.caption_text import repair_stray_tokens


def _w(text, start, end, speaker=None, confidence=0.9):
    return {"text": text, "start": start, "end": end, "speaker": speaker, "confidence": confidence}


# ------------------------------------------------------------------ stray tokens

def test_lone_punctuation_joins_the_word_before_it_and_keeps_that_words_time():
    words = [_w("يحمي", 1.04, 1.92), _w("،", 2.16, 2.48), _w("،", 2.48, 2.56), _w("يبعد", 2.56, 3.2)]
    out = repair_stray_tokens(words)
    assert [w["text"] for w in out] == ["يحمي،،", "يبعد"]
    assert (out[0]["start"], out[0]["end"]) == (1.04, 1.92) and out[1]["start"] == 2.56


def test_punctuation_before_any_word_is_dropped():
    assert [w["text"] for w in repair_stray_tokens([_w("،", 0, 0.1), _w("كلمة", 0.2, 0.5)])] == ["كلمة"]


def test_a_cut_off_article_joins_its_noun_with_the_span_of_both():
    out = repair_stray_tokens([_w("أسباب", 14.0, 15.1), _w("ال", 15.165, 15.325, "0", 0.41), _w("خبراء", 15.325, 15.9, "0", 0.8)])
    assert [w["text"] for w in out] == ["أسباب", "الخبراء"]
    assert (out[1]["start"], out[1]["end"], out[1]["confidence"]) == (15.165, 15.9, 0.41)


def test_an_article_does_not_join_across_a_speaker_change_or_a_long_pause_and_real_words_are_untouched():
    assert len(repair_stray_tokens([_w("ال", 1, 1.2, "0"), _w("خبراء", 1.2, 1.8, "1")])) == 2
    assert len(repair_stray_tokens([_w("ال", 1, 1.2), _w("خبراء", 3.0, 3.8)])) == 2
    same = [_w("من", 1, 1.2), _w("العرض", 1.2, 1.8)]
    assert repair_stray_tokens(same) == same


def test_repair_does_not_change_its_input():
    words = [_w("يحمي", 1, 2), _w("،", 2, 2.1)]
    repair_stray_tokens(words)
    assert words[0]["text"] == "يحمي" and len(words) == 2


def test_a_lone_comma_no_longer_takes_a_place_in_a_caption_line_or_starts_it_early():
    words = [_w("بحيث", 0.48, 1.04), _w("يحمي", 1.04, 1.92), _w("،", 2.16, 2.48), _w("،", 2.48, 2.56), _w("يبعد", 2.56, 3.2), _w("المتهم", 3.76, 4.4)]
    chunks = media.caption_chunks(words, 0, 10, 3, 2.5)
    assert [c["text"] for c in chunks] == ["بحيث يحمي،،", "يبعد المتهم"]
    assert chunks[1]["start"] == 2.56      # it used to start at 2.16, on the comma, before "يبعد" was spoken


# ------------------------------------------------------------------ dialect labels

@pytest.mark.parametrize("label,code", [("Jordanian", "ar-JO"), ("Levantine Arabic", "ar-JO"), ("palestinian", "ar-PS"), ("Egyptian", "ar-EG"),
                                        ("ar-JO", "ar-JO"), ("ar", "ar"), ("Gulf", "ar"), (None, "ar")])
def test_a_dialect_typed_in_words_pins_the_region(label, code):
    assert transcription.resolve_language_code("ar", label) == code


def test_a_dialect_word_never_changes_an_english_request():
    assert transcription.resolve_language_code("en", "Jordanian") == "en"


# ------------------------------------------------------------------ the ASS script

def _styles(ass: str) -> dict[str, str]:
    return {line.split(",")[0].removeprefix("Style: "): line for line in ass.splitlines() if line.startswith("Style: ")}


def test_every_text_style_detects_its_paragraph_direction():
    ass = media.build_ass(1080, 1920, 10, [], "AI", True, {}, "هل هذا مهم؟")
    for name in ("Caption", "Label", "Title"):
        assert _styles(ass)[name].rsplit(",", 1)[1] == "-1", name
    cover = media.build_cover_ass(1080, 1920, "هل هذا مهم؟", {})
    assert _styles(cover)["Cover"].rsplit(",", 1)[1] == "-1"


# ------------------------------------------------------------------ what libass really draws

def _frame(tmp_path, text: str) -> np.ndarray:
    """One frame of a 540x960 black picture with a single Caption event."""
    ass = media.build_ass(540, 960, 2, [{"start": 0.0, "end": 1.5, "text": text}], "AI", True, {"title_card": False, "progress_bar": False})
    path = tmp_path / "t.ass"
    path.write_text(ass, encoding="utf-8-sig")
    out = tmp_path / "t.png"
    subprocess.run([ffmpeg.exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=540x960:d=1", "-vf",
                    "ass=t.ass", "-frames:v", "1", str(out)], cwd=tmp_path, check=True)
    from PIL import Image
    return np.asarray(Image.open(out).convert("RGB")).astype(int)


@pytest.mark.parametrize("k", [0, 1, 2])
def test_the_first_spoken_word_is_drawn_right_of_the_second_whichever_word_is_highlighted(tmp_path, k):
    words = ["المتهم", "من", "العرض"]
    events = media.karaoke_events([_w(t, i * 0.4, i * 0.4 + 0.3) for i, t in enumerate(words)], 0, 2, False, "#FF0000", 540, 960, 40, True)
    # the event where word k is the highlighted one; the highlight fades in over 70 ms, so look at the end state with a plain red tag
    text = re.sub(r"\\t\(0,70,([^)]*)\)", r"\1", events[k]["text"]).replace("\\fad(80,0)", "")
    img = _frame(tmp_path, text)
    red = (img[..., 0] > 200) & (img[..., 1] < 60) & (img[..., 2] < 60)
    white = (img.min(axis=2) > 200)
    assert red.any() and white.any()
    xs_red, xs_white = np.nonzero(red)[1], np.nonzero(white)[1]
    # words 1..3 run right to left: the highlighted word's place among the other two is fixed by its number
    if k == 0:
        assert xs_red.mean() > xs_white.mean()        # first word: right of both others
    elif k == 2:
        assert xs_red.mean() < xs_white.mean()        # last word: left of both others
    else:
        assert xs_white.min() < xs_red.mean() < xs_white.max()    # middle word: between them
