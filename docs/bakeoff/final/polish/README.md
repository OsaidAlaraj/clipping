# Clip polish proof (2026-11-20)

Real segments, one encode, same hook text and `#ad` / `#إعلان` label, TikTok social style, speaker-follow crop (YuNet):
- `en_before.mp4` / `en_after.mp4`: 38 s English podcast (1080p source, Cordkillers ep. 377 at 10:10).
- `ar_before.mp4` / `ar_after.mp4`: 37 s Arabic lecture with English terms (720p slides, KAU physiology at 15:05). The picture is a projected slide, so the crop shows no face; judge captions, zooms and cuts here, not the reframe.
- Before = the code at the start of branch clip-polish (bottom captions 4.2 %, loud-second triangle zoom, no tightening, bicubic). After = tightening, word-by-word captions at 70 % height, eased punch-ins, lanczos. Proofs are re-encoded to 540x960 to keep the repository small; the sharpness numbers come from the full-size measurements, not from these files.
- `*.ass` are the subtitle scripts of each clip. `measure_*.py` reproduce the numbers in `*.jsonl`; the decision with all numbers is Decision 34 in `docs/decisions.md`.
- Source media and Whisper word files stay under `.data/` (internal test footage, see `.data/samples/SOURCES.md`).
