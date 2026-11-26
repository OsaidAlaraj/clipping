"""Clip layouts: single, gaming, split and reaction; the safe areas, the suggestion, the per-clip override and the one encode.
FFmpeg is real; faces are given to the planner as detections (YuNet needs real faces, the geometry and the graph are what is tested)."""
from __future__ import annotations

import pytest

from clipping import ffmpeg, layouts, pipeline, review
from clipping.core import AppError
from test_13_render import Spy, adapter, clip_for, frame  # noqa: F401

OW, OH = 1080, 1920


def box(x, y=0.3, w=0.1, h=0.2):
    return (x, y, w, h, 0.9)


def seen_of(*boxes_per_frame, n=8):
    return {"faces": [list(boxes_per_frame)] * n, "aspect": 16 / 9}


def pair_source(tmp_path, size="1280x720"):
    """Left half red, right half blue: which panel shows which side is visible in the output."""
    out = tmp_path / "pair.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", f"color=c=0x808080:s={size}:r=25", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100", "-t", "5",
                "-vf", "drawbox=x=0:y=0:w=iw/2:h=ih:color=0xC02020:t=fill,drawbox=x=iw/2:y=0:w=iw/2:h=ih:color=0x2020C0:t=fill",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)])
    return out


# ------------------------------------------------------------------ geometry and the safe areas

def test_two_faces_get_one_panel_each_left_person_on_top_and_nothing_under_the_overlays():
    p = layouts.plan_split(seen_of(box(0.22), box(0.68)), 1920, 1080, OW, OH)
    assert p["layout"] == "split" and p["mode"] == "two_faces"
    assert p["crops"][0][0] < p["crops"][1][0]                      # the left person is the top panel
    assert layouts.violations(p, OW, OH) == []
    assert all(f[0] >= 0 and f[0] + f[2] <= OW for f in p["faces_out"])        # nobody cut off at the sides
    top, bottom = p["faces_out"]
    assert top[1] > OH * layouts.HOOK_BAND[1] and top[1] + top[3] < OH * 0.5 - OH * layouts.SEAM_BAND    # between the hook title and the seam captions
    assert bottom[1] > OH * 0.5 + OH * layouts.SEAM_BAND and bottom[1] + bottom[3] < OH * layouts.BOTTOM_BAND[0]


def test_a_neighbour_is_kept_out_of_each_panel():
    p = layouts.plan_split(seen_of(box(0.30), box(0.55)), 1920, 1080, OW, OH)
    left, right = p["crops"]
    sep = (0.55 - 0.30) * 1920
    assert left[2] <= sep * 0.92 + 2 and right[2] <= sep * 0.92 + 2


def test_three_people_or_a_poster_are_not_a_pair():
    assert layouts.face_pair(seen_of(box(0.1), box(0.4), box(0.7))) is None
    assert layouts.face_pair(seen_of(box(0.3, w=0.1, h=0.2), box(0.4, w=0.02, h=0.04))) is None        # tiny second face: not a person
    assert layouts.face_pair(seen_of(box(0.30), box(0.36))) is None                                    # too close together
    assert layouts.plan_split(seen_of(box(0.4)), 1920, 1080, OW, OH)["layout"] == "none"            # one face and no feed


def test_one_person_and_a_feed_make_a_split_with_the_person_on_top():
    p = layouts.plan_split(seen_of(box(0.45)), 1920, 1080, OW, OH, {"path": "x.mp4", "offset_s": 3})
    assert p["layout"] == "split" and p["mode"] == "feed" and len(p["crops"]) == 1 and layouts.violations(p, OW, OH) == []


def test_reaction_puts_the_content_card_where_it_covers_no_face_caption_or_title():
    p = layouts.plan_reaction(seen_of(box(0.45, 0.3, 0.07, 0.16)), 1920, 1080, OW, OH)
    assert p["layout"] == "reaction" and layouts.violations(p, OW, OH) == []
    pip, face = p["pip"], p["faces_out"][0]
    assert pip["y"] + pip["h"] <= OH * layouts.CAPTION_BAND[0] and pip["x"] + pip["w"] <= OW
    assert pip["y"] >= OH * layouts.HOOK_BAND[1] and not layouts._overlap((pip["x"], pip["y"], pip["w"], pip["h"]), face, OH * layouts.FACE_MARGIN)


