"""The blur_pad layout: the whole 16:9 picture centred on a blurred, darkened 9:16 copy of itself, chosen by the template."""
import pytest

from clipping import ffmpeg
from clipping.adapters import media
from clipping.core import AppError
from test_13_render import H, W, adapter, clip_for, flat_source, frame, render


def test_blur_pad_is_one_filtergraph_that_keeps_the_whole_picture():
    graph = media.layout_graph("blur_pad", 1920, 1080, 1080, 1920, None)
    assert graph.count("[0:v]") == 1 and "boxblur" in graph and "brightness=-0.18" in graph and graph.endswith("[lay]")
    assert "crop=1080:1920" in graph and "force_original_aspect_ratio=decrease" in graph


def test_the_template_selects_it_and_refuses_unknown_layouts():
    assert media.normalize_template({"layout": "blur_pad"})["layout"] == "blur_pad"
    assert media.normalize_template({"layout": "nonsense"})["layout"] == "auto"
    with pytest.raises(AppError):
        media.normalize_template({"layout": "nonsense"}, strict=True)


def test_a_blur_pad_render_is_one_encode_with_dark_bars_above_and_below_the_picture(tmp_path):
    src = flat_source(tmp_path)                                   # uniform grey 0x80, so the pad is the same grey made darker
    result = render(tmp_path, src, clip_for(0, 3, crop="follow_speaker"), {"layout": "blur_pad", "progress_bar": False, "title_card": False}, words=[], label="#ad")
    assert result["encodes"] == 1
    picture = frame(media.Path(result["path"]), 1.0)
    top, middle = picture[int(H * 0.03):int(H * 0.08), :, :].mean(), picture[int(H * 0.45):int(H * 0.55), :, :].mean()
    assert top < middle - 10                                      # the blurred copy is darker than the centred picture
