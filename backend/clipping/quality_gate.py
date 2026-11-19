"""Quality gate v1: measurable checks on a rendered clip, stored in its render metadata as pass / warn / fail with plain reasons (EN and AR).
No new dependencies: YuNet for faces (clipping.faces), ffmpeg for loudness, the transcript for words."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import faces, ffmpeg

MIN_WORDS_PER_S = 1.2            # talking content: real speech is about 2 to 3 words a second
FACE_WARN, FACE_FAIL = 0.80, 0.50
LUFS_LOW, LUFS_HIGH = -20.0, -12.0
SENTENCE_END = ".?!؟…"
TALKING_NICHES = ("podcast", "interview", "business", "talk", "arabic")

MESSAGES = {   # code: (English, Arabic)
    "thin_speech": ("Very little speech for a talking clip ({v:.1f} words per second).", "كلام قليل جداً لمقطع حواري ({v:.1f} كلمة بالثانية)."),
    "face_missing": ("The speaker's face is in frame only {v:.0%} of the time.", "وجه المتحدث ظاهر {v:.0%} فقط من الوقت."),
    "face_low": ("The speaker's face is in frame {v:.0%} of the time.", "وجه المتحدث ظاهر {v:.0%} من الوقت."),
    "mid_sentence_end": ("The clip ends in the middle of a sentence.", "المقطع ينتهي في منتصف جملة."),
    "mid_word_start": ("The clip starts in the middle of a word.", "المقطع يبدأ في منتصف كلمة."),
    "loudness": ("Loudness {v:.1f} LUFS is outside the usual -20 to -12 band.", "مستوى الصوت {v:.1f} LUFS خارج النطاق المعتاد من -20 إلى -12."),
    "language_mismatch": ("The audio sounds like '{v}', not the language set on the source.", "الصوت يبدو بلغة '{v}' لا اللغة المحددة للمصدر."),
    "no_disclosure": ("The caption does not start with the disclosure.", "الوصف لا يبدأ بإفصاح الإعلان."),
}


def words_per_second(words: list[dict], start: float, end: float) -> float:
    inside = [w for w in words if start <= float(w["start"]) < end]
    return len(inside) / max(end - start, 0.1)


def sentence_end_ok(words: list[dict], start: float, end: float) -> bool:
    inside = [w for w in words if start <= float(w["start"]) and float(w["end"]) <= end + 0.5]
    return bool(inside) and inside[-1]["text"].rstrip()[-1:] in SENTENCE_END


def word_start_ok(words: list[dict], start: float) -> bool:
    return not any(float(w["start"]) + 0.05 < start < float(w["end"]) - 0.05 for w in words)


def disclosure_first(caption: str | None, disclosure: str | None) -> bool:
    return bool(caption and disclosure and caption.strip().lower().startswith(disclosure.strip().lower()))


def loudness_lufs(video: Path) -> float | None:
    out = subprocess.run([ffmpeg.exe(), "-nostdin", "-i", str(video), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True, errors="replace").stderr
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    return float(found[-1]) if found else None


def face_share(video: Path, fps: float = 2.0) -> float | None:
    """Share of sampled frames (about 2 a second) of the rendered clip where a YuNet face box lies fully inside the picture."""
    try:
        import cv2
        det = faces._load()
        cap = cv2.VideoCapture(str(video))
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30
        every, i, seen, inside = max(1, round(src_fps / fps)), 0, 0, 0
        while cap.grab():
            if i % every == 0:
                ok, frame = cap.retrieve()
                if ok:
                    h, w = frame.shape[:2]
                    sw = max(1, round(w * faces.FRAME_H / h))        # keep the aspect: squashing a portrait frame hides faces from the detector
                    padded = cv2.copyMakeBorder(cv2.resize(frame, (sw, faces.FRAME_H)), 0, 0, 0, max(faces.FRAME_W - sw, 0), cv2.BORDER_CONSTANT)
                    _, found = det.detect(padded[:, :faces.FRAME_W])
                    seen += 1
                    if found is not None and any(f[0] >= -2 and f[1] >= -2 and f[0] + f[2] <= sw + 2 and f[1] + f[3] <= faces.FRAME_H + 2 for f in found):
                        inside += 1
            i += 1
        cap.release()
        return inside / seen if seen else None
    except Exception:
        return None


def evaluate(m: dict) -> dict:
    """`m` holds the measurements (None = not measured): words_per_s, face_share, sentence_end, word_start, lufs, detected_language,
    source_language, disclosure_first, expects_face, talking. Returns {status, reasons, checks}."""
    fails, warns = [], []
    if m.get("talking") and m.get("words_per_s") is not None and m["words_per_s"] < MIN_WORDS_PER_S:
        fails.append(("thin_speech", m["words_per_s"]))
    fs = m.get("face_share")
    if m.get("expects_face") and fs is not None:
        if fs < FACE_FAIL:
            fails.append(("face_missing", fs))
        elif fs < FACE_WARN:
            warns.append(("face_low", fs))
    if m.get("sentence_end") is False:
        warns.append(("mid_sentence_end", None))
    if m.get("word_start") is False:
        warns.append(("mid_word_start", None))
    if m.get("lufs") is not None and not (LUFS_LOW <= m["lufs"] <= LUFS_HIGH):
        warns.append(("loudness", m["lufs"]))
    det, src = m.get("detected_language"), m.get("source_language")
    if det and src and det != src:
        fails.append(("language_mismatch", det))
    if m.get("disclosure_first") is False:
        fails.append(("no_disclosure", None))
    reasons = [{"code": c, "level": lv, "en": MESSAGES[c][0].format(v=v), "ar": MESSAGES[c][1].format(v=v)}
               for lv, items in (("fail", fails), ("warn", warns)) for c, v in items]
    return {"status": "fail" if fails else "warn" if warns else "pass", "reasons": reasons,
            "checks": {k: m.get(k) for k in ("words_per_s", "face_share", "sentence_end", "word_start", "lufs", "detected_language", "disclosure_first")}}


def run(video: Path, words: list[dict], start: float, end: float, *, language: str, caption: str | None, disclosure: str | None,
        niche: str | None = None, layout: str | None = None, detected_language: str | None = None) -> dict:
    """Measure a rendered clip and evaluate it. Faces are expected in talking niches and in follow-the-speaker crops."""
    talking = (niche or "podcast") in TALKING_NICHES
    m = {"words_per_s": round(words_per_second(words, start, end), 2), "sentence_end": sentence_end_ok(words, start, end),
         "word_start": word_start_ok(words, start), "lufs": loudness_lufs(video), "detected_language": detected_language, "source_language": language,
         "disclosure_first": disclosure_first(caption, disclosure) if disclosure is not None else None, "talking": talking,
         "expects_face": talking and layout != "centre"}
    m["face_share"] = face_share(video) if m["expects_face"] else None
    for k in ("lufs", "face_share"):
        m[k] = None if m[k] is None else round(m[k], 2 if k == "face_share" else 1)
    return evaluate(m)
