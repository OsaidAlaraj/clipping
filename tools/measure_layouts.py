"""Measure the clip layouts on real footage: for several windows of each source, plan single/split/reaction, render the real clip
(one encode, real captions on a stand-in sentence) and write before/after frames plus metrics.json.

    python tools/measure_layouts.py OUT_DIR SOURCE.mp4[@start,start] [SOURCE2.mp4 ...]

Metrics per window: faces seen, plan found, safe-area violations (faces vs hook title, captions, label strip, bottom controls),
cut off faces, and for the rendered clip the share of sampled output frames in which the detector still finds the face(s)."""
import json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from clipping import ffmpeg, layouts                      # noqa: E402
from clipping.adapters import media                       # noqa: E402

W, H, DUR = 1080, 1920, 14.0


def faces_in(frame_path: Path):
    import cv2
    from clipping import faces
    img = cv2.imread(str(frame_path))
    h, w = img.shape[:2]
    det = cv2.FaceDetectorYN.create(str(faces.MODEL), "", (w, h), 0.5, 0.3, 5000)
    _, found = det.detect(img)
    return [] if found is None else [tuple(map(float, f[:4])) for f in found]


def main(out_dir: Path, sources: list[Path], windows: dict):
    out_dir.mkdir(parents=True, exist_ok=True)
    render = media.FFmpegRender({"width": W, "height": H, "preset": "veryfast", "crf": 28})
    rows = []
    for src in sources:
        info = ffmpeg.probe(src)
        total = info["duration_s"]
        for k, frac in enumerate(windows.get(str(src), (0.15, 0.4, 0.65))):
            start = frac if str(src) in windows else max(min(total * frac, total - DUR - 1), 0)
            clip = {"start_s": start, "end_s": start + DUR, "language": "en", "platform": "tiktok", "hook_text": "POV: you did not see that coming", "crop": "follow_speaker"}
            seen = layouts.look(src, start, start + DUR)
            row = {"source": src.parent.name if src.stem == "ep" else src.stem, "start_s": round(start, 1), "faces_seen": [len(f) for f in seen.get("faces", [])]}
            row["suggested"] = layouts.suggest(None, info["width"], info["height"], seen, None, False)["layout"]
            for kind in ("single", "split", "reaction"):
                t0 = time.time()
                clip["layout"] = kind
                tag = f"{src.parent.name if src.stem == 'ep' else src.stem}_{k}_{kind}"
                try:
                    res = render.render_clip(str(src), clip, [], {"karaoke": False, "tighten": False, "zooms": False}, "AD", str(out_dir / f"{tag}.mp4"))
                except Exception as exc:
                    row[kind] = {"error": str(exc)[:200]}
                    continue
                used = res["layout"]
                info_out = {"used": used, "fallback": res["layout_fallback"], "problems": res["layout_problems"], "render_s": round(time.time() - t0, 1)}
                hits = []
                for i, at in enumerate((1.0, 4.0, 8.0, 12.0)):
                    png = out_dir / f"{tag}_{i}.png"
                    ffmpeg.run(["-ss", str(at), "-i", str(out_dir / f"{tag}.mp4"), "-frames:v", "1", "-vf", "scale=360:-2", str(png)], cwd=out_dir)
                    hits.append(len(faces_in(png)))
                info_out["faces_in_output_frames"] = hits
                row[kind] = info_out
            rows.append(row)
            print(json.dumps(row), flush=True)
    (out_dir / "metrics.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    wins, paths = {}, []
    for arg in sys.argv[2:]:                      # SOURCE.mp4 or SOURCE.mp4@115,346 (start seconds)
        path, _, at = arg.partition("@")
        paths.append(Path(path))
        if at:
            wins[str(Path(path))] = tuple(float(x) for x in at.split(","))
    main(Path(sys.argv[1]), paths, wins)
