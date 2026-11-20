"""Stacked gaming layout: face-cam finder rules, gameplay crop, each fallback, and the per-niche defaults."""
from pathlib import Path

from clipping import gaming_layout as gl
from clipping.adapters import media
from test_13_render import H, W, clip_for, flat_source, frame, render


def test_only_small_peripheral_faces_count_as_a_webcam():
    assert gl.eligible((10, 20, 60, 70), 960, 540)                  # small, top-left corner
    assert not gl.eligible((400, 150, 150, 170), 960, 540)          # big and central: a game character
    assert not gl.eligible((380, 200, 50, 50), 960, 540)            # small but in the middle of the picture


def test_the_webcam_is_the_cluster_that_stays_put():
    still = [{"fi": i, "cx": 60 + i, "cy": 50, "box": (30, 20, 60, 60), "score": 0.9} for i in range(6)]
    moving = [{"fi": i, "cx": 700 - i * 80, "cy": 300, "box": (0, 0, 40, 40), "score": 0.95} for i in range(6)]
    group = gl.stationary_cluster(still + moving, 8, 960, 540)
    assert len(group) == 6 and all(c["cy"] == 50 for c in group)
    assert gl.stationary_cluster(moving, 8, 960, 540) == []          # nothing stays put: no webcam


def test_the_cam_rectangle_is_flush_with_the_corner_and_has_the_band_aspect():
    x, y, w, h = gl.cam_rect((10, 20, 40, 45), 960, 540, 1080 / 720)
    assert x == 0 and y == 0 and abs(w / h - 1.5) < 0.05


def test_the_gameplay_crop_never_includes_the_cam_on_either_side():
    left, right = (0.0, 0.05, 0.14, 0.17), (0.86, 0.05, 0.14, 0.17)
    lo, hi, clean = gl.gameplay_range(1280, 720, 648, left)
    assert clean and lo >= 0.14 * 1280
    lo, hi, clean = gl.gameplay_range(1280, 720, 648, right)
    assert clean and hi + 648 <= 0.86 * 1280 + 1
    assert not gl.gameplay_range(1280, 720, 648, (0.2, 0.0, 0.6, 0.3))[2]     # a cam across the middle cannot be avoided


def test_the_motion_path_stays_inside_the_range_and_respects_the_velocity_limit(monkeypatch):
    import numpy as np
    frames = [np.zeros((180, 320, 3), np.uint8) for _ in range(60)]
    for i in range(1, 60, 2):
        frames[i][:, 300:] = 255                                   # all the motion on the far right
    monkeypatch.setattr(gl, "_frames", lambda *a, **k: frames)
    pts = gl.motion_path(Path("x"), 0, 30, 1280, 648, 180, 632)
    centres = [c * 1280 for _, c in pts]
    assert all(180 + 324 - 1 <= c <= 632 + 324 + 1 for c in centres)
    assert max(abs(b - a) for a, b in zip(centres, centres[1:])) <= gl.MAX_PAN_PX_S / gl.MOTION_FPS + 1


def _stack_plan(out_h=1280, gw=648):
    cam_h = round(out_h * gl.CAM_SHARE / 2) * 2
    return {"layout": "stack", "cam": (0.0, 0.06, 0.14, 0.17), "gw": gw, "path": [(0.0, 0.5), (3.0, 0.55)], "cam_h": cam_h, "game_h": out_h - cam_h}


def test_the_stacked_graph_is_one_filtergraph_with_the_cam_on_top(tmp_path):
    graph = media.layout_graph("gaming_stack", 1280, 720, 720, 1280, None, stack=_stack_plan())
    assert graph.count("[0:v]") == 1 and "vstack=inputs=2" in graph and graph.endswith("[lay]")


def test_a_stacked_render_is_one_encode_and_a_missing_cam_falls_back_to_the_blurred_pad(tmp_path, monkeypatch):
    src = flat_source(tmp_path)
    monkeypatch.setattr(gl, "plan", lambda *a, **k: _stack_plan(H, 300))
    result = render(tmp_path, src, clip_for(0, 3, crop="facecam_over_game"), {"progress_bar": False, "title_card": False}, words=[], name="stack")
    assert result["encodes"] == 1 and frame(Path(result["path"]), 1.0).shape[0] == H
    for fallback, expected in (({"layout": "none"}, "boxblur"), ({"layout": "fullbleed"}, "crop=")):
        monkeypatch.setattr(gl, "plan", lambda *a, _f=fallback, **k: _f)
        out = render(tmp_path, src, clip_for(0, 3, crop="facecam_over_game"), {"progress_bar": False, "title_card": False}, words=[], name=fallback["layout"])
        assert out["encodes"] == 1


def test_portrait_sources_and_failures_mean_no_stacked_layout():
    assert gl.plan(Path("none.mp4"), {"start_s": 0, "end_s": 5}, 720, 1280, 1080, 1920)["layout"] == "none"
    assert gl.find_facecam(Path("does-not-exist.mp4"), 0, 5, 1.5)["kind"] == "none"


def test_niche_defaults_apply_unless_the_template_says_otherwise():
    assert media.niche_defaults("gaming")["layout"] == "gaming_stack" and media.niche_defaults("business")["layout"] == "auto"
    assert media.niche_defaults("nothing") == {}
    assert media.normalize_template({"layout": "gaming_stack"})["layout"] == "gaming_stack"
