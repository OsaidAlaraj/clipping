"""Clip polish inside the single encode: dead-air and filler tightening, punch-in zooms, word-by-word captions, lanczos scaling.
FFmpeg is real; no database and no vendor."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipping import ffmpeg, polish
from clipping.adapters import media

CFG = {"width": 270, "height": 480, "preset": "ultrafast", "crf": 30, "encoder": "x264"}
PLAIN = {"zooms": False, "progress_bar": False, "title_card": False}


def w(text, start, end):
    return {"text": text, "start": start, "end": end}


def clip_for(end=8.0, **extra) -> dict:
    return {"start_s": 0.0, "end_s": end, "language": "en", "platform": "tiktok", "crop": "centre", "audio": "speech", "hook_text": None, "emphasis": [], **extra}


def render(tmp_path: Path, src: Path, clip: dict, words: list[dict], template: dict, name: str = "o") -> dict:
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    return media.FFmpegRender({"config_json": CFG}).render_clip(str(src), clip, words, template, "#ad", str(folder / f"{name}.mp4"))


# ------------------------------------------------------------------ tightening: the plan

SPOKEN = [w("so", 0.5, 0.8), w("this", 0.9, 1.2), w("works.", 1.3, 1.8),          # a 1.2 s pause follows
          w("Then", 3.0, 3.3), w("um", 3.4, 3.7), w("we", 3.8, 4.0), w("go", 4.1, 4.5),
          w("on", 4.6, 4.9), w("and", 5.0, 5.3), w("stop", 5.4, 5.8), w("here", 5.9, 6.4), w("today", 6.5, 7.0), w("ok", 7.1, 7.5)]


def test_a_long_pause_loses_its_middle_and_keeps_a_beat_after_a_sentence():
    plan = polish.plan_tighten(SPOKEN, 0.0, 8.0, "en", None, 25.0, fillers=False)
    assert len(plan.cuts) == 1 and plan.cuts[0]["kind"] == "pause"
    cut = plan.cuts[0]
    assert cut["start"] >= 1.8 + polish.SENTENCE_PAD_S - 0.05 and cut["end"] <= 3.0 - polish.PAD_S + 0.05    # words and pads stay
    assert plan.new_duration == pytest.approx(8.0 - (cut["end"] - cut["start"]))


def test_fillers_go_with_their_word_and_the_captions_drop_them():
    plan = polish.plan_tighten(SPOKEN, 0.0, 8.0, "en", None, 25.0)
    assert any(c["kind"] == "filler" for c in plan.cuts)
    kept = plan.words(SPOKEN, 0.0, 8.0)
    assert "um" not in [x["text"] for x in kept] and [x["text"] for x in kept][:3] == ["so", "this", "works."]
    starts = [x["start"] for x in kept]
    assert starts == sorted(starts) and kept[3]["start"] == pytest.approx(plan.map(3.0))


def test_a_cut_never_lands_inside_a_word_that_is_kept():
    plan = polish.plan_tighten(SPOKEN, 0.0, 8.0, "en", None, 25.0)
    for cut in plan.cuts:
        for i, word in enumerate(SPOKEN):
            if i not in plan.dropped:
                assert not (cut["start"] < word["end"] - 0.02 and cut["end"] > word["start"] + 0.02), (cut, word)


def test_the_first_and_last_instants_and_short_pauses_are_left_alone():
    words = [w("a", 0.0, 0.3), w("b", 0.6, 0.9), w("c", 1.2, 1.5), w("d", 5.0, 5.3)]      # gaps of 0.3 s: not dead air
    assert polish.plan_tighten(words, 0.0, 6.0, "en", None, 25.0).cuts == []
    edge = [w("a", 0.0, 0.3), w("b", 2.0, 2.3)]
    for cut in polish.plan_tighten(edge, 0.0, 2.3, "en", None, 25.0).cuts:
        assert cut["start"] >= polish.MIN_KEEP_S and cut["end"] <= 2.3 - polish.MIN_KEEP_S


def test_a_clip_never_loses_more_than_the_budget():
    words = [w(str(i), i * 2.0, i * 2.0 + 0.3) for i in range(10)]                         # nothing but dead air
    plan = polish.plan_tighten(words, 0.0, 20.0, "en", None, 25.0)
    assert plan.cuts and plan.removed <= polish.MAX_REMOVED_SHARE * 20.0 + 1e-6


def test_arabic_hesitations_go_but_real_words_stay():
    ar = [w("ااه", 0.0, 0.4), w("اه", 0.6, 0.9), w("امم", 1.0, 1.4), w("ايه", 1.5, 1.8), w("كتاب", 1.9, 2.3)]
    assert [polish.is_filler(ar, i, "ar") for i in range(5)] == [True, False, True, False, False]
    soft = [w("هذا", 0.0, 0.3), w("يعني", 0.35, 0.6), w("جيد", 0.65, 1.0)]                 # يعني said fluently stays
    assert not polish.is_filler(soft, 1, "ar")
    hesitant = [w("هذا", 0.0, 0.3), w("يعني", 0.6, 0.9), w("جيد", 1.2, 1.5)]               # يعني between pauses is a hesitation
    assert polish.is_filler(hesitant, 1, "ar")
    assert not polish.is_filler([w("um", 0, 1)], 0, "ar") and not polish.is_filler([w("umbrella", 0, 1)], 0, "en")


def test_the_time_map_is_continuous_and_monotonic():
    plan = polish.plan_tighten(SPOKEN, 0.0, 8.0, "en", None, 25.0)
    mapped = [plan.map(t) for t in np.linspace(0, 8, 400)]
    assert all(b >= a for a, b in zip(mapped, mapped[1:])) and mapped[0] == 0 and mapped[-1] == pytest.approx(plan.new_duration)


def test_cut_edges_slide_to_the_quietest_instant_and_a_loud_edge_is_refused():
    env = np.full(800, -60.0)
    env[:180] = -20.0                      # speech to 1.8 s, silence after it, speech again from 3.0 s
    env[300:] = -20.0
    env[194] = -75.0                       # the quietest spot, a little after the word
    words = [w("a", 0.0, 1.8), w("b", 3.0, 4.0)]
    plan = polish.plan_tighten(words, 0.0, 8.0, "en", env, 25.0)
    assert plan.cuts and 1.93 <= plan.cuts[0]["start"] <= 1.96
    loud = -20.0 + np.random.default_rng(2).normal(0, 0.5, 800)       # speech all the way: the word times were wrong, so no cut
    assert polish.plan_tighten(words, 0.0, 8.0, "en", loud, 25.0, fillers=False).cuts == []


# ------------------------------------------------------------------ tightening: the render

def beep_source(tmp_path: Path) -> Path:
    """A dark picture that flashes white for 0.2 s at 1, 3, 5 and 7 s, with a 1 kHz beep on the same instants."""
    out = tmp_path / "beeps.mp4"
    flash = "+".join(f"between(t,{t},{t + 0.2})" for t in (1, 3, 5, 7))
    ffmpeg.run(["-f", "lavfi", "-i", f"color=c=0x202020:s=320x240:r=25,geq=lum='if({flash.replace('(t,', '(T,')},235,32)':cb=128:cr=128",
                "-f", "lavfi", "-i", f"aevalsrc='0.5*sin(2*PI*1000*t)*({flash})':s=44100", "-t", "9", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)])
    return out


def _rises(on: np.ndarray, rate: int) -> list[float]:
    return [round(i / rate, 3) for i in range(1, len(on)) if on[i] and not on[i - 1]]


def flash_times(path: Path) -> list[float]:
    raw = subprocess.run([ffmpeg.exe(), "-nostdin", "-loglevel", "error", "-i", str(path), "-vf", "fps=50,scale=16:16,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    return _rises(np.frombuffer(raw, dtype=np.uint8).reshape(-1, 256).mean(axis=1) > 120, 50)


def beep_times(path: Path) -> list[float]:
    raw = subprocess.run([ffmpeg.exe(), "-nostdin", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True).stdout
    a = np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.float32))
    return _rises(np.convolve(a, np.ones(80) / 80, mode="same")[::80] > 3000, 100)       # 10 ms steps


def test_tightened_render_is_shorter_and_picture_and_sound_stay_together(tmp_path):
    src = beep_source(tmp_path)
    words = [w("one", 0.9, 1.3), w("two", 2.9, 3.3), w("three", 4.9, 5.3), w("four", 6.9, 7.3)]      # 1.6 s of dead air between each
    result = render(tmp_path, src, clip_for(), words, PLAIN)
    cuts = result["tightened"]
    assert cuts and 1.4 <= cuts["removed_s"] <= 0.3 * 8.0 and result["duration_s"] == pytest.approx(8.0 - cuts["removed_s"], abs=0.12)
    out = Path(result["path"])
    flashes, beeps = flash_times(out), beep_times(out)
    assert len(flashes) == len(beeps) == 4
    assert max(abs(f - b) for f, b in zip(flashes, beeps)) < 0.045           # under two frames of drift after three cuts
    gaps = np.diff(flashes)
    assert gaps.min() < 2.0 - 1.0 and gaps.min() > 0.3 and gaps.max() == pytest.approx(2.0, abs=0.1)     # one 2 s spacing shrank, the others did not


def test_the_tightened_audio_has_no_click_at_the_joins(tmp_path):
    src = tmp_path / "tone.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=gray:s=320x240:r=25", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100", "-t", "6", "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)])
    plan = polish.Plan(duration=6.0, keeps=[(0.0, 2.0), (3.0, 6.0)], cuts=[{"start": 2.0, "end": 3.0, "kind": "pause"}])
    out = tmp_path / "joined.mkv"
    ffmpeg.run(["-i", str(src), "-filter_complex", media.tighten_graphs(plan, "0:v", True, 25.0), "-map", "[cutv]", "-map", "[cuta]", "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "pcm_s16le", str(out)])
    raw = subprocess.run([ffmpeg.exe(), "-nostdin", "-loglevel", "error", "-i", str(out), "-ac", "1", "-ar", "44100", "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    assert len(a) / 44100 == pytest.approx(5.0, abs=0.05)
    jump = np.abs(np.diff(a))
    assert jump[int(1.95 * 44100):int(2.05 * 44100)].max() < 1.5 * np.abs(np.diff(a[:44100])).max()     # no step bigger than the sine's own slope


def test_tightening_can_be_switched_off_and_a_clip_without_dead_air_is_left_as_it_is(tmp_path):
    src = beep_source(tmp_path)
    words = [w("one", 0.9, 1.3), w("two", 2.9, 3.3)]
    off = render(tmp_path, src, clip_for(6.0), words, {**PLAIN, "tighten": False}, "off")
    assert off["tightened"] is None and off["duration_s"] == pytest.approx(6.0, abs=0.1)
    tone = tmp_path / "tone.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=gray:s=320x240:r=25", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100", "-t", "6", "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(tone)])
    dense = render(tmp_path, tone, clip_for(6.0), [w(str(i), i * 0.4, i * 0.4 + 0.35) for i in range(14)], PLAIN, "dense")
    assert dense["tightened"] is None


# ------------------------------------------------------------------ punch-in zooms

def test_the_punch_curve_eases_in_and_out_within_the_asked_depth():
    moments = [(2.0, 1.08), (9.0, 1.10)]
    z = np.array([media.punch_factor(t, moments) for t in np.arange(0, 14, 1 / 30)])
    assert z.min() == pytest.approx(1.0) and 1.079 < z.max() <= 1.101
    assert np.abs(np.diff(z)).max() < 0.016                  # no more than 1.6 % of the picture per frame, at the fastest instant
    assert np.abs(np.diff(z, 2)).max() < 0.0045              # and it never lurches
    assert media.punch_factor(2.0, moments) == pytest.approx(1.0) and media.punch_factor(2.0 + media.ZOOM_IN_S, moments) == pytest.approx(1.08)
    assert media.punch_factor(2.0 + media.ZOOM_IN_S + media.ZOOM_HOLD_S + media.ZOOM_OUT_S, moments) == pytest.approx(1.0)


def test_the_punch_graph_is_one_scale_and_crop_and_never_touches_the_subtitles():
    graph = media.punch_graph([(2.0, 1.08)], 270, 480)
    assert graph.startswith("[lay]scale=") and graph.endswith("[zm]") and graph.count("crop=270:480") == 1 and "ass" not in graph


def test_stress_moments_pick_loud_words_spaced_and_away_from_the_edges():
    env = -35.0 + np.random.default_rng(1).normal(0, 1.0, 4000)
    words = [w(f"w{i}", i * 1.0, i * 1.0 + 0.4) for i in range(40)]
    for i in (3, 9, 10, 20, 39, 0):
        env[i * 100:i * 100 + 40] = -12.0                    # six shouted words: two side by side, one in the first and one in the last second
    moments = polish.stress_moments(words, 0.0, 40.0, env)
    times = [t for t, _ in moments]
    assert times and all(b - a >= polish.STRESS_GAP_S for a, b in zip(times, times[1:]))
    assert all(1.0 <= t + 0.3 <= 38.5 for t in times) and all(polish.ZOOM_DEPTH_MIN <= d <= polish.ZOOM_DEPTH_MAX for _, d in moments)
    assert len(moments) <= polish.STRESS_MAX and polish.stress_moments(words, 0.0, 40.0, None) == []


def test_the_gameplay_layout_never_zooms(tmp_path):
    src = beep_source(tmp_path)
    result = render(tmp_path, src, clip_for(crop="facecam_over_game", emphasis=[3.0, 6.0]), [w("one", 2.9, 3.3)], {"zooms": True}, "g")
    assert result["zooms"] == 0
    assert render(tmp_path, src, clip_for(emphasis=[3.0, 6.0]), [], {"zooms": True}, "t")["zooms"] >= 1


# ------------------------------------------------------------------ word-by-word captions

LINE = [w("hello", 1.0, 1.4), w("brave", 1.5, 1.9), w("world", 2.0, 2.5), w("again", 3.5, 4.0)]


def test_each_word_gets_its_own_event_with_the_whole_line_and_one_highlight():
    events = media.karaoke_events(LINE, 0.0, 6.0, False, "#FFC400", 1080, 1920, 96, False)
    first_line = [e for e in events if e["start"] < 3.0]
    assert len(first_line) == 3
    for k, event in enumerate(first_line):
        assert re.sub(r"\{[^}]*\}", "", event["text"]) == "hello brave world"        # the line never reflows
        assert event["text"].count("\\t(0,70") == 1                                   # one highlighted word per state
        assert f"{{\\1c&H00FFFFFF&\\t(0,70,\\1c&H0000C4FF&)}}{['hello', 'brave', 'world'][k]}" in event["text"]
    assert [round(e["start"], 2) for e in first_line] == [1.0, 1.5, 2.0] and first_line[0]["end"] == 1.5 and first_line[1]["end"] == 2.0
    assert first_line[-1]["end"] <= 3.5                                                 # holds a moment, never into the next line


def test_captions_sit_in_the_safe_zone_for_english_and_arabic():
    for rtl, y in ((False, 0.58), (True, 0.60)):
        event = media.karaoke_events(LINE, 0.0, 6.0, False, "#FFC400", 1080, 1920, 96, rtl)[0]["text"]
        assert f"\\an5\\pos(540,{int(1920 * y)})" in event
        assert int(re.search(r"\\fs(\d+)", event).group(1)) <= 96
    assert "\\pos(540,960)" in media.karaoke_events(LINE, 0.0, 6.0, False, "#FFC400", 1080, 1920, 96, False, "middle")[0]["text"]


def test_arabic_words_keep_their_order_and_colour_tags_only_wrap_whole_words():
    ar = [w("مرحبا", 1.0, 1.4), w("بالعالم", 1.5, 2.0), w("الجميل", 2.1, 2.6)]
    events = media.karaoke_events(ar, 0.0, 4.0, True, "#FFC400", 1080, 1920, 96, True)
    assert len(events) == 3
    for event in events:
        assert re.sub(r"\{[^}]*\}", "", event["text"]) == "مرحبا بالعالم الجميل"
        body = event["text"].split("}", 1)[1]
        assert not re.search(r"[\u0600-\u06FF]\{[^}]*\}[\u0600-\u06FF]", body)            # never a tag between two letters of one word


def test_a_long_line_shrinks_to_fit_two_lines():
    big = [w("extraordinarily", 1.0, 1.4), w("incomprehensibly", 1.5, 1.9), w("disproportionate", 2.0, 2.5)]
    size = int(re.search(r"\\fs(\d+)", media.karaoke_events(big, 0.0, 4.0, False, "#FFC400", 1080, 1920, 96, False)[0]["text"]).group(1))
    assert size < 96


def test_karaoke_render_burns_the_highlight_and_stays_one_encode(tmp_path):
    src = beep_source(tmp_path)
    result = render(tmp_path, src, clip_for(6.0), [w("hello", 1.0, 1.4), w("world", 1.5, 2.0)], {"progress_bar": False, "title_card": False}, "k")
    assert result["encodes"] == 1 and result["captions"] == 2
    ass = Path(result["ass_path"]).read_text(encoding="utf-8-sig")
    assert "\\1c&H0000C4FF&" in ass and "\\an5\\pos(135,278)" in ass


def test_the_final_scale_is_lanczos():
    assert "flags=lanczos" in media.crop_filter(1920, 1080, 1080, 1920) and "flags=lanczos" in media.layout_graph("blur_pad", 1920, 1080, 1080, 1920, None)


def test_tightening_never_takes_a_clip_below_the_campaign_minimum():
    words = [w(str(i), i * 2.0, i * 2.0 + 0.3) for i in range(10)]
    free = polish.plan_tighten(words, 0.0, 20.0, "en", None, 25.0)
    kept = polish.plan_tighten(words, 0.0, 20.0, "en", None, 25.0, min_s=19.0)
    assert free.new_duration < 19.0 <= kept.new_duration + 1e-6 or kept.new_duration >= 19.0 - 1e-6
    assert kept.new_duration >= 19.0 - 1e-6


def test_tightening_zooms_karaoke_and_the_music_bed_work_together_in_one_encode(tmp_path):
    src = beep_source(tmp_path)
    bed = tmp_path / "bed.wav"
    ffmpeg.run(["-f", "lavfi", "-i", "sine=frequency=110:sample_rate=44100", "-t", "3", str(bed)])
    words = [w("one", 0.9, 1.3), w("two", 2.9, 3.3), w("three", 4.9, 5.3), w("four", 6.9, 7.3)]
    clip = clip_for(music={"path": str(bed), "offset_s": 0.0, "gain_db": -18.0})
    result = render(tmp_path, src, clip, words, {"progress_bar": False})
    assert result["encodes"] == 1 and result["tightened"] and result["captions"] > 0
    info = ffmpeg.probe(Path(result["path"]))
    assert info["has_audio"] and abs(info["duration_s"] - (8.0 - result["tightened"]["removed_s"])) < 0.15


def test_the_niche_and_language_cell_sets_caption_centre_size_and_words():
    podcast_ar = media.niche_defaults("podcast", "ar")
    assert podcast_ar["caption_y"] == 0.6 and podcast_ar["caption_font_pct"] == 4.5 and podcast_ar["caption_words"] == 1.5 and podcast_ar["layout"] == "auto"
    gaming_en = media.niche_defaults("gaming", "en")
    assert gaming_en["caption_y"] == 0.55 and gaming_en["layout"] == "gaming_stack"            # the plain niche entry still supplies the layout
    assert media.niche_defaults("gaming", "fr")["layout"] == "gaming_stack" and media.niche_defaults(None) == {} and media.niche_defaults("nothing", "en") == {}


def test_a_caption_never_sits_below_the_safe_limit():
    event = media.karaoke_events(LINE, 0.0, 6.0, False, "#FFC400", 1080, 1920, 70, False, "lower", 0.9)[0]["text"]
    assert f"\\pos(540,{int(1920 * media.CAPTION_Y_MAX)})" in event
    assert "\\pos(540,1113)" in media.karaoke_events(LINE, 0.0, 6.0, False, "#FFC400", 1080, 1920, 70, False, "lower", 0.58)[0]["text"]
    two = media.karaoke_events(LINE, 0.0, 6.0, False, "#FFC400", 1080, 1920, 70, True, "lower", None, 2)
    assert max(len(re.sub(r"\{[^}]*\}", "", e["text"]).split()) for e in two) == 2


def test_the_cell_drives_the_render_but_the_templates_own_fields_win(tmp_path):
    src = beep_source(tmp_path)
    words = [w("one", 0.9, 1.3), w("two", 1.4, 1.8)]
    cell = render(tmp_path, src, clip_for(6.0, crop="follow_speaker", language="ar"), words, PLAIN | {"tighten": False}, "c")
    ass = Path(cell["ass_path"]).read_text(encoding="utf-8-sig")
    assert "\\pos(135,288)" in ass and "\\fs" in ass                   # Arabic podcast cell: centre 0.60 of 480
    own = render(tmp_path, src, clip_for(6.0, crop="follow_speaker", language="ar"), words, PLAIN | {"tighten": False, "font_size": 5.0, "caption_position": "middle"}, "d")
    assert "\\pos(135,240)" in Path(own["ass_path"]).read_text(encoding="utf-8-sig")


def test_loudness_target_is_minus_14_on_every_platform():
    from clipping import music
    assert {music.target_loudness(p)[0] for p in ("tiktok", "instagram", "youtube", "facebook", None)} == {-14.0}
