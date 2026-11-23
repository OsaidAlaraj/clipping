"""A failed YouTube download reaches the owner as a plain sentence, never as yt-dlp's raw text."""
import pytest
import yt_dlp

from clipping import sources
from clipping.core import AppError


@pytest.mark.parametrize("raw, expected", [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "private"),
    ("ERROR: [youtube] abc: Video unavailable", "unavailable"),
    ("HTTP Error 429: Too Many Requests", "limiting"),
    ("ERROR: Requested format is not available", "format"),
    ("ERROR: something nobody planned for", "server log"),
])
def test_youtube_failures_are_plain_sentences(raw, expected, monkeypatch, tmp_path):
    class Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, *a, **k): raise yt_dlp.utils.DownloadError(raw)

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Boom)
    with pytest.raises(AppError) as err:
        sources.download_youtube("https://www.youtube.com/watch?v=abc", tmp_path)
    assert expected in str(err.value).lower()
    assert "ERROR:" not in str(err.value) and "yt-dlp" not in str(err.value)