def test_a_close_up_face_has_no_room_for_a_card_so_the_plan_says_so():
    p = layouts.plan_reaction(seen_of(box(0.40, 0.10, 0.20, 0.50)), 1920, 1080, OW, OH)
    assert p["layout"] == "none" and "card" in p["reason"]


def test_a_plan_that_breaks_a_safe_area_is_refused_not_rendered(tmp_path):
    seen = seen_of(box(0.2, 0.0, 0.1, 0.2), box(0.7, 0.0, 0.1, 0.2))        # heads at the very top of the picture: under the hook title
    got = layouts.plan("split", tmp_path / "x.mp4", {"start_s": 0, "end_s": 5}, 1920, 1080, OW, OH, seen)
    assert got["layout"] == "none" and "hook title" in got["reason"]


def test_a_vertical_picture_never_gets_a_split_or_a_reaction():
    assert layouts.plan("split", None, {"start_s": 0, "end_s": 5}, 720, 1280, OW, OH, seen_of(box(0.4)))["layout"] == "none"
    assert layouts.suggest(None, 720, 1280, seen_of(box(0.4)))["layout"] == "single"


# ------------------------------------------------------------------ the suggestion

def test_suggestion_by_niche_signals_and_the_campaigns_edit_style():
    two = seen_of(box(0.22), box(0.68))
    assert layouts.suggest("gaming", 1920, 1080, two)["layout"] == "gaming"
    assert layouts.suggest("podcast", 1920, 1080, two)["layout"] == "split"
    assert layouts.suggest("podcast", 1920, 1080, seen_of(box(0.45)))["layout"] == "single"
    one = seen_of(box(0.45, 0.3, 0.07, 0.16))
    assert layouts.suggest(None, 1920, 1080, one, "Split screens and over-the-top reactions work")["layout"] == "single"     # a split needs a feed
    assert layouts.suggest(None, 1920, 1080, one, "Split screens and over-the-top reactions work", has_feed=True)["layout"] == "split"
    assert layouts.suggest(None, 1920, 1080, one, "Over-the-top reaction first")["layout"] == "reaction"
    assert layouts.hint_of("شاشة منقسمة وردة فعل") == "split" and layouts.hint_of("plain talking head") is None
    assert layouts.suggest(None, 1920, 1080, one)["why"]


def test_the_same_clip_gets_the_same_feed_and_an_empty_folder_gets_none(tmp_path):
    assert layouts.pick_feed(tmp_path, "a") is None and layouts.pick_feed(None, "a") is None
    for name in ("a.mp4", "b.mp4", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    assert layouts.pick_feed(tmp_path, "clip-1") == layouts.pick_feed(tmp_path, "clip-1")
    assert layouts.pick_feed(tmp_path, "clip-1")["path"].endswith(".mp4")


# ------------------------------------------------------------------ the render: one encode, the right pixels

def test_split_renders_in_one_encode_with_each_person_in_their_own_panel_and_captions_on_the_seam(tmp_path, monkeypatch):
    src = pair_source(tmp_path)
    monkeypatch.setattr(layouts, "look", lambda *a, **k: seen_of(box(0.22, 0.3, 0.1, 0.2), box(0.68, 0.3, 0.1, 0.2)))
    spy = Spy(monkeypatch)
    folder = tmp_path / "o"
    folder.mkdir()
    res = adapter().render_clip(str(src), clip_for(layout="split", crop="follow_speaker", hook_text="Nobody saw this coming"), [], {}, "#ad", str(folder / "o.mp4"))
    assert res["layout"] == "split" and res["encodes"] == 1 and len(spy.encodes) == 1 and res["layout_problems"] == []
    out = folder / "o.mp4"
    top, bottom = frame(out, 2.0)[60:180, 20:250], frame(out, 2.0)[300:420, 20:250]
    assert top[..., 0].mean() > top[..., 2].mean() + 40                 # the red person is on top
    assert bottom[..., 2].mean() > bottom[..., 0].mean() + 40           # the blue person below
    style = next(x for x in (folder / "o.ass").read_text(encoding="utf-8-sig").splitlines() if x.startswith("Style: Caption"))
    assert style.split(",")[18] == "5"                                  # alignment 5: the captions sit on the seam


def test_split_with_a_feed_puts_the_feed_in_the_bottom_panel_and_stays_one_encode(tmp_path, monkeypatch):
    src = pair_source(tmp_path)
    feed = tmp_path / "feed.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=0x20C020:s=360x640:r=25", "-t", "1", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(feed)])   # shorter than the clip: it loops
    monkeypatch.setattr(layouts, "look", lambda *a, **k: seen_of(box(0.22, 0.3, 0.1, 0.2)))
    spy = Spy(monkeypatch)
    folder = tmp_path / "f"
    folder.mkdir()
    res = adapter().render_clip(str(src), clip_for(layout="split", feed={"path": str(feed), "offset_s": 0}), [], {}, "#ad", str(folder / "f.mp4"))
    assert res["layout"] == "split" and len(spy.encodes) == 1
    bottom = frame(folder / "f.mp4", 3.5)[330:420, 20:250]              # after the 1 s feed has looped
    assert bottom[..., 1].mean() > bottom[..., 0].mean() + 40 and bottom[..., 1].mean() > bottom[..., 2].mean() + 40


