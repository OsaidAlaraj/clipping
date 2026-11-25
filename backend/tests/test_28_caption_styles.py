"""Named caption styles: safe areas, motion rules, speaker labels, Arabic handling and template validation."""
from __future__ import annotations

import re

import pytest

from clipping.adapters import caption_render as cr
from clipping.adapters import media
from clipping.core import AppError


def _words(texts, speakers=None, step=0.4):
    return [{"text": t, "start": i * step, "end": i * step + 0.35, "speaker": (speakers[i] if speakers else None)} for i, t in enumerate(texts)]


EN = _words("this is the one thing nobody tells you about making 100 dollars online".split())


@pytest.mark.parametrize("name", list(cr.STYLES))
def test_every_style_keeps_text_inside_the_safe_area(name):
    for platform in ("tiktok", "instagram_reels", "youtube_shorts"):
        events = cr.style_events(name, EN, 0, 6, True, "#FFC400", 1080, 1920, 70, False, platform)
        assert events
        left, top, right, bottom = cr.safe_box(platform, 1080, 1920)
        for e in events:
            pos = re.search(r"\\pos\((\d+),(\d+)\)", e["text"])
            x, y = int(pos[1]), int(pos[2])
            assert left < x < right + 1 and top < y < bottom


def test_a_line_pops_in_once_and_then_holds_still():
    events = cr.style_events("bold_pop", EN, 0, 6, True, "#FFC400", 1080, 1920, 70, False)
    first_of_line = [e for e in events if r"\t(0,110" in e["text"]]
    others = [e for e in events if r"\t(0,110" not in e["text"]]
    assert first_of_line and others                      # the pop is on the first word's event only
    assert all(r"\fscx" not in e["text"] for e in others)  # the words that follow do not move the line
    assert "THIS" in events[0]["text"]                   # Latin text is upper-cased by this style


def test_arabic_is_never_upper_cased_or_spaced():
    ar = _words("هاد الشي اللي ما حدا بقلك عنه".split())
    events = cr.style_events("bold_pop", ar, 0, 4, True, "#FFC400", 1080, 1920, 70, True)
    assert events and all(r"\fsp" not in e["text"] for e in events)
    assert "هاد" in events[0]["text"]
    assert cr.resolve("auto", "gaming", "ar") == "karaoke_clean"     # no loud capital style for Arabic by default


def test_stacked_breaks_into_two_lines_and_emphasis_marks_the_number():
    stacked = cr.style_events("stacked", EN, 0, 6, True, "#FFC400", 1080, 1920, 70, False)
    assert "\\N" in stacked[0]["text"]
    emph = cr.style_events("emphasis", EN, 0, 6, True, "#FFC400", 1080, 1920, 70, False)
    assert any("&H0000C4FF&}100" in e["text"] for e in emph)


def test_speaker_chips_only_appear_with_two_or_more_speakers():
    one = _words("a b c d e f".split(), ["0"] * 6)
    assert not [e for e in cr.style_events("speaker_label", one, 0, 3, True, "#FFC400", 1080, 1920, 70, False) if e.get("style") == "Speaker"]
    two = _words("a b c d e f g h".split(), ["0", "0", "0", "0", "1", "1", "1", "1"])
    chips = [e for e in cr.style_events("speaker_label", two, 0, 4, True, "#FFC400", 1080, 1920, 70, False, names={"0": "Sara", "1": "Omar"})
             if e.get("style") == "Speaker"]
    assert [("Sara" in c["text"]) for c in chips] == [True, False] and "Omar" in chips[1]["text"]


def test_the_style_reaches_the_ass_script_and_is_validated():
    spec = media.normalize_template({"caption_style": "bold_pop"}, strict=True)
    assert spec["caption_style"] == "bold_pop" and media.normalize_template({})["caption_style"] == "auto"
    with pytest.raises(AppError):
        media.normalize_template({"caption_style": "wobbly"}, strict=True)
    ass = media.build_ass(1080, 1920, 6, cr.style_events("speaker_label", EN, 0, 6, True, "#FFC400", 1080, 1920, 70, False), None, False, spec, None, "bold_pop")
    assert "Style: Speaker," in ass and "Style: Caption," in ass


def test_auto_picks_by_niche():
    assert cr.resolve("auto", "gaming") == "bold_pop" and cr.resolve("auto", "podcast") == "speaker_label"
    assert cr.resolve("minimal", "gaming") == "minimal"
