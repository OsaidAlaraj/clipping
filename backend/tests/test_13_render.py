"""Phase D step 5: one encode per clip, the originality layer (templates), the Arabic label, covers and the creative library.
FFmpeg is real; the database is the embedded one; no vendor is called."""
from __future__ import annotations

import datetime as dt
import re
import struct
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from clipping import alerts, api, ffmpeg, pipeline, templates
from clipping.adapters import media
from clipping.core import AppError
from clipping.db import Actor
from conftest import add_accounts, approved_campaign  # noqa: F401  (fixtures and helpers)

W, H = 270, 480
CFG = {"width": W, "height": H, "preset": "ultrafast", "crf": 30, "encoder": "x264"}
WORDS = [{"text": t, "start": 1.0 + i * 0.5, "end": 1.4 + i * 0.5} for i, t in
         enumerate("this is a test of the 100 caption words, with punctuation. Another line here".split())]


# ------------------------------------------------------------------ helpers

def adapter(cls=media.FFmpegRender, **cfg) -> media.FFmpegRender:
    return cls({"config_json": {**CFG, **cfg}})


def clip_for(start=0.0, end=8.0, **extra) -> dict:
    return {"start_s": start, "end_s": end, "language": "en", "platform": "tiktok", "crop": "centre", "audio": "speech",
            "hook_text": None, "emphasis": [], **extra}


def flat_source(tmp_path: Path, size="640x360", seconds=8) -> Path:
    """A uniform grey picture with a tone: any pixel that is not grey comes from the overlay."""
    out = tmp_path / "flat.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", f"color=c=0x808080:s={size}:r=25", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100",
                "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)])
    return out


def person_source(tmp_path: Path, x: int, seconds=4) -> Path:
    """A grey frame with one skin-toned block at column x (a stand-in for a person)."""
    out = tmp_path / f"person{x}.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=0x808080:s=640x360:r=25", "-t", str(seconds),
                "-vf", f"drawbox=x={x}:y=100:w=100:h=160:color=0xC8785A:t=fill", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)])
    return out


def frame(path: Path, at: float, size=(W, H)) -> np.ndarray:
    raw = ffmpeg.frame_raw(path, at, size[0], size[1])
    assert raw is not None, f"no frame at {at}"
    return np.frombuffer(raw, dtype=np.uint8).reshape(size[1], size[0], 3).astype(np.int16)


def deviation(region: np.ndarray) -> float:
    """How far a region is from the source's uniform grey."""
    return float(np.abs(region - 128).mean())


def render(tmp_path: Path, src: Path, clip: dict, template: dict | None = None, label="#ad", words=None, name="out", **cfg) -> dict:
    folder = tmp_path / name   # every render has its own folder: the clip's ASS and cover files sit next to it
    folder.mkdir(exist_ok=True)
    return adapter(**cfg).render_clip(str(src), clip, WORDS if words is None else words, template or {}, label, str(folder / f"{name}.mp4"))


class Spy:
    """Records every FFmpeg call so the number of video encodes can be asserted from the calls themselves."""

    def __init__(self, monkeypatch, fail_nvenc=False):
        self.calls: list[list[str]] = []
        real = ffmpeg.run

        def run(args, *a, **kw):
            self.calls.append(list(args))
            if fail_nvenc and "h264_nvenc" in args:
                raise ffmpeg.FFmpegError("OpenEncodeSessionEx failed: out of memory (nvenc busy)")
            return real(args, *a, **kw)

        monkeypatch.setattr(ffmpeg, "run", run)

    @property
    def encodes(self) -> list[list[str]]:
        return [c for c in self.calls if "-c:v" in c and c[c.index("-c:v") + 1] in ("libx264", "h264_nvenc")]


# ------------------------------------------------------------------ DW-5.1: one encode

def test_DW_5_1_a_clip_is_one_encode_with_captions_and_disclosure_burned_in(tmp_path, footage, monkeypatch):
    spy = Spy(monkeypatch)
    result = render(tmp_path, Path(footage), clip_for(2.0, 12.0, hook_text="A hook for the title card"), {"tighten": False}, words=WORDS)
    assert result["encodes"] == 1 and len(spy.encodes) == 1               # exactly one encode, counted from the FFmpeg calls
    graph = spy.encodes[0][spy.encodes[0].index("-filter_complex") + 1]
    assert "ass=out.ass" in graph                                          # captions, label and title card ride in that encode
    ass = Path(result["ass_path"]).read_text(encoding="utf-8-sig")
    assert "Label,,0,0,0,,#ad" in ass and "Dialogue: 3," in ass and "with punctuation" in re.sub(r"\{[^}]*\}", "", ass)
    info = ffmpeg.probe(Path(result["path"]))
    assert info["has_audio"] and abs(info["duration_s"] - 10.0) < 0.3 and (info["width"], info["height"]) == (W, H)
    assert result["overlay"] == {"text": "#ad", "start_s": 0.0, "end_s": info["duration_s"]}


def test_a_blank_disclosure_label_is_refused_before_any_ffmpeg_work(tmp_path, footage, monkeypatch):
    spy = Spy(monkeypatch)
    with pytest.raises(ValueError):
        render(tmp_path, Path(footage), clip_for(), label="  ")
    assert spy.calls == []


def test_every_clip_of_a_full_pipeline_run_is_exactly_one_encode(db, owner, footage, monkeypatch):
    from clipping import campaigns

    add_accounts(db, owner)
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        campaigns.register_source(conn, owner, campaign_id, footage, "en", permission_ref="P-1")
    spy = Spy(monkeypatch)
    pipeline.run_until_idle(db)
    with db.session(owner) as conn:
        clips = conn.execute("select render_meta, cover_path, file_path from clips").fetchall()
        types = [j["type"] for j in conn.execute("select type from jobs").fetchall()]
    assert len(clips) == 3 and len(spy.encodes) == 3                       # one encode per clip, nothing else encodes video
    assert types.count("render") == 3 and "captions" not in types
    for clip in clips:
        assert clip["render_meta"]["encodes"] == 1 and clip["render_meta"]["encoder"] in ("x264", "nvenc")
        assert clip["render_meta"]["overlay_burned"] is True and clip["cover_path"] == f"{clip['file_path'].rsplit('/', 1)[0]}/cover.png"


# ------------------------------------------------------------------ DW-5.2: the Arabic label top right

