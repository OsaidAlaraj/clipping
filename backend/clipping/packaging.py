"""Per-platform packaging: one clip becomes a title, caption, hashtags and cover text for each platform it can go to.

The result is stored with the clip (clips.render_meta["packaging"][platform]) and the clip's own platform fills clips.caption_text,
so the post flow, the posting worker and the swipe "Ready to post manually" copy all read the same text. Order inside a caption is
fixed: disclosure first, then the body, the original commentary line when it fits, required mentions, hashtags. The disclosure,
required mentions and required hashtags are never cut to make room.

Norms are practitioner guidance, not platform guarantees (see docs/decisions.md, caption lab): TikTok and Instagram show only the first
line or two of a caption before "more", and both do best with a few specific hashtags rather than a pile."""
from __future__ import annotations

from psycopg.types.json import Jsonb

from . import caption_lab, originality, transcripts
from .caption_text import clip_to, transcript_text
from .core import AppError, GateError, audit

# soft: characters that stay visible before the "more" fold (body is cut to this); hard: the platform's limit; tags: (min, max) hashtags.
NORMS = {
    "tiktok": {"soft": 150, "hard": 2200, "tags": (3, 5), "title": 70, "cover_words": 5},
    "instagram_reels": {"soft": 125, "hard": 2200, "tags": (3, 5), "title": 70, "cover_words": 5},
    "youtube_shorts": {"soft": 100, "hard": 5000, "tags": (2, 3), "title": 70, "cover_words": 5},
}
DEFAULT_NORM = NORMS["tiktok"]


def platforms_for(allowed: list[str] | None, clip_platform: str | None) -> list[str]:
    """The platforms to package for: the campaign's allowed ones we know, plus the clip's own."""
    out = [p for p in (allowed or []) if p in NORMS]
    if clip_platform and clip_platform not in out and clip_platform in NORMS:
        out.append(clip_platform)
    return out


def _fit_tags(required: list[str], candidates: list[str], norm: dict, language: str, transcript: str, niche_words: set[str]) -> list[str]:
    """Required campaign hashtags first (always kept, even above the maximum), then the clip's own tags up to the platform's range."""
    from .caption_match import hashtag_supported

    tags = list(dict.fromkeys(required))
    lo, hi = norm["tags"]
    seen = {x.lower() for x in tags}
    pool = []
    for t in candidates:   # one tag per spelling, letter case aside: "#Everyone" and "#everyone" are the same hashtag
        if t.lower() not in seen:
            seen.add(t.lower())
            if hashtag_supported(t, transcript, niche_words):
                pool.append(t)
    for tag in pool:
        if len(tags) >= hi:
            break
        tags.append(tag)
    return tags


def package(variant: dict, *, platform: str, language: str, disclosure: str, required_tags: list[str], mentions: list[str],
            commentary: str | None, transcript: str, niche_words: set[str] | None = None, extra_tags: list[str] | None = None) -> dict:
    """The post text for one platform from one variant. Pure."""
    norm = NORMS.get(platform, DEFAULT_NORM)
    tags = _fit_tags(required_tags, list(variant.get("hashtags") or []) + (extra_tags or []), norm, language, transcript, niche_words or set())
    fixed_tail = " ".join(x for x in [" ".join(mentions), " ".join(tags)] if x)
    # The visible part (disclosure + body) stays inside the soft length; the commentary line goes after it only if the hard limit allows.
    room = max(norm["soft"] - len(disclosure) - 1, 20)
    body = clip_to(variant["caption"], room)
    parts = [disclosure, body]
    commentary_in = False
    if commentary:
        candidate = " ".join(p for p in [*parts, commentary, fixed_tail] if p)
        if len(candidate) <= norm["hard"]:
            parts.append(commentary)
            commentary_in = True
    caption = " ".join(p for p in [*parts, fixed_tail] if p).strip()
    if len(caption) > norm["hard"]:
        caption = " ".join(p for p in [disclosure, clip_to(body, max(norm["hard"] - len(disclosure) - len(fixed_tail) - 2, 20)), fixed_tail] if p)
    return {"platform": platform, "kind": variant["kind"], "colour": variant.get("colour"), "title": clip_to(variant.get("title") or body, norm["title"]),
            "caption": caption, "hashtags": tags, "mentions": list(mentions),
            "cover_text": " ".join(str(variant.get("cover_text") or body).split()[: norm["cover_words"]]),
            "commentary_in_caption": commentary_in, "length": len(caption), "soft_limit": norm["soft"], "hard_limit": norm["hard"],
            "visible_before_fold": len(disclosure) + 1 + len(body) <= norm["soft"] + 1}


