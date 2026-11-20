# Active-speaker bake-off (2026-11-20)

- **Winner:** FabuLight-ASD face-only, as ONNX, behind `faces.track` (`backend/clipping/speaker.py`). Decision 33 in `docs/decisions.md` has the reasons and every rejected candidate.
- **Rubric** (fixed before running): `RUBRIC.md`. **Labels:** `labels.json` (read by eye from 2 s tiles). **Raw results:** `results.json`. **Table:** `scores.md`. **Crop overlays on real frames:** `sheets/` (red = old crop, green = speaker crop, magenta = labelled speaker).
- **Re-run:** `scripts/extract.py` (faces, tracks, audio), `scripts/asd_scores.py METHOD CLIPS` (bake-off venv with CUDA torch, `.data/benchmark/speaker/venv`), `scripts/paths.py`, `scripts/report.py`; `scripts/export_onnx.py` rebuilds the ONNX.
- **Scores (seconds with the speaker inside the crop):** see `scores.md`.
