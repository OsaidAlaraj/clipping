"""Face tracking for the crop: the smoothed path, the ffmpeg expression, and the fallbacks."""
from clipping import faces, ffmpeg
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