def _required_tokens(rules: dict) -> tuple[list[str], list[str]]:
    import re

    tags, mentions = [], []
    for rule in (rules or {}).get("content_rules") or []:
        if rule.get("requirement") != "required":
            continue
        for token in re.findall(r"[#@][\w؀-ۿ]+", rule.get("text") or ""):
            target = tags if token.startswith("#") else mentions
            if token not in target:
                target.append(token)
    return tags, mentions


def build_all(variants: dict, *, platforms: list[str], language: str, disclosure: str, rules: dict, commentary: str | None, transcript: str,
              niche_words: set[str], choose: str = "matched") -> dict:
    """{platform: package} for the usable variant `choose` (the matched one when the choice is not usable)."""
    usable = {v["kind"]: v for v in variants["variants"] if v.get("usable")}
    variant = usable.get(choose) or usable["matched"]
    required, mentions = _required_tokens(rules)
    required = [t for t in required if t.lower() != disclosure.lower()]
    extra = caption_lab.hashtag_candidates(transcript, language)
    return {p: package(variant, platform=p, language=language, disclosure=disclosure, required_tags=required, mentions=mentions,
                       commentary=commentary, transcript=transcript, niche_words=niche_words, extra_tags=extra) for p in platforms}


def text_for(render_meta: dict | None, platform: str | None) -> dict | None:
    """The stored package for a platform, or None."""
    return ((render_meta or {}).get("packaging") or {}).get(platform or "")


def caption_for(clip: dict, platform: str | None) -> str:
    """The text to post on `platform`: its own package, else the clip's caption. Used by the posting worker and the manual flow."""
    if (clip.get("render_meta") or {}).get("caption_edited"):
        return clip.get("caption_text") or ""   # the owner's own words win over the generated package
    package_ = text_for(clip.get("render_meta"), platform)
    return (package_ or {}).get("caption") or clip.get("caption_text") or ""


def hashtags_for(clip: dict, platform: str | None) -> list[str]:
    package_ = None if (clip.get("render_meta") or {}).get("caption_edited") else text_for(clip.get("render_meta"), platform)
    return list(package_["hashtags"] if package_ else (clip.get("hashtags") or []))


# ------------------------------------------------------------------ database side

def package_clip(conn, registry, clip: dict, source: dict, campaign: dict, version: dict, disclosure: dict) -> object:
    """Run the caption lab and the originality layer for one clip and store the result in its render_meta. The clip's own platform
    package becomes clips.caption_text (unless the owner edited the caption by hand). Returns the model cost.
    Any failure in here must leave the clip as it was: the caller wraps this."""
    owner = clip["owner_operator_id"]
    meta = dict(clip["render_meta"] or {})
    words = transcripts.load(conn, source["id"])["words"]
    transcript = transcript_text(words, clip["start_s"], clip["end_s"])
    if not transcript:
        raise AppError("The clip has no transcript text to match a caption to.", "no_transcript")
    language = clip["language"]
    template = None
    if clip["account_id"] is not None:
        row = conn.execute("select commentary_template from accounts where id = %s", (clip["account_id"],)).fetchone()
        template = (row or {}).get("commentary_template")
    rules = version["rules_json"] or {}
    variants, cost = caption_lab.generate(conn, registry, owner, transcript=transcript, language=language, dialect=clip["dialect"],
                                          platform=clip["platform"], hook_text=meta.get("hook_text"))
    record, cost2 = originality.build(conn, registry, owner, transcript=transcript, language=language, dialect=clip["dialect"],
                                      rules=rules, template=template)
    commentary = " ".join(x for x in (record["commentary_text"], record["template_text"]) if x) or None   # the clip's take, then the account's line
    niche = conn.execute("select name from categories where id = %s", (campaign["category_id"],)).fetchone()
    niche_words = set((niche or {}).get("name", "").lower().split())
    packages = build_all(variants, platforms=platforms_for(rules.get("platforms_allowed"), clip["platform"]) or [clip["platform"] or "tiktok"],
                         language=language, disclosure=disclosure["caption_text"], rules=rules, commentary=commentary,
                         transcript=transcript, niche_words=niche_words, choose=meta.get("caption_kind") or "matched")
    meta["caption_lab"] = {"variants": variants["variants"], "default": variants["default"],
                           "chosen": meta.get("caption_kind") or "matched", "transcript_chars": len(transcript)}
    meta["packaging"] = packages
    meta["originality"] = record
    sets, args = "render_meta = %s", [Jsonb(meta)]
    own = packages.get(clip["platform"]) if clip["platform"] else next(iter(packages.values()), None)
    if own and not meta.get("caption_edited"):
        sets += ", caption_text = %s, hashtags = %s, mentions = %s"
        args += [own["caption"], own["hashtags"], own["mentions"]]
    conn.execute(f"update clips set {sets} where id = %s", (*args, clip["id"]))
    audit(conn, "clip.caption_lab", "clips", clip["id"], {"kinds": [v["kind"] for v in variants["variants"] if v.get("usable")],
                                                          "platforms": list(packages), "originality": record["status"]},
          rule_version_id=version["id"])
    return cost + cost2