def test_reaction_renders_in_one_encode_with_a_clean_plan(tmp_path, monkeypatch):
    src = pair_source(tmp_path)
    monkeypatch.setattr(layouts, "look", lambda *a, **k: seen_of(box(0.45, 0.3, 0.07, 0.16)))
    monkeypatch.setattr(layouts.gaming_layout, "find_facecam", lambda *a, **k: {"kind": "none"})
    folder = tmp_path / "r"
    folder.mkdir()
    res = adapter().render_clip(str(src), clip_for(layout="reaction"), [], {}, "#ad", str(folder / "r.mp4"))
    assert res["layout"] == "reaction" and res["encodes"] == 1 and res["layout_problems"] == []


def test_a_layout_the_footage_cannot_carry_falls_back_to_the_single_crop_and_says_why(tmp_path, monkeypatch):
    src = pair_source(tmp_path)
    monkeypatch.setattr(layouts, "look", lambda *a, **k: {"faces": [[]] * 8})
    folder = tmp_path / "x"
    folder.mkdir()
    res = adapter().render_clip(str(src), clip_for(layout="split"), [], {}, "#ad", str(folder / "x.mp4"))
    assert res["layout"] == "single" and "split needs" in res["layout_fallback"] and (folder / "x.mp4").is_file()


def test_the_owners_single_overrides_a_suggestion_and_auto_follows_it(tmp_path, monkeypatch):
    src = pair_source(tmp_path)
    monkeypatch.setattr(layouts, "look", lambda *a, **k: seen_of(box(0.22, 0.3, 0.1, 0.2), box(0.68, 0.3, 0.1, 0.2)))
    for want, used in (("single", "single"), (None, "split")):
        folder = tmp_path / f"w{used}"
        folder.mkdir()
        clip = clip_for(crop="follow_speaker", **({"layout": want} if want else {}))
        res = adapter().render_clip(str(src), clip, [], {}, "#ad", str(folder / "w.mp4"))
        assert res["layout"] == used, (want, res["layout_suggested"])
    assert res["layout_suggested"]["layout"] == "split"


# ------------------------------------------------------------------ the editor: one tap, one new version

def test_layout_pick_is_a_new_version_kept_in_the_clips_meta_and_refuses_unknown_layouts(produced):
    with produced.db.session(produced.owner) as conn:
        target = review.feed(conn, produced.owner.operator_id)[0]
        with pytest.raises(AppError) as err:
            review.edit(conn, produced.owner, target["id"], layout="diagonal")
        assert err.value.code == "bad_layout"
        v2 = review.edit(conn, produced.owner, target["id"], layout="split")
        assert v2["status"] == "rendering" and v2["version_no"] == 2 and v2["render_meta"]["layout"] == "split"


def test_the_main_person_is_one_person_even_when_a_second_one_is_sometimes_bigger():
    left, right = box(0.15, 0.3, 0.1, 0.2), box(0.70, 0.3, 0.12, 0.24)
    seen = {"faces": [[left, right], [left], [left, right], [right], [left], [left, right], [left], [left, right]], "aspect": 1.78}
    got = layouts.main_face(seen)
    assert abs(got[0] - 0.15) < 0.02            # the person who is there most, not a point between the two
    assert layouts.main_face({"faces": [[left]] + [[]] * 7}) is None      # seen in one frame of eight: nobody
