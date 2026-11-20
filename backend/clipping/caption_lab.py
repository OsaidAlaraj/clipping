"""Caption lab: per clip, two honest text variants and a verdict on each.

- matched (green, the default): says only what the clip says.
- curiosity (amber): an open question the clip really answers. Never a promise the clip does not keep.

Every variant goes through the match checker (caption_match). A variant that fails is kept with its reasons but is never used.
If the model's matched variant fails, an extractive caption built from the transcript itself takes its place, so a clip always has a
caption that matches its video. The model comes through the adapter registry (task `caption_lab`), so tests use the fake."""
from __future__ import annotations

import re

from . import caption_match
from .caption_text import clip_to, content_stems, sentences, stem, WORD

KINDS = ("matched", "curiosity")
COLOUR = {"matched": "green", "curiosity": "amber"}
CAPTION_MAX = 110
ARABIC_STYLE = "plain Arabic understood in Jordan and the Gulf; light Jordanian or Gulf colloquial words allowed, nothing specific to one far dialect"


def arabic_dialect_hint(dialect: str | None) -> str:
    """jordanian, gulf or neutral, from the clip's dialect label (free text such as ar-JO, ar-SA, Gulf)."""
    d = (dialect or "").lower()
    if re.search(r"\bjo\b|jordan|levant|\bshami|ar-jo", d):
        return "jordanian"
    if re.search(r"\b(sa|ae|kw|qa|bh|om)\b|ar-(sa|ae|kw|qa|bh|om)|gulf|khaleej|saudi|emirat|kuwait|qatar|najdi|hijazi", d):
        return "gulf"
    return "neutral"


def extractive_caption(transcript: str, language: str) -> dict:
    """The clip's own best sentence, trimmed. Supported by construction."""
    sents = [s for s in sentences(transcript) if len(s.split()) >= 3] or sentences(transcript) or [transcript]
    best = max(sents[:4], key=lambda s: len(set(content_stems(s, language)))).strip().rstrip(".!؟?")
    best = clip_to(best, CAPTION_MAX)
    return {"kind": "matched", "caption": best, "title": clip_to(best, 60), "cover_text": " ".join(best.split()[:5]), "hashtags": [], "source": "transcript"}


def _clean(item) -> dict | None:
    if not isinstance(item, dict) or item.get("kind") not in KINDS:
        return None
    caption = " ".join(str(item.get("caption") or "").split())
    if not caption or len(caption) > CAPTION_MAX + 40:
        return None
    tags = [t for t in (item.get("hashtags") or []) if isinstance(t, str) and re.fullmatch(r"#?[\w؀-ۿ]{2,40}", t.strip())]
    return {"kind": item["kind"], "caption": caption, "title": clip_to(str(item.get("title") or caption), 60),
            "cover_text": " ".join(str(item.get("cover_text") or caption).split()[:5]),
            "hashtags": ["#" + t.strip().lstrip("#") for t in tags][:3], "source": "model"}


def verify(conn, registry, owner_id, caption: str, transcript: str, language: str, kind: str) -> tuple[dict, object]:
    """Deterministic checks plus the model's claim check. Returns (verdict, cost)."""
    raw, cost = registry.call(conn, "llm", "complete_json", {"task": "caption_check", "payload": {
        "language": language, "caption": caption, "transcript": transcript}}, owner_id)
    claims = raw.get("claims") if isinstance(raw, dict) else None
    verdict = caption_match.check(caption, transcript, language, kind, claims if isinstance(claims, list) else [])
    return verdict, cost


def generate(conn, registry, owner_id, *, transcript: str, language: str, dialect: str | None, platform: str | None,
             hook_text: str | None, brief_note: str = "") -> tuple[dict, object]:
    """Ask the model for the two variants, check each, and always return a usable matched one.
    Result: {"variants": [{kind, colour, caption, title, cover_text, hashtags, check, usable}], "default": "matched"}."""
    payload = {"language": language, "dialect": arabic_dialect_hint(dialect) if language == "ar" else None, "platform": platform,
               "transcript": transcript, "hook_text": hook_text or "", "brief_note": brief_note}
    if language == "ar":
        payload["style"] = ARABIC_STYLE
    raw, total = registry.call(conn, "llm", "complete_json", {"task": "caption_lab", "payload": payload}, owner_id)
    by_kind: dict[str, dict] = {}
    for item in (raw or {}).get("variants") or [] if isinstance(raw, dict) else []:
        cleaned = _clean(item)
        if cleaned and cleaned["kind"] not in by_kind:
            by_kind[cleaned["kind"]] = cleaned
    out = []
    for kind in KINDS:
        variant = by_kind.get(kind)
        if variant is not None:
            verdict, cost = verify(conn, registry, owner_id, variant["caption"], transcript, language, kind)
            total += cost
            variant.update(check=verdict, usable=verdict["ok"])
            if kind == "curiosity" and not _is_open_question(variant["caption"]):
                variant["check"]["ok"] = variant["usable"] = False
                variant["check"]["reasons"].append({"code": "not_a_question", "detail": "A curiosity variant must be an open question the clip answers."})
            out.append(variant)
    matched = next((v for v in out if v["kind"] == "matched"), None)
    if matched is None or not matched["usable"]:
        fallback = extractive_caption(transcript, language)
        verdict, cost = verify(conn, registry, owner_id, fallback["caption"], transcript, language, "matched")
        total += cost
        fallback.update(check=verdict, usable=True, replaces_rejected=bool(matched), rejected=matched)   # extractive text is the clip's own words
        out = [fallback] + [v for v in out if v["kind"] != "matched"]
    for v in out:
        v["colour"] = COLOUR[v["kind"]]
    return {"variants": out, "default": "matched"}, total


def _is_open_question(text: str) -> bool:
    return "?" in text or "؟" in text


def hashtag_candidates(transcript: str, language: str, limit: int = 8) -> list[str]:
    """Hashtags taken from what the clip talks about: its most repeated meaningful words (as said), shortest stem first on ties."""
    stems = content_stems(transcript, language)
    counts: dict[str, int] = {}
    for s in stems:
        counts[s] = counts.get(s, 0) + 1
    ranked = sorted(counts, key=lambda s: (-counts[s], -len(s)))
    spoken = WORD.findall(transcript)
    tags = []
    for s in ranked:
        if len(s) < 4:
            continue
        word = next((w for w in spoken if stem(w) == s), s)
        tags.append("#" + word.lower() if language != "ar" else "#" + word)
        if len(tags) >= limit:
            break
    return tags
