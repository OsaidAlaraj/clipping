"""Face tracking for the crop: the smoothed path, the ffmpeg expression, and the fallbacks."""
from clipping import faces, ffmpeg, speaker
from clipping.adapters import media


def test_smoothing_holds_still_inside_the_dead_zone_and_jumps_on_a_cut():
    still = faces.smooth([0.5, 0.52, 0.48, 0.51])
    assert max(still) - min(still) < 0.01
    cut = faces.smooth([0.2] * 5 + [0.8] * 3)
    assert abs(cut[5] - 0.8) < 1e-9            # a shot cut moves the crop at once, it does not glide across the picture


def test_smoothing_holds_when_the_face_is_lost_then_returns_to_the_middle():
    path = faces.smooth([0.2, 0.2] + [None] * 12)
    assert path[2] == path[9] and path[-1] == 0.5


def test_keyframes_are_thinned_and_clamped_inside_the_picture():
    pts = faces.keyframes([0.01, 0.01, 0.01, 0.9], 1 / 3, 0.316)
    assert pts[0][1] >= 0.158 and pts[-1][1] <= 1 - 0.158 and len(pts) <= 3


def test_the_crop_expression_is_a_valid_ffmpeg_filter(tmp_path):
    path = [(0.0, 0.3), (1.0, 0.3), (1.05, 0.7), (2.0, 0.7)]
    graph = media.crop_filter(1280, 720, 1080, 1920, 0.5, path)
    assert "if(lt(t," in graph and "crop=404:720" in graph
    out = tmp_path / "o.mp4"
    ffmpeg.run(["-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=10:duration=2", "-vf", graph, str(out)])
    assert out.exists() and out.stat().st_size > 0


def test_without_a_path_the_crop_is_the_old_fixed_one():
    assert media.crop_filter(1280, 720, 1080, 1920, 0.5) == media.crop_filter(1280, 720, 1080, 1920, 0.5, None)
    assert "'" not in media.crop_filter(1280, 720, 1080, 1920, 0.5)


def test_track_returns_none_when_nobody_is_in_the_picture(tmp_path):
    src = ffmpeg.make_fixture(tmp_path / "f.mp4", seconds=3)
    assert faces.track(src, {"start_s": 0, "end_s": 2}) is None


# ---- speaker choice (docs/bakeoff/speaker): the crop follows the face that talks ----

def _track(frames, centre, area=0.05, shade=128):
    import numpy as np
    n = len(frames)
    return {"frames": np.array(frames), "centre": np.full(n, centre), "area": area, "crops": np.full((n, 112, 112), shade, np.uint8)}


def test_pick_follows_the_speaking_face_not_the_big_one():
    import numpy as np
    big, small = _track(range(100), 0.3, area=0.2), _track(range(100), 0.8, area=0.02)
    got = speaker.pick([big, small], [np.full(100, 0.1), np.full(100, 0.9)], [10, 50, 90])
    assert got == [0.8, 0.8, 0.8]


def test_pick_needs_a_margin_to_switch_and_a_floor_to_move():
    import numpy as np
    a, b = _track(range(100), 0.2), _track(range(100), 0.8)
    assert speaker.pick([a, b], [np.full(100, 0.80), np.full(100, 0.85)], [10, 50]) == [0.8, 0.8]
    assert speaker.pick([a, b], [np.full(100, 0.99), np.full(100, 0.85)], [10, 50]) == [0.2, 0.2]
    rise = np.concatenate([np.full(40, 0.5), np.full(60, 0.9)])                       # 0.9 vs 0.85: a gain below the margin does not move the crop
    assert speaker.pick([a, b], [rise, np.full(100, 0.85)], [10, 90]) == [0.8, 0.8]
    quiet = speaker.pick([_track(range(100), 0.2, area=0.01), _track(range(100), 0.8, area=0.3)], [np.full(100, 0.2), np.full(100, 0.3)], [10, 50])
    assert quiet == [0.8, 0.8]                                                           # nobody speaks: the biggest face


def test_mfcc_has_thirteen_cepstra_at_100_frames_a_second():
    import numpy as np
    feats = speaker.mfcc((np.sin(np.arange(16000) / 5) * 3000).astype(np.int16))
    assert feats.shape[1] == 13 and 99 <= feats.shape[0] <= 101 and np.isfinite(feats).all()


def test_the_crop_follows_the_scripted_speaker_and_falls_back_when_the_scorer_breaks(tmp_path):
    src = ffmpeg.make_fixture(tmp_path / "f.mp4", seconds=4)
    tracks = [_track(range(100), 0.25, shade=40), _track(range(100), 0.75, shade=220)]
    fn = lambda s, a, e: (tracks, 100, 640)
    got = faces.track(src, {"start_s": 0, "end_s": 4}, scorer=speaker.ScriptedScorer(), tracks_fn=fn)   # the bright face "speaks"
    assert got and abs(got[1] - 0.75) < 0.01 and all(c > 0.6 for _, c in got[0])

    class Broken:
        def score(self, *a): raise RuntimeError("model gone")
    assert faces.track(src, {"start_s": 0, "end_s": 4}, scorer=Broken(), tracks_fn=fn) is None          # falls back to YuNet: no real face in the fixture


def test_the_real_onnx_model_scores_every_frame():
    import numpy as np
    sc = speaker.default_scorer()
    if sc is None:
        import pytest
        pytest.skip("onnxruntime or the model is missing")
    out = sc.score(np.random.randint(0, 255, (60, 112, 112), dtype=np.uint8), np.random.randn(240, 13))
    assert out.shape == (60,) and ((out >= 0) & (out <= 1)).all()


def test_shot_path_steps_on_a_source_cut_and_never_slides_across_the_picture():
    pts = faces.shot_keyframes([0.2] * 6 + [0.8] * 6, 1 / 3, 0.316, [2.0])
    assert (1.999, 0.2) in pts and (2.0, 0.8) in pts           # one frame between the two positions
    assert all(not (0.2 < c < 0.8) for _, c in pts)           # no keyframe in between: nothing to glide through


def test_shot_path_starts_a_shot_on_its_own_face_and_ignores_a_one_sample_blip():
    pts = faces.shot_keyframes([0.3] * 4 + [0.8] + [0.3] * 7, 1 / 3, 0.316, [])
    assert [c for _, c in pts] == [0.3, 0.3]                   # the blip at 0.8 never moves the camera
    switched = faces.shot_keyframes([0.3] * 4 + [0.8] * 8, 1 / 3, 0.316, [])
    assert any(c == 0.8 for _, c in switched)                  # a lasting speaker change does


def test_snap_edges_moves_the_start_into_the_silence_before_the_first_word(tmp_path):
    import numpy as np
    from clipping import polish
    rate = 16000
    t = np.arange(rate * 10) / rate
    tone = (0.3 * np.sin(2 * np.pi * 220 * t) * ((t > 2.0) & (t < 8.0))).astype(np.float32)      # speech from 2.0 to 8.0 s
    wav = tmp_path / "a.wav"
    import wave
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate); f.writeframes((tone * 32767).astype(np.int16).tobytes())
    start, end = polish.snap_edges(wav, 2.0, 8.0)           # a cut placed exactly on the first sound
    assert start < 2.0 and start >= 1.8 and end >= 8.0
