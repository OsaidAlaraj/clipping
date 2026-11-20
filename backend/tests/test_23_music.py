"""Decision 32: the music bed. Catalog and licences, the selector, the single-encode mix (ducking, loudness, no clipping), the licence
record in render_meta. FFmpeg is real; the music adapter is the registry's fake."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipping import campaigns, ffmpeg, music, pipeline
from clipping.adapters import media
from clipping.adapters import music as music_adapters
from clipping.adapters.base import activate
from conftest import add_accounts, approved_campaign  # noqa: F401

CFG = {"width": 270, "height": 480, "preset": "ultrafast", "crf": 30, "encoder": "x264"}
WORDS = [{"text": t, "start": 1.0 + i * 0.5, "end": 1.4 + i * 0.5} for i, t in enumerate("this is a test of the bed under speech".split())]


def pool(**over) -> list[dict]:
    out = []
    for t in music_adapters._FAKE_TRACKS:
        out.append({**t, "path": f"/x/{t['file']}", **over})
    return out


# ------------------------------------------------------------------ the catalog and its licences

def test_every_catalog_track_is_valid_and_carries_a_clear_licence():
    cat = music.catalog()
    assert cat["tracks"], "the catalog is empty"
    ids = [t["id"] for t in cat["tracks"]]
    assert len(ids) == len(set(ids))
    for track in cat["tracks"]:
        assert music.validate_track(track) == [], track["id"]
        assert track["licence_url"].startswith("https://") and track["source_url"].startswith("https://")
        assert cat["accepted_licences"][track["licence"]]["burned_in_commercial"] is True
        assert len(track["sha256"]) == 64
    assert {n for t in cat["tracks"] for n in t["niches"]} == set(music.NICHES)   # every niche has beds
    assert all(sum(1 for t in cat["tracks"] if n in t["niches"]) >= 4 for n in music.NICHES)


def test_an_unclear_licence_or_a_missing_field_makes_a_track_unusable():
    base = dict(music.catalog()["tracks"][0])
    assert music.validate_track({**base, "licence": "CC-BY-NC-4.0"}) == ["licence not accepted"]
    assert "missing source_url" in music.validate_track({**base, "source_url": ""})
    assert "unknown niche" in music.validate_track({**base, "niches": ["politics"]})


def test_the_loader_skips_missing_and_changed_files(tmp_path):
    track = music.catalog()["tracks"][0]
    assert music.tracks(tmp_path) == []                                   # no files: no tracks, no error
    (tmp_path / track["file"]).write_bytes(b"not the real file")
    assert music.tracks(tmp_path) == []                                   # a changed file fails its SHA-256
    assert [t["id"] for t in music.tracks(tmp_path, verify_hash=False)] == [track["id"]]


# ------------------------------------------------------------------ the clip's mood and energy

def test_a_fast_excited_clip_is_more_energetic_than_a_slow_calm_one():
    fast = [{"text": "wow!", "start": i * 0.3, "end": i * 0.3 + 0.25} for i in range(40)]
    slow = [{"text": "so", "start": i * 1.2, "end": i * 1.2 + 0.4} for i in range(10)]
    a, b = music.clip_profile(fast, 0, 12), music.clip_profile(slow, 0, 12)
    assert a["energy"] > b["energy"] + 0.3 and b["mood"] == "calm" and a["mood"] in ("upbeat", "driving")


def test_laughter_and_tension_words_set_the_mood():
    assert music.clip_profile([{"text": "haha", "start": 0, "end": 1}], 0, 5)["mood"] == "playful"
    assert music.clip_profile([{"text": "never", "start": 0, "end": 1}, {"text": "mistake", "start": 1, "end": 2}], 0, 5)["mood"] in ("tense", "mysterious")


# ------------------------------------------------------------------ the selector

def test_the_selector_matches_energy_and_mood():
    calm = music.choose("business", {"mood": "calm", "energy": 0.2, "speech_share": 0.5}, 20, pool=pool())
    hot = music.choose("business", {"mood": "driving", "energy": 0.85, "speech_share": 0.5}, 20, pool=pool())
    assert calm["track"]["id"] == "fake-a" and hot["track"]["id"] == "fake-b"


def test_the_selector_never_repeats_the_previous_track_while_another_fits():
    profile = {"mood": "calm", "energy": 0.2, "speech_share": 0.5}
    first = music.choose("business", profile, 20, pool=pool())
    second = music.choose("business", profile, 20, recent=[first["track"]["id"]], pool=pool())
    assert second["track"]["id"] != first["track"]["id"]
    only = music.choose("business", profile, 20, recent=["fake-a"], pool=pool()[:1])
    assert only["track"]["id"] == "fake-a"                                # one track in the pool: reuse beats silence


def test_a_run_of_clips_uses_several_tracks_and_no_two_in_a_row():
    profile = {"mood": "upbeat", "energy": 0.5, "speech_share": 0.5}
    recent, used = [], []
    for i in range(9):
        pick = music.choose("business", profile, 20, recent=recent, seed=str(i), pool=pool())
        used.append(pick["track"]["id"])
        recent = [pick["track"]["id"], *recent][:5]
    assert all(a != b for a, b in zip(used, used[1:])) and len(set(used)) >= 3


def test_the_niche_limits_the_pool_when_possible():
    only_gaming = [{**t, "niches": ["gaming"]} if t["id"] == "fake-b" else {**t, "niches": ["business"]} for t in pool()]
    assert music.choose("gaming", {"mood": "calm", "energy": 0.1, "speech_share": 0.4}, 20, pool=only_gaming)["track"]["id"] == "fake-b"


def test_speech_heavy_podcasts_get_no_bed_but_roomy_ones_and_other_niches_do():
    heavy = {"mood": "calm", "energy": 0.3, "speech_share": 0.9}
    roomy = {**heavy, "speech_share": 0.4}
    off = music.choose("podcasts", heavy, 20, pool=pool())
    assert off["use"] is False and "speech-heavy" in off["reason"]
    assert music.choose("podcasts", roomy, 20, pool=pool())["use"] is True
    assert music.choose("business", heavy, 20, pool=pool())["use"] is True


def test_no_track_means_no_bed_and_the_offset_sits_on_a_bar_line_inside_the_track():
    assert music.choose("business", {"mood": "calm", "energy": 0.2, "speech_share": 0.2}, 20, pool=[])["use"] is False
    track = pool()[0]
    for seed in "abcdef":
        off = music.offset_for(track, 20, seed)
        bar = 4 * 60 / track["bpm"]
        assert off >= 0 and off + 20 <= track["duration_s"] and abs(off / bar - round(off / bar)) < 0.01


# ------------------------------------------------------------------ the licence record and the caption credit

def test_the_licence_record_names_track_source_licence_and_the_credit_a_by_licence_needs():
    by = [t for t in pool() if t["licence"] == "CC-BY-4.0"][0]
    cc0 = [t for t in pool() if t["licence"] == "CC0-1.0"][0]
    rec, free = music.licence_record(by, 12.0, "tiktok"), music.licence_record(cc0, 0.0, "instagram")
    assert rec["track_id"] == "fake-c" and rec["licence_url"].endswith("/by/4.0/") and rec["attribution_required"]
    assert "Fake C" in music.credit_line(rec) and "CC-BY-4.0" in music.credit_line(rec) and rec["licence_checked"]
    assert free["attribution_required"] is False and music.credit_line(free) is None and music.credit_line(None) is None


# ------------------------------------------------------------------ the mix inside the one encode

def test_the_audio_graph_ducks_the_bed_under_the_voice_and_limits_peaks():
    graph = music.audio_graph({"gain_db": -3.0}, "tiktok", False, duration_s=10)
    assert "sidechaincompress" in graph and "amix=inputs=2" in graph and "alimiter" in graph and "loudnorm=I=-14.0" in graph
    assert "loudnorm" in music.audio_graph(None, "instagram", True) and "sidechain" not in music.audio_graph(None, "instagram", True)
    assert "I=-16.0" in music.audio_graph(None, "facebook", False)
    assert music.target_loudness("nowhere") == music.DEFAULT_LOUDNESS


def measure(path: Path) -> tuple[float, float]:
    proc = subprocess.run([ffmpeg.exe(), "-hide_banner", "-nostdin", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    lufs = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr)[-1])
    peak = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", proc.stderr)[-1])
    return lufs, peak


def speech_source(tmp_path: Path, seconds=14) -> Path:
    """Pulsed tone bursts (voice stand-in: loud, then gaps) on grey video."""
    out = tmp_path / "speech.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=0x808080:s=640x360:r=25", "-f", "lavfi", "-i", "sine=frequency=200:sample_rate=44100",
                "-t", str(seconds), "-af", "volume='if(lt(mod(t,4),2),0.5,0)':eval=frame", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)])
    return out


def test_a_clip_with_a_bed_is_still_one_encode_loud_enough_and_not_clipping(tmp_path, monkeypatch):
    src, track = speech_source(tmp_path), music_adapters.fake_pool()[1]
    calls = []
    real = ffmpeg.run
    monkeypatch.setattr(ffmpeg, "run", lambda args, *a, **k: (calls.append(args), real(args, *a, **k))[1])
    clip = {"start_s": 0.0, "end_s": 10.0, "language": "en", "platform": "tiktok", "crop": "centre", "audio": "speech", "hook_text": None, "emphasis": [],
            "music": {"path": track["path"], "offset_s": 0.0, "gain_db": music.music_gain_db(track), "record": music.licence_record(track, 0.0, "tiktok")}}
    result = media.FFmpegRender({"config_json": CFG}).render_clip(str(src), clip, WORDS, {}, "#ad", str(tmp_path / "o" / "out.mp4"))
    encodes = [c for c in calls if "-filter_complex" in c and "libx264" in c and "-frames:v" not in c]
    assert result["encodes"] == 1 and len(encodes) == 1
    assert result["music"]["track_id"] == "fake-b" and result["loudness_target"] == -14.0
    lufs, peak = measure(Path(result["path"]))
    assert abs(lufs - (-14.0)) < 2.5 and peak < -0.5, (lufs, peak)       # near the TikTok target, never at full scale


def test_the_bed_is_quieter_while_the_voice_speaks_than_in_the_gaps(tmp_path):
    """Ducking depth: the bed's level during the voice bursts versus in the pauses (decode the ducked bed alone)."""
    src, track = speech_source(tmp_path), music_adapters.fake_pool()[0]
    duck = music.duck_graph("[sc]", "[1:a]", "[md]", gain_db=music.music_gain_db(track), duration_s=None)
    wav = tmp_path / "bed.wav"
    ffmpeg.run(["-i", str(src), "-stream_loop", "-1", "-i", track["path"], "-filter_complex", f"[0:a]volume=4[sc];{duck}", "-map", "[md]",
                "-t", "12", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    data = np.frombuffer(wav.read_bytes()[44:], dtype=np.int16).astype(np.float64)
    rms = lambda a, b: 20 * np.log10(np.sqrt(np.mean(data[int(a * 16000):int(b * 16000)] ** 2)) + 1e-9)
    speaking, gap = rms(1.0, 1.9), rms(3.0, 3.9)    # voice on 0-2 s and 4-6 s, off 2-4 s and 6-8 s
    assert gap - speaking > 6.0, (speaking, gap)


def test_a_clip_without_audio_or_with_a_missing_track_file_renders_without_a_bed(tmp_path):
    src = speech_source(tmp_path)
    clip = {"start_s": 0.0, "end_s": 6.0, "language": "en", "platform": "instagram", "crop": "centre", "audio": "speech", "hook_text": None, "emphasis": [],
            "music": {"path": str(tmp_path / "gone.mp3"), "offset_s": 0.0, "gain_db": -3.0, "record": {"track_id": "x"}}}
    result = media.FFmpegRender({"config_json": CFG}).render_clip(str(src), clip, WORDS, {}, "#ad", str(tmp_path / "o2" / "out.mp4"))
    assert result["encodes"] == 1 and result["music"] is None


# ------------------------------------------------------------------ the pipeline: stage, licence log, caption credit

def run_with_fake_music(db, owner, footage, category="lifestyle"):
    with db.session(owner) as conn:
        activate(conn, "music", "fake")
    add_accounts(db, owner)
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        conn.execute("update campaigns set category_id = (select id from categories where name = %s) where id = %s", (category, campaign_id))
        campaigns.register_source(conn, owner, campaign_id, footage, "en", permission_ref="P-1")
    pipeline.run_until_idle(db)
    with db.session(owner) as conn:
        return conn.execute("select render_meta, caption_text from clips order by updated_at").fetchall()


def test_every_rendered_clip_logs_its_track_and_licence_and_no_track_repeats_back_to_back(db, owner, footage):
    clips = run_with_fake_music(db, owner, footage)
    assert len(clips) == 3
    ids = []
    for clip in clips:
        m = clip["render_meta"]["music"]
        assert m["track_id"], m
        assert m["track_id"].startswith("fake-") and m["licence"] and m["licence_url"] and m["source_url"] and m["mood"] and "offset_s" in m
        assert m["attribution_required"] == (m["licence"] == "CC-BY-4.0")
        assert clip["render_meta"]["encodes"] == 1 and clip["render_meta"]["loudness_target_lufs"] == -14.0
        if m["attribution_required"]:                                      # the credit is in the caption
            assert m["attribution_text"] in clip["caption_text"] and clip["render_meta"]["music_credit_in_caption"] is True
        ids.append(m["track_id"])
    assert all(a != b for a, b in zip(ids, ids[1:])), (ids, music_adapters.FAKE_MUSIC["calls"])


def test_a_speech_heavy_podcast_clip_is_logged_as_rendered_without_a_bed(db, owner, footage):
    clips = run_with_fake_music(db, owner, footage, category="podcast")
    assert len(clips) == 3
    for clip in clips:
        m = clip["render_meta"]["music"]
        assert m["track_id"] is None and "speech-heavy" in m["skipped"] and clip["render_meta"]["music_credit_in_caption"] is False


def test_a_failing_music_stage_never_blocks_the_clip(db, owner, footage):
    music_adapters.FAKE_MUSIC["fail"] = True
    try:
        clips = run_with_fake_music(db, owner, footage)
    finally:
        music_adapters.FAKE_MUSIC["fail"] = False
    assert len(clips) == 3 and all(c["render_meta"]["encodes"] == 1 and c["render_meta"]["music"]["track_id"] is None and "failed" in c["render_meta"]["music"]["skipped"] for c in clips)


def test_with_the_real_catalog_and_no_files_the_clip_renders_without_a_bed(db, owner, footage):
    add_accounts(db, owner)
    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        campaigns.register_source(conn, owner, campaign_id, footage, "en", permission_ref="P-1")
        assert conn.execute("select provider from adapters where stage = 'music' and active").fetchone()["provider"] == "catalog"
    pipeline.run_until_idle(db)
    with db.session(owner) as conn:
        metas = [c["render_meta"] for c in conn.execute("select render_meta from clips").fetchall()]
    assert len(metas) == 3 and all(m["music"]["track_id"] is None for m in metas)
