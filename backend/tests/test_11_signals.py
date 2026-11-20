"""Phase D step 3: per-second signals. FFmpeg is real (the 60 s fixture and small synthetic clips); yt-dlp metadata
comes from saved samples in fixtures/ytdlp; the sherpa-onnx model is replaced by a stub module. No network, no model."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipping import config, ffmpeg, pipeline, signals, sources, transcripts
from clipping.adapters import audiotag, heatmap
from clipping.core import AppError
from clipping.db import Actor
from conftest import approved_campaign

YTDLP = config.FIXTURES / "ytdlp"
KEYS = ("loudness", "speech_rate", "turns", "scene", "face", "laughter", "reaction", "chat", "replay")


def word(text, start, end=None, speaker=None):
    return {"text": text, "start": start, "end": start + 0.4 if end is None else end, "confidence": 0.95, "speaker": speaker}


def transcript(words, utterances=None):
    return {"words": words, "utterances": utterances or []}


@pytest.fixture
def made(db, owner, footage):
    """A source registered and taken through the whole pipeline (fake transcript, signals, moments...)."""
    from clipping import campaigns

    campaign_id = approved_campaign(db, owner)
    with db.session(owner) as conn:
        source = campaigns.register_source(conn, owner, campaign_id, footage, "en", permission_ref="TEST-PERMISSION-1")
    pipeline.run_until_idle(db)
    return SimpleNamespace(db=db, owner=owner, source_id=str(source["id"]), campaign_id=campaign_id, footage=footage)


@pytest.fixture
def ffmpeg_calls(monkeypatch):
    """Counts every FFmpeg pass signals starts (probe, audio, video)."""
    calls = []
    for name in ("probe", "audio_pass", "video_thumbs"):
        real = getattr(ffmpeg, name)

        def spy(*a, _real=real, _name=name, **k):
            calls.append(_name)
            return _real(*a, **k)
        monkeypatch.setattr(ffmpeg, name, spy)
    return calls


def lavfi(out: Path, *args: str) -> Path:
    ffmpeg.run([*args, str(out)])
    return out


# ------------------------------------------------------------------ DW-3.1: stored once, reused

def test_DW_3_1_signals_are_stored_after_transcription_and_before_moments(made):
    with made.db.session(made.owner) as conn:
        stored = signals.load(conn, made.source_id)
        order = [r["type"] for r in conn.execute("select type from jobs where source_id = %s order by created_at", (made.source_id,))]
    assert order[:3] == ["transcribe", "signals", "moments"]
    assert stored["step_s"] == 1.0
    assert set(stored["series"]) == set(KEYS)
    for key in KEYS[:7]:
        assert len(stored["series"][key]) == 60, key
    assert stored["series"]["chat"] is None and stored["series"]["replay"] is None
    assert max(stored["series"]["loudness"]) > -40          # the fixture's tone
    assert sum(stored["series"]["speech_rate"]) > 90        # the fake transcript talks four seconds in five
    assert max(stored["series"]["scene"]) > 0                # the test pattern keeps moving
    assert stored["topics"] and stored["topics"][0]["start"] == 0 and stored["topics"][-1]["end"] == 60


def test_DW_3_1_a_second_run_does_no_ffmpeg_work(made, ffmpeg_calls):
    with made.db.session(made.owner) as conn:
        first = signals.load(conn, made.source_id)
        again = signals.compute(conn, made.source_id)
        rows = conn.execute("select count(*) as n from source_signals where source_id = %s", (made.source_id,)).fetchone()["n"]
    assert ffmpeg_calls == []
    assert again == first and rows == 1


def test_load_is_none_before_signals_exist(db, owner):
    with db.session(owner) as conn:
        assert signals.load(conn, "00000000-0000-0000-0000-000000000000") is None


def test_a_corrected_transcript_rebuilds_only_the_text_signals(made, ffmpeg_calls):
    with made.db.session(made.owner) as conn:
        before = signals.load(conn, made.source_id)
        current = transcripts.load(conn, made.source_id)
        words = [dict(w) for w in current["words"]]
        words[0]["text"] = "Wow"
        transcripts.save_version(conn, made.owner, current["id"], words, None, current["version"])
        after = signals.compute(conn, made.source_id)
    assert ffmpeg_calls == []
    assert after["series"]["loudness"] == before["series"]["loudness"]
    assert after["series"]["scene"] == before["series"]["scene"]
    assert after["series"]["reaction"][0] == 1 and before["series"]["reaction"][0] == 0


def test_the_source_signals_table_hides_other_owners_rows(made):
    with made.db.session(Actor.system()) as conn:
        row = conn.execute("insert into operators (name, role) values ('Second owner', 'operator') returning id").fetchone()
    stranger = Actor(str(row["id"]), "operator")
    with made.db.session(stranger) as conn:
        assert signals.load(conn, made.source_id) is None
        assert conn.execute("select count(*) as n from source_signals").fetchone()["n"] == 0
    with made.db.session(made.owner) as conn:
        assert signals.load(conn, made.source_id) is not None


# ------------------------------------------------------------------ errors: corrupted media, odd media

def _corrupt_stored_source(made) -> None:
    with made.db.session(Actor.system()) as conn:
        source = conn.execute("select * from source_assets where id = %s", (made.source_id,)).fetchone()
        path = Path(os.environ["CLIPPING_DATA_DIR"]) / "storage" / source["stored_path"]
        path.write_bytes(b"this is not a video" * 500)
        conn.execute("update source_assets set file_hash = %s where id = %s", (ffmpeg.sha256_file(path), made.source_id))
        conn.execute("update transcripts set source_hash = %s where source_id = %s", (ffmpeg.sha256_file(path), made.source_id))
        conn.execute("delete from source_signals where source_id = %s", (made.source_id,))


def test_a_corrupted_file_is_an_app_error_and_the_job_fails_with_an_alert(made):
    from clipping.core import enqueue

    _corrupt_stored_source(made)
    with made.db.session(Actor.system()) as conn:
        with pytest.raises(AppError) as caught:
            signals.compute(conn, made.source_id)
        assert caught.value.code == "bad_media"
        source = conn.execute("select * from source_assets where id = %s", (made.source_id,)).fetchone()
    with made.db.session(Actor.worker()) as conn:
        enqueue(conn, "signals", {"source_id": made.source_id, "again": 1}, source["owner_operator_id"], source_id=source["id"],
                campaign_version_id=conn.execute("select approved_version_id from campaigns where id = %s", (made.campaign_id,)).fetchone()["approved_version_id"])
    pipeline.run_until_idle(made.db)
    with made.db.session(Actor.system()) as conn:
        job = conn.execute("select * from jobs where type = 'signals' and payload ? 'again'").fetchone()
        alerts = conn.execute("select message from alerts where type = 'job_failed'").fetchall()
    assert job["status"] == "failed" and job["attempts"] == 1
    assert any("signals" in a["message"] for a in alerts)


def test_ffmpeg_passes_raise_on_unreadable_input(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"\x00\x01garbage" * 1000)
    with pytest.raises(ffmpeg.FFmpegError):
        ffmpeg.audio_pass(junk, lambda raw: None)
    with pytest.raises(ffmpeg.FFmpegError):
        list(ffmpeg.video_thumbs(junk, 10))


def test_an_unexpected_signals_failure_raises_an_alert_and_moments_still_run(made, monkeypatch):
    from clipping.core import enqueue

    with made.db.session(Actor.system()) as conn:
        conn.execute("delete from source_signals where source_id = %s", (made.source_id,))
        conn.execute("delete from jobs where type = 'moments'")
        conn.execute("delete from candidates where source_id = %s", (made.source_id,))
        source = conn.execute("select * from source_assets where id = %s", (made.source_id,)).fetchone()
        version = conn.execute("select approved_version_id from campaigns where id = %s", (made.campaign_id,)).fetchone()["approved_version_id"]

    def boom(*args, **kwargs):
        raise RuntimeError("numpy exploded")
    monkeypatch.setattr(signals, "compute", boom)
    with made.db.session(Actor.worker()) as conn:
        enqueue(conn, "signals", {"source_id": made.source_id, "retry": 1}, source["owner_operator_id"], source_id=source["id"],
                campaign_version_id=version)
    pipeline.run_until_idle(made.db)
    with made.db.session(Actor.system()) as conn:
        assert conn.execute("select count(*) as n from alerts where type = 'signals_unavailable'").fetchone()["n"] == 1
        assert conn.execute("select count(*) as n from candidates where source_id = %s", (made.source_id,)).fetchone()["n"] > 0
        assert signals.load(conn, made.source_id) is None


def test_a_video_without_audio_or_picture_still_measures(tmp_path):
    still = lavfi(tmp_path / "silent.mp4", "-f", "lavfi", "-i", "color=c=blue:size=160x90:rate=5", "-t", "4", "-pix_fmt", "yuv420p")
    only = signals.measure_media(still, 4, 4.0, has_audio=False, has_video=True)
    assert only["loudness"] == [-70.0] * 4 and only["laughter"] == [0.0] * 4
    assert only["scene"] == [0.0] * 4
    tone = lavfi(tmp_path / "tone.m4a", "-f", "lavfi", "-i", "sine=frequency=300:duration=4", "-c:a", "aac")
    audio_only = signals.measure_media(tone, 4, 4.0, has_audio=True, has_video=False)
    assert max(audio_only["loudness"]) > -40 and audio_only["scene"] == [0.0] * 4 and audio_only["face"] == [0.0] * 4


def test_the_picture_signals_see_a_cut_and_a_face_colour(tmp_path):
    clip = lavfi(tmp_path / "cut.mp4", "-f", "lavfi", "-i", "color=c=0x1030a0:size=160x90:rate=5:duration=3",
                 "-f", "lavfi", "-i", "color=c=0xe0ac69:size=160x90:rate=5:duration=3",
                 "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p")
    scene, face = signals._measure_video(clip, 6.0)
    assert len(scene) == len(face) == 6
    assert scene[3] == 1.0 and max(scene[:3]) == 0.0 and max(scene[4:]) == 0.0
    assert face[:3] == [0.0, 0.0, 0.0] and min(face[3:]) == 1.0


# ------------------------------------------------------------------ DW-3.2: heatmap

def test_DW_3_2_the_heatmap_is_read_from_a_saved_ytdlp_metadata_sample():
    info = json.loads((YTDLP / "watch_info.json").read_text(encoding="utf-8"))
    segments = heatmap.heatmap_from_info(info)
    assert len(segments) == 20 and segments[10] == {"start_time": 30.0, "end_time": 33.0, "value": 1.0}
    series = heatmap.replay_series(segments, 60)
    assert len(series) == 60 and series[30] == 1.0 and series[0] == 0.12 and series[59] == 0.1


def test_DW_3_2_no_heatmap_is_none_not_zero():
    info = json.loads((YTDLP / "watch_info_no_heatmap.json").read_text(encoding="utf-8"))
    assert heatmap.heatmap_from_info(info) is None
    assert heatmap.replay_series(None, 60) is None and heatmap.replay_series([], 60) is None


@pytest.mark.parametrize("info", [
    None, "text", 5, [], {}, {"heatmap": None}, {"heatmap": "x"}, {"heatmap": {"start_time": 0}}, {"heatmap": [None, 3, "a", []]},
    {"heatmap": [{"start_time": "0", "end_time": "5", "value": "0.5"}]},
    {"heatmap": [{"start_time": float("nan"), "end_time": 5, "value": 0.5}]},
    {"heatmap": [{"start_time": 0, "end_time": float("inf"), "value": 0.5}]},
    {"heatmap": [{"start_time": 9, "end_time": 3, "value": 0.5}]},
    {"heatmap": [{"start_time": -4, "end_time": 3, "value": 0.5}]},
    {"heatmap": [{"start_time": 0, "end_time": 10 ** 9, "value": 0.5}]},
    {"heatmap": [{"start_time": True, "end_time": 5, "value": 0.5}]},
    {"heatmap": [{"start_time": 0, "end_time": 5}]},
])
def test_DW_3_2_malformed_metadata_is_ignored_safely(info):
    assert heatmap.heatmap_from_info(info) is None


def test_a_heatmap_keeps_its_good_segments_clamps_values_and_is_bounded():
    info = {"heatmap": [{"start_time": 0, "end_time": 5, "value": 7}, {"start_time": "bad"}, {"start_time": 5, "end_time": 8, "value": -1}]}
    assert heatmap.heatmap_from_info(info) == [{"start_time": 0.0, "end_time": 5.0, "value": 1.0},
                                               {"start_time": 5.0, "end_time": 8.0, "value": 0.0}]
    many = {"heatmap": [{"start_time": i, "end_time": i + 1, "value": 0.5} for i in range(heatmap.MAX_HEATMAP + 50)]}
    assert len(heatmap.heatmap_from_info(many)) == heatmap.MAX_HEATMAP


def test_DW_3_2_a_stored_heatmap_becomes_the_replay_series(made):
    info = json.loads((YTDLP / "watch_info.json").read_text(encoding="utf-8"))
    with made.db.session(made.owner) as conn:
        source = conn.execute("select * from source_assets where id = %s", (made.source_id,)).fetchone()
        signals.record_inputs(conn, source, heatmap.heatmap_from_info(info), None)
        signals.record_inputs(conn, source, heatmap.heatmap_from_info(info), None)   # twice: an upsert, not a second row
        conn.execute("update source_signals set series = null where source_id = %s", (made.source_id,))
        stored = signals.compute(conn, made.source_id)
    assert stored["series"]["replay"][30] == 1.0 and len(stored["series"]["replay"]) == 60
    assert stored["series"]["chat"] is None


def _fetch_job(made):
    return {"payload": {"campaign_id": made.campaign_id, "url": "https://www.youtube.com/watch?v=fixture0001", "language": "en",
                        "permission_ref": "TEST-PERMISSION-1", "kind": "youtube"}}


def test_DW_3_2_the_youtube_fetch_captures_the_heatmap_and_chat_where_links_are_handled(made, monkeypatch, tmp_path):
    info = json.loads((YTDLP / "watch_info.json").read_text(encoding="utf-8"))

    def fake_download(url, folder, info_out=None):
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "fixture0001.mp4"
        video.write_bytes(Path(made.footage).read_bytes() + b"\x00")   # a new hash: a new source row
        (folder / "fixture0001.live_chat.json").write_text((YTDLP / "live_chat.json").read_text(encoding="utf-8"), encoding="utf-8")
        info_out.update(info)
        return video
    monkeypatch.setattr(sources, "download_youtube", fake_download)
    with made.db.session(Actor.worker()) as conn:
        sources.stage_fetch_source(conn, _fetch_job(made))
    with made.db.session(Actor.system()) as conn:
        row = conn.execute("""select s.id, g.heatmap, g.chat_counts, g.series from source_signals g
                              join source_assets s on s.id = g.source_id where s.origin_url like %s""", ("%fixture0001%",)).fetchone()
    assert len(row["heatmap"]) == 20 and row["series"] is None
    assert [31, 3] in row["chat_counts"]
    pipeline.run_until_idle(made.db)
    with made.db.session(Actor.system()) as conn:
        stored = signals.load(conn, row["id"])
    assert stored["series"]["replay"][30] == 1.0 and stored["series"]["chat"][31] == 3.0


def test_a_youtube_fetch_without_metadata_stores_none(made, monkeypatch):
    def fake_download(url, folder, info_out=None):
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "v.mp4"
        video.write_bytes(Path(made.footage).read_bytes() + b"\x01")
        return video
    monkeypatch.setattr(sources, "download_youtube", fake_download)
    with made.db.session(Actor.worker()) as conn:
        sources.stage_fetch_source(conn, _fetch_job(made))
    pipeline.run_until_idle(made.db)
    with made.db.session(Actor.system()) as conn:
        row = conn.execute("select g.heatmap, g.chat_counts, g.series from source_signals g join source_assets s on s.id = g.source_id "
                           "where s.origin_url like %s", ("%fixture0001%",)).fetchone()
    assert row["heatmap"] is None and row["chat_counts"] is None
    assert row["series"]["replay"] is None and row["series"]["chat"] is None


def test_a_direct_link_fetch_records_no_youtube_inputs(made, monkeypatch):
    def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(made.footage).read_bytes() + b"\x02")
        return dest
    monkeypatch.setattr(sources, "download_to", fake_download)
    job = _fetch_job(made)
    job["payload"].update(kind="direct", url="https://example.com/v.mp4")
    with made.db.session(Actor.worker()) as conn:
        sources.stage_fetch_source(conn, job)
        assert conn.execute("select count(*) as n from source_signals g join source_assets s on s.id = g.source_id "
                            "where s.origin_url = 'https://example.com/v.mp4'").fetchone()["n"] == 0


def test_youtube_download_hands_back_the_heatmap_through_info_out(monkeypatch, tmp_path):
    info = json.loads((YTDLP / "watch_info.json").read_text(encoding="utf-8"))
    seen = {}

    class FakeYDL:
        def __init__(self, options):
            seen.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            return {**info, "ext": "mp4"}

        def prepare_filename(self, i):
            return str(tmp_path / "x.mp4")
    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    out: dict = {}
    assert sources.download_youtube("https://youtu.be/x", tmp_path / "dl", out) == tmp_path / "x.mp4"
    assert len(out["heatmap"]) == 20 and seen["subtitleslangs"] == ["live_chat"]
    assert sources.download_youtube("https://youtu.be/x", tmp_path / "dl") == tmp_path / "x.mp4"   # info_out is optional


# ------------------------------------------------------------------ DW-3.4: chat bursts

def test_DW_3_4_chat_bursts_are_counted_per_second_from_a_saved_replay():
    text = (YTDLP / "live_chat.json").read_text(encoding="utf-8")
    counts = heatmap.chat_counts_from_live_chat(text)
    assert counts == [[2, 1], [9, 1], [15, 1], [31, 3], [32, 3], [33, 2], [48, 1]]   # offsets before the start or unreadable are dropped
    series = heatmap.chat_series(counts, 60)
    assert series[31] == 3 and series[0] == 0 and sum(series) == 12


def test_DW_3_4_chat_is_absent_without_a_replay():
    assert heatmap.chat_series(None, 60) is None and heatmap.chat_series([], 60) is None
    assert heatmap.chat_counts_from_live_chat("") is None
    assert heatmap.chat_counts_from_live_chat("not json\n[1, 2]\n{}\n") is None
    assert heatmap.read_live_chat(Path("does-not-exist.json")) is None


def test_chat_files_are_bounded_and_odd_pairs_skipped(tmp_path, monkeypatch):
    path = tmp_path / "x.live_chat.json"
    path.write_text((YTDLP / "live_chat.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert heatmap.read_live_chat(path)[0] == [2, 1]
    monkeypatch.setattr(heatmap, "MAX_CHAT_BYTES", 10)
    assert heatmap.read_live_chat(path) is None
    assert heatmap.chat_series([[1, 2], ["x"], [None, 1], [999, 5], [-1, 1], [3, float("nan")]], 10) == [0, 2, 0, 0, 0, 0, 0, 0, 0, 0]
    assert heatmap.chat_counts_from_live_chat('{"replayChatItemAction": {"videoOffsetTimeMsec": "999999999999"}}') is None


# ------------------------------------------------------------------ DW-3.4: reaction words (English and Arabic)

def test_DW_3_4_reaction_words_in_english_and_arabic():
    words = [word("Honestly", 0), word("no", 3.0), word("way!", 3.4), word("it", 4.0), word("works", 4.4),
             word("وَاللَّهِ", 10.0), word("مستحيل", 11.2), word("يا", 20.0), word("الله", 20.4), word("fine", 25)]
    out = signals.text_signals(transcript(words), [-20.0] * 30, 30)
    reaction = out["reaction"]
    assert reaction[3] == 1 and reaction[10] == 1 and reaction[11] == 1 and reaction[20] == 1
    assert sum(reaction) == 4


def test_DW_3_4_reactions_are_absent_without_matching_words_or_words():
    plain = [word("the", 0), word("quarterly", 1), word("report", 2), word("noway", 3)]
    assert sum(signals.text_signals(transcript(plain), [-20.0] * 10, 10)["reaction"]) == 0
    assert signals.text_signals(transcript([]), [-20.0] * 10, 10)["reaction"] == [0.0] * 10


def test_reaction_matching_ignores_case_punctuation_and_alef_forms():
    words = [word("OH", 1), word("My", 1.3), word("God,", 1.6), word("يا", 5), word("إلهي!", 5.4), word("Wow...", 7)]
    reaction = signals.text_signals(transcript(words), [-20.0] * 10, 10)["reaction"]
    assert reaction[1] == 1 and reaction[5] == 1 and reaction[7] == 1


# ------------------------------------------------------------------ speech signals

def test_speech_rate_is_zero_in_pauses_and_in_silent_stretches():
    words = [word(f"w{i}", i * 0.25, i * 0.25 + 0.2) for i in range(8)] + [word("late", 6.0, 6.5)]
    loud = [-20.0] * 10
    out = signals.text_signals(transcript(words), loud, 10)
    assert out["speech_rate"][:2] == [4.0, 4.0] and out["speech_rate"][2:6] == [0.0] * 4 and out["speech_rate"][6] > 0
    quiet = [-20.0] * 6 + [-70.0] * 4          # the same words, but the audio is silent from second 6 (music or nothing)
    assert signals.text_signals(transcript(words), quiet, 10)["speech_rate"][6] == 0.0


def test_a_word_with_a_wild_time_span_cannot_smear_the_series():
    words = [word("long", 0, 5000.0), word("bad", -3, -1), word("late", 99, 100)]
    rate = signals.text_signals(transcript(words), [-20.0] * 20, 20)["speech_rate"]
    assert len(rate) == 20 and all(v >= 0 for v in rate)
    assert sum(rate[:10]) == pytest.approx(1.0 + 1.0)       # the long word spreads over its first 10 s, the negative one lands at 0
    assert sum(rate[10:]) == 0                               # nothing past the cap, and the word at 99 s is outside the file


def test_speaker_turns_are_counted_where_the_speaker_changes_and_not_in_silence():
    utts = [{"start": 0, "end": 4, "speaker": "0"}, {"start": 5.2, "end": 8, "speaker": "1"},
            {"start": 9.0, "end": 9.5, "speaker": None}, {"start": 12.0, "end": 14, "speaker": "1"},
            {"start": 15.5, "end": 17, "speaker": "0"}]
    loud = [-20.0] * 20
    turns = signals.text_signals(transcript([], utts), loud, 20)["turns"]
    assert turns[5] == 1 and turns[15] == 1 and sum(turns) == 2        # a None speaker and a repeat are not turns
    loud[5] = -70.0
    assert signals.text_signals(transcript([], utts), loud, 20)["turns"][5] == 0
    assert sum(signals.text_signals(transcript([], [{"start": 0, "end": 1, "speaker": None}, {"start": 2, "end": 3, "speaker": None}]),
                                    loud, 20)["turns"]) == 0


# ------------------------------------------------------------------ topics

def _talk(vocab, start, seconds):
    words, t, i = [], start, 0
    while t < start + seconds:
        words.append(word(vocab[i % len(vocab)], t, t + 0.3))
        t += 0.45
        i += 1
    return words


def test_topic_segmentation_cuts_where_the_vocabulary_changes():
    cooking = ["recipe", "flour", "butter", "oven", "baking", "dough", "sugar", "kitchen"]
    cars = ["engine", "turbo", "gearbox", "tyres", "brakes", "racing", "garage", "exhaust"]
    words = _talk(cooking, 0, 200) + _talk(cars, 200, 200)
    topics = signals.topic_segments(words, 400.0)
    assert len(topics) == 2
    assert abs(topics[0]["end"] - 200) <= 15 and topics[0]["end"] == topics[1]["start"]
    assert topics[0]["start"] == 0 and topics[1]["end"] == 400
    assert set(topics[0]["title"].split()) <= set(cooking) and set(topics[1]["title"].split()) <= set(cars)


def test_topic_segmentation_handles_arabic_and_short_or_empty_transcripts():
    arabic = ["الطبيعة", "الجبال", "الأشجار", "الغابات", "الأنهار", "الحيوانات"]
    economy = ["الاقتصاد", "التضخم", "الأسعار", "الاستثمار", "الأسواق", "العملات"]
    topics = signals.topic_segments(_talk(arabic, 0, 180) + _talk(economy, 180, 180), 360.0)
    assert len(topics) == 2 and topics[0]["title"] and abs(topics[0]["end"] - 180) <= 15
    assert signals.topic_segments(_talk(["alpha", "bravo", "charlie"], 0, 30), 30.0) == [
        {"start": 0.0, "end": 30.0, "title": "alpha bravo charlie"}]
    assert signals.topic_segments([], 45.0) == [{"start": 0.0, "end": 45.0, "title": ""}]


def test_a_single_subject_stays_one_topic():
    assert len(signals.topic_segments(_talk(["market", "growth", "revenue", "customers", "pricing"], 0, 400), 400.0)) == 1


# ------------------------------------------------------------------ DW-3.4: laughter and applause

def _laugh_clip(tmp_path: Path) -> Path:
    """Quiet low tone for 10 s, three seconds of pulsed noise (a crowd laughing), then the tone again."""
    return lavfi(tmp_path / "laugh.wav", "-f", "lavfi", "-i", "sine=frequency=200:duration=10,volume=0.05",
                 "-f", "lavfi", "-i", "anoisesrc=d=3:amplitude=0.8:color=white,tremolo=f=6:d=0.9",
                 "-f", "lavfi", "-i", "sine=frequency=200:duration=5,volume=0.05",
                 "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1", "-ar", "44100")


def test_DW_3_4_laughter_is_found_in_a_laugh_clip_by_the_loudness_burst_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(audiotag, "model_dir", lambda: tmp_path / "no-model")
    assert audiotag.pick_tagger().name == "loudness_burst"
    loudness, laughter = signals._measure_audio(_laugh_clip(tmp_path))
    assert len(loudness) == len(laughter) == 18
    assert max(laughter[10:13]) >= 0.5
    assert sum(laughter[:9]) == 0 and sum(laughter[14:]) == 0       # the steady tone before and after is not laughter


def test_steady_loud_speech_is_not_laughter(tmp_path, monkeypatch):
    monkeypatch.setattr(audiotag, "model_dir", lambda: tmp_path / "no-model")
    steady = lavfi(tmp_path / "steady.wav", "-f", "lavfi", "-i", "sine=frequency=200:duration=20", "-ar", "44100")
    assert max(signals._measure_audio(steady)[1]) == 0


def test_burst_tagger_ignores_silence_and_short_inputs():
    tagger = audiotag.BurstTagger()
    assert tagger.finish([]) == []
    assert tagger.finish([-70.0, -70.0, -10.0]) == [0.0, 0.0, 0.0]       # silence, and no zero-crossing data was observed


@pytest.fixture
def stub_sherpa(tmp_path, monkeypatch):
    """A stand-in for the sherpa_onnx package that calls "laughter" in any window of loud samples."""
    folder = tmp_path / "models"
    folder.mkdir()
    (folder / "model.int8.onnx").write_bytes(b"x")
    (folder / "class_labels_indices.csv").write_text("index,mid,display_name\n", encoding="utf-8")
    monkeypatch.setattr(audiotag, "model_dir", lambda: folder)
    state = {"built": 0, "fail": False}

    class Stream:
        def accept_waveform(self, sample_rate, waveform):
            self.loud = float(abs(waveform).max()) > 0.1

    class AudioTagging:
        def __init__(self, cfg):
            if state["fail"]:
                raise RuntimeError("bad model")
            state["built"] += 1

        def create_stream(self):
            return Stream()

        def compute(self, stream):
            return [SimpleNamespace(name="Laughter" if stream.loud else "Speech", prob=0.9 if stream.loud else 0.8)]

    module = SimpleNamespace(AudioTagging=AudioTagging, AudioTaggingConfig=lambda **k: k, AudioTaggingModelConfig=lambda **k: k)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    monkeypatch.setattr(audiotag.importlib.util, "find_spec", lambda name: object() if name == "sherpa_onnx" else None)
    return state


def test_DW_3_4_the_model_tagger_is_used_when_installed_and_scores_whole_windows(stub_sherpa, tmp_path):
    tagger = audiotag.pick_tagger()
    assert tagger.name == "sherpa_onnx" and stub_sherpa["built"] == 1
    import numpy as np

    quiet, loud = np.zeros(16000, dtype=np.int16), np.full(16000, 20000, dtype=np.int16)
    for chunk in (quiet, quiet, quiet, loud, loud, loud, quiet):      # two full windows and a one-second remainder
        tagger.observe(chunk)
    assert tagger.finish([-30.0] * 8) == [0.0, 0.0, 0.0, 0.9, 0.9, 0.9, 0.0, 0.0]
    assert tagger.finish([-30.0] * 5) == [0.0, 0.0, 0.0, 0.9, 0.9]                  # never longer than the loudness series


def test_DW_3_4_a_broken_model_falls_back_to_the_burst_heuristic(stub_sherpa):
    stub_sherpa["fail"] = True
    assert audiotag.pick_tagger().name == "loudness_burst"


def test_DW_3_4_the_model_is_absent_when_the_package_or_files_are_missing(stub_sherpa, tmp_path, monkeypatch):
    monkeypatch.setattr(audiotag, "model_dir", lambda: tmp_path / "empty")
    assert audiotag.pick_tagger().name == "loudness_burst"
    monkeypatch.undo()
    assert audiotag.model_dir().name == "audio-tagging"          # the default lives under the data directory's cache
    assert "cache" in audiotag.model_dir().parts


# ------------------------------------------------------------------ DW-3.3: timing

def _long_clip(tmp_path: Path, seconds: int) -> Path:
    return lavfi(tmp_path / "long.mp4", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=10:duration={seconds}",
                 "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                 "-g", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest")


def test_DW_3_3_a_synthetic_long_file_is_measured_fast_enough_for_two_hours_in_three_minutes(tmp_path):
    """A 6-minute 720p synthetic file stands in for the long file; the 2-hour figure is extrapolated linearly.
    CLIPPING_LONG_TEST=1 builds and measures the real 2-hour file instead (about 5 minutes of build time)."""
    seconds = 7200 if os.environ.get("CLIPPING_LONG_TEST") == "1" else 360
    clip = _long_clip(tmp_path, seconds)
    started = time.perf_counter()
    series = signals.measure_media(clip, seconds, float(seconds), True, True)
    elapsed = time.perf_counter() - started
    assert len(series["loudness"]) == len(series["scene"]) == seconds
    projected = elapsed * 7200 / seconds
    print(f"signals: {seconds}s of media in {elapsed:.1f}s; 2 h projected {projected:.0f}s")
    assert projected < 180


def test_long_videos_decode_keyframes_only_and_still_fill_every_second(tmp_path):
    clip = _long_clip(tmp_path, 40)
    series = signals.measure_media(clip, 40, float(ffmpeg.FULL_DECODE_MAX_S + 1), True, True)   # claims to be long: keyframe mode
    assert len(series["scene"]) == 40 and max(series["scene"]) > 0