def top_strip(video: Path) -> tuple[float, float]:
    """Deviation from the grey source in the left and right halves of the top strip, where the label sits."""
    strip = frame(video, 1.0)[: int(H * 0.12)]
    return deviation(strip[:, : W // 2]), deviation(strip[:, W // 2:])


def test_DW_5_2_arabic_disclosure_sits_top_right_and_english_stays_top_left(tmp_path):
    src = flat_source(tmp_path)
    en = render(tmp_path, src, clip_for(language="en"), label="#ad", words=[], name="en")
    ar = render(tmp_path, src, clip_for(language="ar"), label="#إعلان", words=[], name="ar")
    en_left, en_right = top_strip(Path(en["path"]))
    ar_left, ar_right = top_strip(Path(ar["path"]))
    assert en_left > 3 and en_right < 1          # English: top left, as it was
    assert ar_right > 3 and ar_left < 1          # Arabic: top right
    assert "Label,Arial,12,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,3,2,0,9," in Path(ar["ass_path"]).read_text(encoding="utf-8-sig")
    assert ",3,2,0,7," in Path(en["ass_path"]).read_text(encoding="utf-8-sig")


def test_the_label_runs_for_the_whole_clip_in_both_languages(tmp_path):
    src = flat_source(tmp_path)
    for language, text in (("en", "#ad"), ("ar", "#إعلان")):
        result = render(tmp_path, src, clip_for(language=language), label=text, words=[], name=language)
        side = 0 if language == "en" else 1
        for at in (0.2, 4.0, result["duration_s"] - 0.3):
            strip = frame(Path(result["path"]), at)[: int(H * 0.12)]
            half = strip[:, : W // 2] if side == 0 else strip[:, W // 2:]
            assert deviation(half) > 3, f"{language} label missing at {at:.1f}s"


# ------------------------------------------------------------------ DW-5.3: templates change the frame

def test_DW_5_3_two_templates_give_measurably_different_frames(tmp_path, footage):
    clip = clip_for(2.0, 12.0, hook_text="Why nobody says this out loud", emphasis=[])
    a = {"font": "Arial", "primary": "#FFFFFF", "accent": "#FFC400", "caption_position": "lower", "progress_bar": True, "title_card": True}
    b = {"font": "Tahoma", "primary": "#F2FFF8", "accent": "#2EE59D", "caption_position": "middle", "progress_bar": False, "title_card": False}
    one, again, two = (render(tmp_path, Path(footage), clip, t, name=n) for t, n in ((a, "a1"), (a, "a2"), (b, "b")))
    frames = [frame(Path(r["path"]), 2.2) for r in (one, again, two)]
    same, different = (float(np.abs(frames[0] - f).mean()) for f in (frames[1], frames[2]))
    assert same < 1.0                      # the same template renders the same frame
    assert different > 3.0 and different > 10 * max(same, 0.1)


def test_the_progress_bar_grows_across_the_clip_and_switches_off(tmp_path):
    src = flat_source(tmp_path)
    on = Path(render(tmp_path, src, clip_for(), {"progress_bar": True, "accent": "#FF0000"}, words=[], name="on")["path"])
    off = Path(render(tmp_path, src, clip_for(), {"progress_bar": False}, words=[], name="off")["path"])

    def bar(path: Path, at: float) -> float:
        rows = frame(path, at)[-2:]
        return float(((rows[..., 0] > 170) & (rows[..., 1] < 110)).mean())   # the share of the bottom rows that is the red bar

    assert bar(on, 1.0) < 0.3 < 0.4 < bar(on, 4.0) < 0.6 < 0.8 < bar(on, 7.5)
    assert bar(off, 7.5) == 0.0


def test_the_title_card_shows_the_hook_for_the_first_seconds_only(tmp_path):
    src = flat_source(tmp_path)
    clip = clip_for(hook_text="The hook")
    on = Path(render(tmp_path, src, clip, {"title_card": True}, words=[], name="on")["path"])
    off = Path(render(tmp_path, src, clip, {"title_card": False}, words=[], name="off")["path"])
    zone = lambda path, at: deviation(frame(path, at)[int(H * 0.08): int(H * 0.25)])  # noqa: E731
    assert zone(on, 1.0) > 5 and zone(on, 6.0) < 2 and zone(off, 1.0) < 2


def test_emphasis_zooms_push_the_picture_in_on_loud_moments_and_can_be_off(tmp_path, footage):
    clip = clip_for(0.0, 10.0, emphasis=[5.5])
    on = render(tmp_path, Path(footage), clip, {"zooms": True, "progress_bar": False}, words=[], name="on")
    off = render(tmp_path, Path(footage), clip, {"zooms": False, "progress_bar": False}, words=[], name="off")
    assert on["zooms"] == 1 and off["zooms"] == 0
    peak = float(np.abs(frame(Path(on["path"]), 5.5) - frame(Path(off["path"]), 5.5)).mean())
    away = float(np.abs(frame(Path(on["path"]), 2.0) - frame(Path(off["path"]), 2.0)).mean())
    assert peak > 3 * max(away, 0.3)


# ------------------------------------------------------------------ DW-5.4: the cover

def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_DW_5_4_each_clip_has_a_cover_png_with_the_hook_text(tmp_path, footage):
    clip = clip_for(2.0, 12.0, hook_text="The one mistake that quietly costs you everything")
    with_hook = render(tmp_path, Path(footage), clip, name="with")
    without = render(tmp_path, Path(footage), {**clip, "hook_text": None}, name="without")
    cover, plain = Path(with_hook["cover_path"]), Path(without["cover_path"])
    assert png_size(cover) == (W, H) and cover.name == "cover.png"
    assert "quietly costs you" in cover.with_suffix(".ass").read_text(encoding="utf-8-sig")
    lower = lambda path: frame_png(path)[int(H * 0.6): int(H * 0.9)]  # noqa: E731
    assert float(np.abs(lower(cover) - lower(plain)).mean()) > 5                      # the hook line is drawn into the picture
    assert float(np.abs(frame_png(cover)[: int(H * 0.3)] - frame_png(plain)[: int(H * 0.3)]).mean()) < 1.0   # and only there


def frame_png(path: Path) -> np.ndarray:
    return frame(path, 0.0)


def test_a_cover_failure_leaves_the_clip_without_one_and_never_blocks_it(tmp_path, footage, monkeypatch):
    real = ffmpeg.run

    def run(args, *a, **kw):
        if "-frames:v" in args:
            raise ffmpeg.FFmpegError("no cover for you")
        return real(args, *a, **kw)

    monkeypatch.setattr(ffmpeg, "run", run)
    result = render(tmp_path, Path(footage), clip_for(hook_text="x"))
    assert result["cover_path"] is None and result["encodes"] == 1 and Path(result["path"]).exists()


def test_cover_times_prefer_a_face_laughter_and_no_scene_cut_in_the_middle_of_the_clip():
    face = [0.0, 1.0, 1.0, 0.2, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    sig = {"first_s": 0, "face": face, "laughter": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0], "scene": [0, 0, 1, 0, 0, 0, 0, 0, 0, 0],
           "loudness": [-30] * 10}
    times = media.cover_times({"start_s": 0, "end_s": 10, "signals": sig})
    assert times == [4.5, 1.5, 2.5]                             # face and laughter first; the face on a scene cut is last
    assert all(1.0 <= t <= 9.0 for t in times)                  # never the first or the last tenth
    assert media.cover_times({"start_s": 10, "end_s": 20}) == [13.5, 15.0, 16.5]   # no signals: spread-out guesses


def test_the_cover_frame_is_the_sharpest_candidate(tmp_path):
    out = tmp_path / "two.mp4"   # sharp test pattern for 2 s, then the same picture blurred
    ffmpeg.run(["-f", "lavfi", "-i", "testsrc2=size=320x568:rate=25", "-t", "4", "-vf", "boxblur=8:2:enable='gte(t,2)'", "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)])
    assert media.sharpest_time(out, [3.0, 1.0, 3.5]) == 1.0
    sharp, blurred = (ffmpeg.frame_raw(out, at, 160, 284, "gray") for at in (1.0, 3.0))
    assert media.sharpness(sharp, 160, 284) > 5 * media.sharpness(blurred, 160, 284)
    assert media.sharpest_time(tmp_path / "missing.mp4", [2.0, 5.0]) == 2.0   # unreadable frames: the first candidate


def test_the_cover_hook_line_is_short():
    assert media.short_hook("Short one") == "Short one"
    long = media.short_hook("word " * 40)
    assert len(long) <= media.COVER_HOOK_MAX + 1 and long.endswith("…") and "  " not in long
    assert media.short_hook("x" * 100).endswith("…")                         # one long word is cut as it is


# ------------------------------------------------------------------ templates as data: normalising and refusing

def test_a_template_missing_fields_or_with_bad_ones_gets_defaults_at_render():
    assert media.normalize_template({}) == media.TEMPLATE_DEFAULTS and media.normalize_template(None) == media.TEMPLATE_DEFAULTS
    out = media.normalize_template({"accent": "#00FF00", "font": "Arial,2,&H0000FF", "caption_position": "top", "zooms": "yes", "extra": 1})
    assert out["accent"] == "#00FF00"                                          # the valid field is kept
    assert out["font"] == "Arial" and out["caption_position"] == "lower" and out["zooms"] is True and "extra" not in out


@pytest.mark.parametrize("bad", [{"caption_position": "top"}, {"font_size": 12}, {"font_size": 1}, {"font_size": True}, {"font": "x" * 80},
                                 {"font": "a,b"}, {"primary": "red"}, {"accent": "#12345"}, {"zooms": 1}, {"cover": "wild"}])
def test_an_owner_edit_with_a_bad_value_is_refused(bad):
    with pytest.raises(AppError) as refused:
        media.normalize_template(bad, strict=True)
    assert refused.value.code == "bad_template"


def test_template_text_cannot_break_out_of_the_subtitle_script():
    hostile = "Hook {\\an9\\pos(0,0)}\\N{\\p1}m 0 0\nsecond: 'quoted', [bracket];x"
    ass = media.build_ass(270, 480, 8, [], "#ad {\\b1}", False, {"font": "Arial\nStyle: Evil,Arial"}, hostile)
    lines = ass.splitlines()
    assert sum(line.startswith("Style:") for line in lines) == 5 and "Evil" not in ass         # the font name cannot add a style
    title = next(line for line in lines if ",Title," in line and line.startswith("Dialogue"))
    assert title.endswith("{\\fad(150,200)}Hook (an9pos(0,0))N(p1)m 0 0 second: 'quoted', [bracket];x")   # braces and backslashes neutralised, one line
    label = next(line for line in lines if ",Label," in line and line.startswith("Dialogue"))
    assert label.endswith(",#ad (b1)")
    assert sum(line.startswith("Dialogue") for line in lines) == 3                                # label, title card, progress bar: nothing was injected


def test_text_with_filter_characters_renders_because_user_text_never_enters_the_filter_graph(tmp_path, footage, monkeypatch):
    spy = Spy(monkeypatch)
    words = [{"text": "it's,", "start": 1.0, "end": 1.4}, {"text": "[x];y:z", "start": 1.5, "end": 1.9}]
    result = render(tmp_path, Path(footage), clip_for(0, 6, hook_text="a:b,c;d'e[f]"), words=words, label="#ad'x")
    graph = spy.encodes[0][spy.encodes[0].index("-filter_complex") + 1]
    assert "a:b" not in graph and "it's" not in graph and result["encodes"] == 1


# ------------------------------------------------------------------ captions (R6)

TEXT = [{"text": "Hello,", "start": 0.0, "end": 0.4}, {"text": "world.", "start": 0.5, "end": 0.9}, {"text": "We", "start": 1.0, "end": 1.2},
        {"text": "made", "start": 1.3, "end": 1.5}, {"text": "300", "start": 1.6, "end": 1.9}, {"text": "clips;", "start": 2.0, "end": 2.4}]


def test_social_captions_drop_light_punctuation_keep_question_marks_and_never_change_a_word():
    social = media.caption_events(TEXT + [{"text": "right?", "start": 3.0, "end": 3.4}], 0, 6, True, [])
    plain = media.caption_events(TEXT + [{"text": "right?", "start": 3.0, "end": 3.4}], 0, 6, False, [])
    social_text, plain_text = " ".join(e["text"] for e in social), " ".join(e["text"] for e in plain)
    assert "," not in social_text and ";" not in social_text and "." not in social_text and "right?" in social_text
    assert "Hello," in plain_text and "clips;" in plain_text and "\\1c" not in plain_text    # standard: as spoken, no highlight
    strip = lambda text: [w.strip(".,;:") for w in text.replace("{\\r}", "").split()]  # noqa: E731
    assert len([w for w in strip(social_text) if w]) == len(strip(plain_text))             # the same words


def test_social_captions_highlight_the_loud_word_else_a_number_in_the_accent_colour():
    loud = media.caption_events(TEXT, 0, 6, True, [1.4], "#00FF00")
    assert "{\\1c&H0000FF00&}made{\\r}" in loud[1]["text"] or "{\\1c&H0000FF00&}made{\\r}" in loud[0]["text"]
    number = media.caption_events(TEXT, 0, 6, True, [], "#00FF00")
    assert "{\\1c&H0000FF00&}300{\\r}" in " ".join(e["text"] for e in number)
    assert media.caption_events(TEXT, 0, 6, False, [1.4], "#00FF00")[0]["text"].count("\\1c") == 0


def test_captions_keep_arabic_with_english_words_in_one_line_and_time_them_inside_the_clip():
    words = [{"text": t, "start": 10 + i * 0.4, "end": 10.3 + i * 0.4} for i, t in enumerate(["هذا", "iPhone", "رائع", "جدا،", "وأنا", "أحبه"])]
    events = media.caption_events(words, 9.0, 14.0, True, [])
    joined = " ".join(e["text"] for e in events)
    assert "iPhone" in joined and "جدا" in joined and "،" not in joined
    assert events[0]["start"] == pytest.approx(1.0) and all(0 <= e["start"] < e["end"] <= 5.0 for e in events)


def test_the_standard_and_social_style_follow_the_platform(tmp_path, footage):
    social = render(tmp_path, Path(footage), clip_for(0, 8, platform="tiktok", emphasis=[3.0]), words=WORDS, name="s")
    standard = render(tmp_path, Path(footage), clip_for(0, 8, platform="youtube_shorts", emphasis=[3.0]), words=WORDS, name="y")
    assert "\\1c" in Path(social["ass_path"]).read_text(encoding="utf-8-sig").split("[Events]")[1]
    assert "punctuation." in Path(standard["ass_path"]).read_text(encoding="utf-8-sig")
    assert "punctuation." not in Path(social["ass_path"]).read_text(encoding="utf-8-sig")


def test_a_very_long_arabic_hook_is_wrapped_and_shrunk_and_still_renders(tmp_path, footage):
    hook = "لماذا لا يتحدث أحد عن هذا الأمر الخطير الذي يغير كل شيء في حياتنا اليومية وما الذي يجب أن نفعله الآن بالضبط؟ " * 3
    assert media.fit_size("قصير", 200, 40) == 40
    assert media.fit_size(hook, W * 0.8, 40) < 40 and media.fit_size(hook, 1, 40) == 22     # shrunk, never below 55 percent
    result = render(tmp_path, Path(footage), clip_for(language="ar", hook_text=hook), name="long")
    ass = Path(result["ass_path"]).read_text(encoding="utf-8-sig")
    size = int(next(line for line in ass.splitlines() if line.startswith("Style: Title")).split(",")[2])
    assert size < int(H * 0.036) and "لماذا" in ass and result["encodes"] == 1 and png_size(Path(result["cover_path"])) == (W, H)


# ------------------------------------------------------------------ reframing

def test_the_crop_follows_the_person_and_falls_back_to_the_centre_when_nobody_is_there(tmp_path):
    left, right, flat = person_source(tmp_path, 100), person_source(tmp_path, 460), flat_source(tmp_path, seconds=4)
    clip = clip_for(0, 3, crop="follow_speaker")
    assert media.face_focus(left, clip) < 0.4 and media.face_focus(right, clip) > 0.6
    assert media.face_focus(flat, clip) is None
    for src, reframe in ((left, "face"), (flat, "centre_crop")):
        assert render(tmp_path, src, clip, words=[], name=f"r{reframe}")["reframe"] == reframe
    # the speaker really is inside the 9:16 crop at both ends
    for src, name in ((left, "L"), (right, "R")):
        picture = frame(Path(render(tmp_path, src, clip, {"progress_bar": False, "title_card": False}, words=[], label="#ad", name=name)["path"]), 1.0)
        skin = ffmpeg.skin_mask(picture)[int(H * 0.15):]
        assert skin.mean() > 0.05


def test_face_focus_uses_the_strongest_face_seconds_of_the_signal(tmp_path):
    src = person_source(tmp_path, 460, seconds=6)
    clip = clip_for(0, 6, signals={"first_s": 0, "face": [0, 0, 1.0, 0, 0, 0]})
    assert media._samples(clip) == [2.5] and media.face_focus(src, clip) > 0.6
    assert media._samples(clip_for(0, 6)) == [3.0]


def test_each_crop_style_builds_its_own_layout():
    follow = media.layout_graph("follow_speaker", 1920, 1080, 1080, 1920, 0.8)
    centre = media.layout_graph("centre", 1920, 1080, 1080, 1920, 0.8)
    stacked = media.layout_graph("facecam_over_game", 1920, 1080, 1080, 1920, 0.8)
    assert follow != centre and "boxblur" in stacked and "overlay" in stacked and stacked.endswith("[lay]")
    assert media.layout_graph("facecam_over_game", 1080, 1920, 1080, 1920, None) == media.layout_graph("centre", 1080, 1920, 1080, 1920, None)
    assert media.layout_graph("unknown_style", 1920, 1080, 1080, 1920, 0.2) == centre   # unknown style: centre


def test_the_game_layout_keeps_the_whole_picture_over_a_blurred_fill(tmp_path, footage):
    result = render(tmp_path, Path(footage), clip_for(0, 6, crop="facecam_over_game"), {"progress_bar": False, "title_card": False}, words=[], name="g")
    picture = frame(Path(result["path"]), 2.0)
    assert picture[int(H * 0.4): int(H * 0.6)].std() > 20 and picture[int(H * 0.15): int(H * 0.2)].std() < picture[int(H * 0.4): int(H * 0.6)].std()


def test_the_declared_fallback_renders_a_plain_centre_crop_without_zooms(tmp_path, footage):
    result = render(tmp_path, Path(footage), clip_for(0, 8, crop="follow_speaker", emphasis=[4.0]), name="fb")
    fallback = adapter(media.CentreCropRender).render_clip(footage, clip_for(0, 8, crop="follow_speaker", emphasis=[4.0]), WORDS, {}, "#ad", str(tmp_path / "fb2.mp4"))
    assert fallback["zooms"] == 0 and fallback["reframe"] == "centre_crop" and result["zooms"] == 1 and fallback["encodes"] == 1


def test_zoom_times_are_spaced_and_stay_off_the_first_and_last_second():
    assert media.zoom_times([10.2, 10.9, 12.0, 17.0, 50.0, 3.0], 10.0, 10.0) == [2.0, 7.0]     # 0.2 and 0.9 too early, 40 too late
    spread = media.zoom_times([float(i) for i in range(1, 60)], 0.0, 60.0)
    assert len(spread) == media.ZOOM_MAX and all(b - a >= media.ZOOM_GAP_S for a, b in zip(spread, spread[1:]))
    expr = media.zoom_graph([2.0, 8.0], 270, 480)
    assert "abs(t-2.00)" in expr and "abs(t-8.00)" in expr and expr.startswith("[lay]scale=") and expr.endswith("[zm]")


# ------------------------------------------------------------------ NVENC when free, x264 otherwise

def test_the_encoder_choice_follows_the_setting_and_the_gpu(monkeypatch):
    monkeypatch.setattr(ffmpeg, "nvenc_ready", lambda: False)
    assert ffmpeg.video_encoder("auto") == "libx264" and ffmpeg.video_encoder("x264") == "libx264" and ffmpeg.video_encoder("nvenc") == "h264_nvenc"
    monkeypatch.setattr(ffmpeg, "nvenc_ready", lambda: True)
    assert ffmpeg.video_encoder("auto") == "h264_nvenc" and ffmpeg.video_encoder("x264") == "libx264"
    assert "h264_nvenc" in ffmpeg.encoder_args("h264_nvenc") and ffmpeg.encoder_args("libx264", "fast", 20) == ["-c:v", "libx264", "-preset", "fast", "-crf", "17"]


def test_nvenc_probe_is_cached_and_a_missing_gpu_gives_false(monkeypatch):
    calls = []

    def run(args, *a, **kw):
        calls.append(args)
        raise ffmpeg.FFmpegError("No capable devices found")

    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "_nvenc", {"at": 0.0, "ok": False})
    assert ffmpeg.nvenc_ready() is False and ffmpeg.nvenc_ready() is False and len(calls) == 1


def test_a_busy_or_missing_nvenc_falls_back_to_x264_in_the_same_render(tmp_path, footage, monkeypatch):
    spy = Spy(monkeypatch, fail_nvenc=True)
    monkeypatch.setattr(ffmpeg, "nvenc_ready", lambda: True)
    result = render(tmp_path, Path(footage), clip_for(0, 6), encoder="auto")
    assert result["encoder"] == "x264" and result["encodes"] == 1 and ffmpeg.probe(Path(result["path"]))["duration_s"] > 5
    assert [c[c.index("-c:v") + 1] for c in spy.encodes] == ["h264_nvenc", "libx264"]


def test_an_x264_failure_is_not_retried_and_surfaces(tmp_path, footage, monkeypatch):
    calls = []

    def run(args, *a, **kw):
        calls.append(args)
        raise ffmpeg.FFmpegError("encoder exploded")

    monkeypatch.setattr(ffmpeg, "run", run)
    with pytest.raises(ffmpeg.FFmpegError):
        render(tmp_path, Path(footage), clip_for(0, 4))
    assert len(calls) == 1


def test_a_source_ffmpeg_cannot_read_fails_before_any_encode(tmp_path, monkeypatch):
    spy = Spy(monkeypatch)
    with pytest.raises(ffmpeg.FFmpegError):
        render(tmp_path, tmp_path / "missing.mp4", clip_for())
    assert spy.encodes == []


def test_a_real_nvenc_encode_when_the_gpu_is_free(tmp_path, footage):
    if not ffmpeg.nvenc_ready():
        pytest.skip("no NVENC on this machine")
    result = render(tmp_path, Path(footage), clip_for(0, 6), encoder="nvenc")
    assert result["encoder"] == "nvenc" and ffmpeg.probe(Path(result["path"]))["has_audio"]


# ------------------------------------------------------------------ the creative library (DB)

def accounts(db, owner) -> list[dict]:
    add_accounts(db, owner)
    with db.session(owner) as conn:
        return conn.execute("select id, platform from accounts order by platform").fetchall()


def client(owner) -> TestClient:
    api._owner = owner
    return TestClient(api.app)


def test_an_account_without_templates_gets_a_starter_family_that_stays_recognisable(db, owner):
    account = accounts(db, owner)[0]
    with db.session(owner) as conn:
        found = templates.ensure_set(conn, account["id"])
        assert templates.ensure_set(conn, account["id"]) == found           # seeded once
        assert templates.primary(conn, account["id"])["id"] == found[0]["id"] and templates.pick(conn, account["id"])["id"] == found[0]["id"]
    assert len(found) == 3 and len({t["spec"]["font"] for t in found}) == 1 and len({t["spec"]["primary"] for t in found}) == 1
    assert len({(t["spec"]["accent"], t["spec"]["caption_position"], t["spec"]["progress_bar"]) for t in found}) == 3


def test_different_accounts_start_from_different_looks():
    import uuid

    first, second = (templates.starter_set(uuid.UUID(int=i)) for i in (0, 1))
    assert first[0][1]["accent"] != second[0][1]["accent"] and first[0][0].startswith(templates.LOOKS[0][0])
    assert templates.starter_set(uuid.UUID(int=len(templates.LOOKS)))[0] == first[0]            # the six looks repeat
    assert [p["name"] for p in templates.presets()] == [look[0] for look in templates.LOOKS]


def test_DW_5_6_a_new_template_added_through_the_api_is_next_in_the_rotation(db, owner):
    account = accounts(db, owner)[0]
    c = client(owner)
    first = c.post(f"/api/accounts/{account['id']}/templates", json={"name": "One", "accent": "#111111"})
    assert first.status_code == 200 and first.json()["version"] == 1 and first.json()["accent"] == "#111111"
    with db.session(owner) as conn:
        assert templates.pick(conn, account["id"])["name"] == "One"
    second = c.post(f"/api/accounts/{account['id']}/templates", json={"name": "Two", "accent": "#222222", "zooms": False}).json()
    with db.session(owner) as conn:
        chosen = templates.pick(conn, account["id"])
    assert chosen["id"] == second["template_id"] and chosen["spec"]["accent"] == "#222222" and chosen["spec"]["zooms"] is False   # no code change


def test_DW_5_6_an_account_with_several_templates_rotates_them_across_consecutive_clips(db, owner):
    account = accounts(db, owner)[0]
    c = client(owner)
    names = ["A", "B", "C"]
    for name in names:
        assert c.post(f"/api/accounts/{account['id']}/templates", json={"name": name}).status_code == 200
    with db.session(owner) as conn:
        order = [templates.pick(conn, account["id"])["name"] for _ in range(7)]
        kept = templates.pick(conn, account["id"], keep_id=templates.list_for_account(conn, account["id"])[0]["id"])
        after = templates.pick(conn, account["id"])["name"]
    assert order == ["C", "A", "B", "C", "A", "B", "C"]           # the newest first, then round and round
    assert kept["name"] == "A" and after == "A"                   # an edited clip keeps its look and does not advance the rotation


def test_a_switched_off_template_leaves_the_rotation_and_none_left_means_defaults(db, owner):
    account = accounts(db, owner)[0]
    c = client(owner)
    a = c.post(f"/api/accounts/{account['id']}/templates", json={"name": "A"}).json()
    b = c.post(f"/api/accounts/{account['id']}/templates", json={"name": "B"}).json()
    assert c.put(f"/api/templates/{a['template_id']}", json={"active": False}).json()["active"] is False
    with db.session(owner) as conn:
        assert {templates.pick(conn, account["id"])["name"] for _ in range(3)} == {"B"}
        assert templates.pick(conn, account["id"], keep_id=a["template_id"])["name"] == "B"      # a switched-off template is not kept
    c.put(f"/api/templates/{b['template_id']}", json={"active": False})
    with db.session(owner) as conn:
        none = templates.pick(conn, account["id"])
        assert none == {"id": None, "name": "Default", "version": 0, "spec": media.TEMPLATE_DEFAULTS}
        assert templates.pick(conn, None)["id"] is None
    assert c.get(f"/api/accounts/{account['id']}/template").status_code == 400    # the owner is told to switch one on


def test_DW_5_6_reverting_a_version_restores_the_previous_look_as_a_new_version(db, owner):
    account = accounts(db, owner)[0]
    c = client(owner)
    made = c.post(f"/api/accounts/{account['id']}/templates", json={"name": "Look", "accent": "#AA0000", "font": "Arial"}).json()
    edited = c.put(f"/api/templates/{made['template_id']}", json={"accent": "#0000BB", "font": "Tahoma"}).json()
    assert edited["version"] == 2 and edited["accent"] == "#0000BB"
    back = c.post(f"/api/templates/{made['template_id']}/revert", json={}).json()
    assert back["version"] == 3 and back["accent"] == "#AA0000" and back["font"] == "Arial"          # the old look, as a new version
    assert [v["version"] for v in back["versions"]] == [3, 2, 1]
    assert back["versions"][0]["note"] == "Reverted to version 1"
    again = c.post(f"/api/templates/{made['template_id']}/revert", json={"version": 2}).json()
    assert again["version"] == 4 and again["accent"] == "#0000BB"
    for bad in ({"version": 9}, {"version": 4}, {"version": "x"}, {"version": True}):
        assert c.post(f"/api/templates/{made['template_id']}/revert", json=bad).status_code == 400
    fresh = c.post(f"/api/accounts/{account['id']}/templates", json={"name": "Fresh"}).json()
    assert c.post(f"/api/templates/{fresh['template_id']}/revert", json={}).json()["code"] == "no_version"


def test_template_versions_are_immutable_in_the_database(db, owner):
    account = accounts(db, owner)[0]
    made = client(owner).post(f"/api/accounts/{account['id']}/templates", json={"name": "Look"}).json()
    with pytest.raises(Exception, match="never changes"):
        with db.session(Actor.system()) as conn:
            conn.execute("update template_versions set spec = '{}' where template_id = %s", (made["template_id"],))


def test_templates_follow_row_level_security(db, owner):
    account = accounts(db, owner)[0]
    made = client(owner).post(f"/api/accounts/{account['id']}/templates", json={"name": "Mine"}).json()
    with db.session(Actor.system()) as conn:
        other = conn.execute("insert into operators (name, role, languages) values ('Other', 'operator', '{en}') returning id").fetchone()["id"]
    stranger = Actor(str(other), "operator")
    with db.session(stranger) as conn:
        assert conn.execute("select count(*) as n from templates").fetchone()["n"] == 0
        assert conn.execute("select count(*) as n from template_versions").fetchone()["n"] == 0
        with pytest.raises(AppError):
            templates.update(conn, made["template_id"], {"accent": "#123456"})
        with pytest.raises(AppError):
            templates.add(conn, account["id"], "Sneaky")


def test_the_template_api_refuses_bad_input_with_a_400_and_a_reason(db, owner):
    account = accounts(db, owner)[0]
    c = client(owner)
    url = f"/api/accounts/{account['id']}/template"
    for bad, word in (({"caption_position": "top"}, "lower or middle"), ({"font_size": 40}, "caption size"), ({"font_size": "huge"}, "caption size"),
                      ({"font": "A" * 90}, "font name"), ({"primary": "white"}, "colour"), ({"title_card": "yes"}, "on or off"),
                      ({"colour": "#FFFFFF"}, "Unknown template field")):
        refused = c.put(url, json=bad)
        assert refused.status_code == 400 and refused.json()["code"] == "bad_template" and word in refused.json()["error"], bad
    assert c.get(url).json()["version"] == 1                                           # nothing was saved
    assert c.post(f"/api/accounts/{account['id']}/templates", json={"accent": "#FFFFFF"}).status_code == 400          # a name is needed
    assert c.post(f"/api/accounts/{account['id']}/templates", json={"name": "x" * 70}).status_code == 400
    assert c.post(f"/api/accounts/{account['id']}/templates", json={"name": "Dup"}).status_code == 200
    assert c.post(f"/api/accounts/{account['id']}/templates", json={"name": "Dup"}).json()["code"] == "duplicate"
    assert c.put("/api/templates/not-an-id", json={"accent": "#FFFFFF"}).status_code == 400
    assert c.get("/api/accounts/00000000-0000-0000-0000-000000000000/template").json()["code"] == "not_found"
    assert c.put(f"/api/templates/{c.get(url).json()['template_id']}", json={"active": "no"}).status_code == 400


def test_the_template_limit_per_account_is_enforced(db, owner):
    account = accounts(db, owner)[0]
    c = client(owner)
    for i in range(templates.MAX_PER_ACCOUNT):
        assert c.post(f"/api/accounts/{account['id']}/templates", json={"name": f"T{i}"}).status_code == 200
    assert c.post(f"/api/accounts/{account['id']}/templates", json={"name": "One too many"}).json()["code"] == "too_many"


def test_get_and_put_template_return_the_contract_fields_and_merge_only_what_was_sent(db, owner):
    account = accounts(db, owner)[0]
    c = client(owner)
    url = f"/api/accounts/{account['id']}/template"
    first = c.get(url).json()
    for key in ("font", "primary", "accent", "caption_position", "title_card", "progress_bar", "zooms"):
        assert key in first
    saved = c.put(url, json={"accent": "#00AA55", "caption_position": "middle", "title_card": False}).json()
    assert (saved["accent"], saved["caption_position"], saved["title_card"], saved["font"], saved["version"]) == ("#00AA55", "middle", False, first["font"], 2)
    assert c.put(url, json={"accent": "#00AA55"}).json()["version"] == 2            # nothing changed: no new version
    renamed = c.put(f"/api/templates/{saved['template_id']}", json={"name": "Renamed"}).json()
    assert renamed["name"] == "Renamed" and renamed["version"] == 2
    listing = c.get(f"/api/accounts/{account['id']}/templates").json()
    assert len(listing["templates"]) == 3 and len(listing["presets"]) == len(templates.LOOKS)
    assert c.get(f"/api/accounts/{account['id']}/templates").json()["templates"][0]["versions"][0]["version"] == 2


# ------------------------------------------------------------------ the persona

def test_best_hours_come_from_the_accounts_own_results_once_there_are_enough_posts(db, owner):
    account = accounts(db, owner)[0]
    with db.session(owner) as conn:
        typical = templates.persona(conn, account["id"])
    assert typical["best_hours"] == {"hours": [12, 18, 21], "basis": "typical", "tz": "local", "posts_counted": 0}
    assert typical["posts_per_week"] in (5, 7) and typical["look"]["templates"] == 3 and typical["look"]["font"]
    day = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    posts = [{"posted_at": day.replace(hour=h), "views": v} for h, v in ((9, 100), (9, 120), (20, 900), (20, 700), (20, 800), (14, 300))]
    assert templates.best_hours(posts[:5]) is None and templates.best_hours([]) is None            # under the minimum: not enough to learn from
    assert templates.best_hours(posts) == {"hours": [9, 14, 20], "basis": "results", "tz": "utc", "posts_counted": 6}
    more = posts + [{"posted_at": day.replace(hour=3), "views": 5000}]
    assert templates.best_hours(more)["hours"] == [3, 14, 20]                                       # the three best, in clock order


def test_the_persona_posts_per_week_respects_the_accounts_own_daily_cap(db, owner):
    account = accounts(db, owner)[0]
    with db.session(owner) as conn:
        conn.execute("update accounts set daily_cap = 0 where id = %s", (account["id"],))
        assert templates.persona(conn, account["id"])["posts_per_week"] == 1       # never zero or negative
        conn.execute("update accounts set daily_cap = 1 where id = %s", (account["id"],))
        assert templates.persona(conn, account["id"])["posts_per_week"] == 5       # Reels: five a week
    c = client(owner)
    assert c.get(f"/api/accounts/{account['id']}/persona").json()["look"]["names"]
    assert c.get("/api/accounts/nope/persona").status_code == 400


# ------------------------------------------------------------------ signals feed the render

def test_loud_moments_are_the_peaks_inside_the_clip_well_above_its_usual_level():
    loudness = [-30.0] * 40
    loudness[12], loudness[13], loudness[25], loudness[39], loudness[3] = -14.0, -13.0, -12.0, -10.0, -11.0
    assert templates.emphasis_times(loudness, 5.0, 38.0) == [13.5, 25.5]       # the peak of 12-13 once; 3 and 39 are outside the clip
    assert templates.emphasis_times([-30.0] * 40, 0, 40) == []                 # nothing stands out
    assert templates.emphasis_times([-70.0, -70.0, -20.0, -70.0, -70.0], 0, 5) == [2.5]
    assert templates.emphasis_times([-30.0, -10.0], 0, 2) == [] and templates.emphasis_times([], 0, 10) == []
    capped = [(-10.0 if i % 2 else -40.0) for i in range(100)]
    assert len(templates.emphasis_times(capped, 0, 100)) == templates.EMPHASIS_MAX


# ------------------------------------------------------------------ the pipeline stage

def rerender(produced, clip_id) -> dict:
    """Run the render stage again for a clip as if it were new (its template choice cleared)."""
    with produced.db.session(Actor.worker()) as conn:
        conn.execute("update clips set render_meta = render_meta - 'template_id' where id = %s", (clip_id,))
        pipeline.stage_render(conn, {"payload": {"clip_id": str(clip_id)}})
        return conn.execute("select * from clips where id = %s", (clip_id,)).fetchone()


def ass_of(produced, clip) -> str:
    with produced.db.session(produced.owner) as conn:
        return alerts.registry.get(conn, "storage").path(clip["file_path"]).with_suffix(".ass").read_text(encoding="utf-8-sig")


def test_the_render_stage_records_the_template_encoder_cover_and_account(produced):
    with produced.db.session(produced.owner) as conn:
        clips = conn.execute("select * from clips").fetchall()
        known = {str(t["id"]) for t in conn.execute("select id from templates").fetchall()}
    assert len(clips) == 3
    for clip in clips:
        meta = clip["render_meta"]
        assert meta["template_id"] in known and meta["template_version"] == 1 and meta["encodes"] == 1 and meta["encoder"] == "x264"
        assert meta["cover_key"] == clip["cover_path"] and clip["account_id"] and clip["platform"] and meta["overlay_burned"] is True
        with produced.db.session(produced.owner) as conn:
            cover = alerts.registry.get(conn, "storage").path(clip["cover_path"])
        assert png_size(cover) == (270, 480)


def test_DW_5_5_the_owner_edits_the_account_template_and_the_next_clip_uses_it(produced):
    with produced.db.session(produced.owner) as conn:
        clip = conn.execute("select * from clips order by start_s limit 1").fetchone()
    account_id = str(clip["account_id"])
    c = client(produced.owner)
    saved = c.put(f"/api/accounts/{account_id}/template", json={"font": "Tahoma", "accent": "#112233", "caption_position": "middle", "progress_bar": False})
    assert saved.status_code == 200 and saved.json()["version"] == 2
    again = rerender(produced, clip["id"])
    assert again["render_meta"]["template_version"] == 2
    ass = ass_of(produced, again)
    assert "Tahoma" in ass and "&H00332211" in ass and ",0,5," in ass and ",Bar," not in ass.split("[Events]")[1]


def test_DW_5_5_two_accounts_with_different_templates_render_differently(produced):
    c = client(produced.owner)
    with produced.db.session(produced.owner) as conn:
        by_account = {str(r["account_id"]): r for r in conn.execute("select * from clips").fetchall()}
    first, second = list(by_account)[:2]
    c.put(f"/api/accounts/{first}/template", json={"accent": "#FF0000", "font": "Arial", "caption_position": "lower"})
    c.put(f"/api/accounts/{second}/template", json={"accent": "#0000FF", "font": "Tahoma", "caption_position": "middle"})
    one, two = (rerender(produced, by_account[a]["id"]) for a in (first, second))
    assert one["render_meta"]["template_id"] != two["render_meta"]["template_id"]
    assert "&H000000FF" in ass_of(produced, one) and "&H00FF0000" in ass_of(produced, two)


def test_DW_5_6_the_next_render_uses_a_template_added_through_the_api_and_a_revert_restores_the_look(produced):
    with produced.db.session(produced.owner) as conn:
        clip = conn.execute("select * from clips order by start_s limit 1").fetchone()
    account_id, c = str(clip["account_id"]), client(produced.owner)
    new = c.post(f"/api/accounts/{account_id}/templates", json={"name": "Brand new", "accent": "#00EE88", "font": "Arial"}).json()
    again = rerender(produced, clip["id"])
    assert again["render_meta"]["template_id"] == new["template_id"] and "&H0088EE00" in ass_of(produced, again)
    c.put(f"/api/templates/{new['template_id']}", json={"accent": "#EE0088"})
    edited = rerender(produced, clip["id"])
    assert edited["render_meta"]["template_version"] == 2 and "&H008800EE" in ass_of(produced, edited)
    c.post(f"/api/templates/{new['template_id']}/revert", json={})
    restored = rerender(produced, clip["id"])
    assert restored["render_meta"]["template_version"] == 3 and "&H0088EE00" in ass_of(produced, restored)


def test_an_edited_clip_keeps_its_template_and_a_retried_render_does_not_advance_the_rotation(produced):
    with produced.db.session(Actor.worker()) as conn:
        clip = conn.execute("select * from clips order by start_s limit 1").fetchone()
        used = conn.execute("select id, last_used_at from templates where account_id = %s", (clip["account_id"],)).fetchall()
        pipeline.stage_render(conn, {"payload": {"clip_id": str(clip["id"])}})
        after = conn.execute("select id, last_used_at from templates where account_id = %s", (clip["account_id"],)).fetchall()
        again = conn.execute("select render_meta from clips where id = %s", (clip["id"],)).fetchone()
    assert used == after and again["render_meta"]["template_id"] == clip["render_meta"]["template_id"]


def test_the_account_is_chosen_at_render_and_kept_afterwards(produced):
    with produced.db.session(Actor.worker()) as conn:
        clip = conn.execute("select * from clips order by start_s limit 1").fetchone()
        campaign = conn.execute("select * from campaigns where id = %s", (clip["campaign_id"],)).fetchone()
        version = conn.execute("select * from campaign_versions where id = %s", (clip["campaign_version_id"],)).fetchone()
        assert pipeline._assign_account(conn, clip, campaign, version) == (clip["account_id"], clip["platform"])    # already set: kept
        conn.execute("update clips set account_id = null, platform = null where id = %s", (clip["id"],))
        loose = conn.execute("select * from clips where id = %s", (clip["id"],)).fetchone()
        chosen = pipeline._assign_account(conn, loose, campaign, version)
        saved = conn.execute("select account_id, platform from clips where id = %s", (clip["id"],)).fetchone()
        assert chosen[0] is not None and (saved["account_id"], saved["platform"]) == chosen
        conn.execute("update accounts set status = 'retired'")
        conn.execute("update clips set account_id = null, platform = null where id = %s", (clip["id"],))
        assert pipeline._assign_account(conn, conn.execute("select * from clips where id = %s", (clip["id"],)).fetchone(), campaign, version) == (None, None)


def test_a_clip_with_no_account_renders_with_the_default_template(produced):
    with produced.db.session(Actor.worker()) as conn:
        conn.execute("update accounts set status = 'retired'")
        clip = conn.execute("select * from clips order by start_s limit 1").fetchone()
        conn.execute("update clips set account_id = null, platform = null, render_meta = render_meta - 'template_id' where id = %s", (clip["id"],))
        pipeline.stage_render(conn, {"payload": {"clip_id": str(clip["id"])}})
        meta = conn.execute("select render_meta, platform from clips where id = %s", (clip["id"],)).fetchone()
    assert meta["render_meta"]["template_id"] is None and meta["render_meta"]["template_version"] == 0 and meta["platform"] is None


def test_the_render_request_carries_the_profile_style_and_the_signals_around_the_clip(produced):
    with produced.db.session(Actor.worker()) as conn:
        clip = conn.execute("select * from clips order by start_s limit 1").fetchone()
        source = conn.execute("select * from source_assets").fetchone()
        request = pipeline._render_request(conn, clip, source, "tiktok", "A hook")
        start, end = float(clip["start_s"]), float(clip["end_s"])
        assert request["crop"] == "follow_speaker" and request["audio"] == "speech" and request["platform"] == "tiktok" and request["hook_text"] == "A hook"
        sig = request["signals"]
        assert sig["first_s"] == int(start) and len(sig["face"]) == len(sig["loudness"]) == int(-(-end // 1)) - int(start)
        assert all(start <= t <= end for t in request["emphasis"])
        conn.execute("delete from source_signals")
        bare = pipeline._render_request(conn, clip, source, None, None)
    assert "signals" not in bare and bare["emphasis"] == [] and bare["platform"] is None


def test_the_captions_stage_is_a_passthrough_for_queued_jobs(produced):
    with produced.db.session(Actor.worker()) as conn:
        clip = conn.execute("select * from clips order by start_s limit 1").fetchone()
        jobs = lambda: conn.execute("select count(*) as n from jobs where type = 'render'").fetchone()["n"]  # noqa: E731
        before = jobs()
        assert pipeline.stage_captions(conn, {"payload": {"clip_id": str(clip["id"])}}) == Decimal("0") and jobs() == before    # finished: nothing to do
        conn.execute("update clips set file_path = null where id = %s", (clip["id"],))
        assert pipeline.stage_captions(conn, {"payload": {"clip_id": str(clip["id"])}}) == Decimal("0") and jobs() == before + 1   # cut but not captioned: rendered in one pass
        assert pipeline.stage_captions(conn, {"payload": {"clip_id": str(clip["id"])}}) == Decimal("0") and jobs() == before + 1   # and only once
    assert pipeline.HANDLERS["captions"] is pipeline.stage_captions


def test_a_missing_disclosure_blocks_the_render_before_any_work(produced, monkeypatch):
    spy = Spy(monkeypatch)
    with produced.db.session(Actor.system()) as conn:
        conn.execute("delete from disclosure_templates where language = 'en'")
    with produced.db.session(Actor.worker()) as conn:
        clip = conn.execute("select * from clips order by start_s limit 1").fetchone()
        with pytest.raises(AppError) as blocked:
            pipeline.stage_render(conn, {"payload": {"clip_id": str(clip["id"])}})
    assert blocked.value.code == "disclosure_missing" and spy.encodes == []