def choose_variant(conn, actor, clip_id, kind: str) -> dict:
    """The owner picks a usable variant (matched or curiosity) for the clip; every platform package is rebuilt from it.
    A variant that failed the match check cannot be chosen."""
    if actor.kind != "operator":
        raise GateError("Only the owner chooses the caption variant.")
    clip = conn.execute("select * from clips where id = %s", (clip_id,)).fetchone()
    if clip is None:
        raise AppError("Clip not found.", "not_found")
    if clip["status"] not in ("ready_for_review", "accepted", "review_later"):
        raise AppError("The caption can be changed before the clip is approved to post.", "bad_status")
    meta = dict(clip["render_meta"] or {})
    lab = meta.get("caption_lab")
    if not lab:
        raise AppError("This clip has no caption variants.", "no_variants")
    chosen = next((v for v in lab["variants"] if v["kind"] == kind), None)
    if chosen is None or not chosen.get("usable"):
        raise AppError("That caption variant did not pass the match check against the video.", "variant_rejected")
    version = conn.execute("select * from campaign_versions where id = %s", (clip["campaign_version_id"],)).fetchone()
    from . import pipeline   # lazy: the pipeline imports this module

    campaign = conn.execute("select * from campaigns where id = %s", (clip["campaign_id"],)).fetchone()
    tpl = pipeline.disclosure_for(conn, campaign, version, clip["language"])
    if tpl is None:
        raise AppError("Disclosure text is missing for this language.", "disclosure_missing")
    cand = conn.execute("select source_id from candidates where id = %s", (clip["candidate_id"],)).fetchone()
    words = transcripts.load(conn, cand["source_id"])["words"]
    transcript = transcript_text(words, clip["start_s"], clip["end_s"])
    niche = conn.execute("select name from categories where id = %s", (campaign["category_id"],)).fetchone()
    packages = build_all(lab, platforms=list((meta.get("packaging") or {}).keys()) or [clip["platform"] or "tiktok"], language=clip["language"],
                         disclosure=tpl["caption_text"], rules=version["rules_json"] or {}, commentary=" ".join(x for x in ((meta.get("originality") or {}).get("commentary_text"), (meta.get("originality") or {}).get("template_text")) if x) or None,
                         transcript=transcript, niche_words=set((niche or {}).get("name", "").lower().split()), choose=kind)
    meta["packaging"], lab["chosen"], meta["caption_kind"] = packages, kind, kind
    meta["caption_lab"] = lab
    own = packages.get(clip["platform"]) or next(iter(packages.values()))
    sets, args = "render_meta = %s", [Jsonb(meta)]
    if not meta.get("caption_edited"):
        sets += ", caption_text = %s, hashtags = %s, mentions = %s"
        args += [own["caption"], own["hashtags"], own["mentions"]]
    conn.execute(f"update clips set {sets} where id = %s", (*args, clip_id))
    audit(conn, "clip.caption_variant", "clips", clip_id, {"kind": kind}, rule_version_id=clip["campaign_version_id"])
    return {"chosen": kind, "packaging": packages}
