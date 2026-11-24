"""Campaign spec extractor: brief text (Markdown, links kept) in, a typed CampaignSpec out.

Design (docs/spec-extraction-research.md): a flat list of items `{layer, key, value, source_quote, confidence, status}`.
1. A deterministic rule pass finds what has a fixed shape (money, minimums, percentages, platforms, hashtags, deadlines,
   verbs like ADD / NEVER / NOT ALLOWED / ONLY).
2. The LLM (adapter registry, task "brief_spec") reads the rest. Its output is not trusted: an item whose quote is not in the
   brief, or whose number is not in its quote, is dropped.
3. The two passes are merged key by key. Agreement raises confidence; a disagreement becomes `conflict`.
Anything the text does not say is `unclear` and has no value. The brief is DATA: instructions inside it addressed to an AI are
ignored and reported as a warning."""
from __future__ import annotations

import bisect
import datetime as dt
import re
import uuid
from typing import Any

from .core import AppError

VERSION = 1
PLATFORMS = ("tiktok", "instagram", "youtube_shorts")

# key -> (kind, description). Kinds: str, num, bool, list, dict, enum:a|b.  `rep` marks keys that may repeat (one item per value).
CATALOG: dict[str, tuple[str, str]] = {
    "identity.marketplace": ("str", "marketplace slug if named (clipster, content_rewards, reach)"),
    "identity.name": ("str", "campaign name"),
    "identity.campaign_url": ("str", "campaign page link"),
    "payout.model": ("enum:cpm|flat|per_view|retainer", "payout model"),
    "payout.rate_per_1k": ("num", "USD per 1000 views"),
    "payout.min_payout": ("num", "minimum payout, USD"),
    "payout.min_qualifying_views": ("num", "views needed before a payout starts"),
    "payout.max_per_clip": ("num", "USD cap per clip"),
    "payout.max_per_creator": ("num", "USD cap per creator"),
    "payout.max_per_platform": ("num", "USD cap per platform"),
    "payout.budget_total": ("num", "total campaign budget, USD"),
    "payout.budget_used_pct": ("num", "percent of the budget already used"),
    "payout.payment_methods": ("list", "payment methods named"),
    "platforms.allowed": ("list", "allowed platforms: tiktok, instagram, youtube_shorts"),
    "audience.language": ("str", "content language code (en, ar)"),
    "audience.tier": ("str", "audience tier such as T1"),
    "audience.min_share": ("dict", "{pct, countries[], label}: minimum audience share from named countries"),
    "audience.forbidden_regions": ("list", "regions or tiers whose audience is not wanted"),
    "content.allowed_categories": ("list", "content categories the campaign wants"),
    "content.forbidden": ("str", "one forbidden thing (repeat the key per item)"),
    "content.safety_flag": ("str", "gambling, crypto, young_audience, ai_generated_forbidden"),
    "length.min_s": ("num", "minimum clip length in seconds"),
    "length.max_s": ("num", "maximum clip length in seconds"),
    "captions.required_hashtags": ("list", "hashtags the caption must carry"),
    "captions.required_mentions": ("list", "mentions the caption must carry"),
    "captions.disclosure_policy": ("enum:required|forbidden|unspecified", "ad disclosure (#ad) policy"),
    "captions.copy_rules": ("str", "caption wording rule (repeat the key per rule)"),
    "assets.logo": ("dict", "{requirement: required|forbidden, link}"),
    "assets.logo_guidelines": ("dict", "{link}: the logo guidelines page"),
    "assets.logo_placement": ("str", "where the logo goes (top_half, bottom_half, a corner)"),
    "assets.logo_type": ("enum:static|animated|green_screen", "logo file type"),
    "sources.whitelist": ("dict", "{title, url}: one allowed source (repeat the key per source)"),
    "sources.forbidden_class": ("str", "a class of footage that is not allowed (broadcast)"),
    "hook.constraints": ("str", "one rule for the opening hook (repeat the key per rule)"),
    "edit_style.guidance": ("str", "one editing guidance item (repeat the key per item)"),
    "account.requirements": ("dict", "{type: branded_page|niche_page|bio_link, detail}"),
    "deadlines.deadline": ("dict", "{type, content_type, at (ISO with offset), timezone, date_text}"),
    "submission.within_minutes": ("num", "submit the clip within N minutes of posting"),
    "submission.duplicates_rejected": ("bool", "duplicate URLs are rejected"),
    "performance.min_engagement_pct": ("num", "minimum engagement rate, percent"),
    "performance.min_views": ("num", "minimum views"),
    "enforcement.strikes": ("dict", "{strikes, consequence}"),
    "rejection.reasons": ("str", "one reason a clip is rejected (repeat the key per reason)"),
    "priority.content": ("str", "content to cut first (repeat the key per item)"),
}
REPEATING = {"content.forbidden", "content.safety_flag", "captions.copy_rules", "sources.whitelist", "sources.forbidden_class",
             "hook.constraints", "edit_style.guidance", "account.requirements", "deadlines.deadline", "rejection.reasons",
             "priority.content", "captions.required_hashtags", "captions.required_mentions", "content.allowed_categories"}
# Keys reported `unclear` when the text does not state them. BLOCKING ones must be answered (or marked not applicable) before approval.
CORE = ("payout.model", "payout.rate_per_1k", "payout.budget_total", "platforms.allowed", "audience.language", "length.min_s",
        "captions.disclosure_policy", "assets.logo")
BLOCKING = ("payout.model", "platforms.allowed")
LAYERS = ("identity", "payout", "platforms", "audience", "content", "length", "captions", "assets", "sources", "hook",
          "edit_style", "account", "deadlines", "submission", "performance", "enforcement", "rejection", "priority")

_MD_LINK = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\((https?://[^)\s]+)\)")   # one level of [brackets] inside the text
_URL = re.compile(r"https?://[^\s)\]>]+")
_INJECTION = re.compile(r"ignore (?:all |any )?(?:the )?(?:previous|prior|above) (?:instructions|rules)|disregard (?:the )?(?:above|previous)|"
                        r"\bas an ai\b|system prompt|you are (?:now )?(?:chatgpt|an? (?:ai|assistant|language model))", re.I)


# ------------------------------------------------------------------ text helpers

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def plain_text(markdown: str) -> str:
    """The brief as words: links keep their text, bold and bullet markers go, so quotes can be checked word for word."""
    out = []
    for line in (markdown or "").replace("\r", "").split("\n"):
        line = _MD_LINK.sub(lambda m: m.group(1), line)
        line = re.sub(r"^\s*(?:[*\-•]\s+|\d+\.\s+)", "", line.replace("**", "").replace("`", ""))
        out.append(line.strip())
    return "\n".join(out)


def is_verbatim(quote: Any, brief_norm: str) -> bool:
    return isinstance(quote, str) and len(norm(quote)) >= 3 and norm(quote) in brief_norm


class _Text:
    """The plain brief with unit boundaries (line starts and sentence starts) so a match can be quoted as its own sentence."""

    def __init__(self, markdown: str):
        self.raw = markdown or ""
        self.plain = plain_text(self.raw)
        self.norm = norm(self.plain)
        starts = {0}
        for m in re.finditer(r"\n+", self.plain):
            starts.add(m.end())
        for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z$#@0-9\"“])", self.plain):
            starts.add(m.end())
        self.starts = sorted(starts)
        # link lists per plain line, to attach a link to the unit that mentions it
        self.line_links: list[tuple[str, list[tuple[str, str]]]] = []
        for raw_line in self.raw.replace("\r", "").split("\n"):
            links = _MD_LINK.findall(raw_line)
            for bare in _URL.findall(_MD_LINK.sub("", raw_line)):
                links.append(("", bare.rstrip(".,;")))
            if links:
                self.line_links.append((norm(plain_text(raw_line)), links))

    def unit(self, pos: int, end: int | None = None) -> str:
        i = bisect.bisect_right(self.starts, pos) - 1
        start = self.starts[i]
        stop_at = (end if end is not None else pos)
        j = bisect.bisect_right(self.starts, stop_at)
        stop = self.starts[j] if j < len(self.starts) else len(self.plain)
        return self.plain[start:stop].strip()

    def links_for(self, quote: str) -> list[tuple[str, str]]:
        q = norm(quote)
        found: list[tuple[str, str]] = []
        for line, links in self.line_links:
            if q and (q in line or line in q):
                found.extend(links)
        return found


def _num(raw: str) -> float:
    return float(raw.replace(",", ""))


def _clean(value: float) -> float | int:
    return int(value) if float(value).is_integer() else value


def item(layer: str, key: str, value: Any, quote: str | None, confidence: float = 0.9, origin: str = "rule",
         status: str = "stated") -> dict:
    return {"id": uuid.uuid4().hex[:10], "layer": layer, "key": f"{layer}.{key}" if "." not in key else key, "value": value,
            "source_quote": quote, "confidence": round(confidence, 2), "status": status, "origin": origin}


def _it(key: str, value: Any, quote: str, confidence: float = 0.9) -> dict:
    layer = key.split(".")[0]
    return {"id": uuid.uuid4().hex[:10], "layer": layer, "key": key, "value": value, "source_quote": quote,
            "confidence": round(confidence, 2), "status": "stated", "origin": "rule"}


def _unclear(key: str, value: Any = None) -> dict:
    return {"id": uuid.uuid4().hex[:10], "layer": key.split(".")[0], "key": key, "value": value, "source_quote": None,
            "confidence": 0.0, "status": "unclear", "origin": "rule"}


# ------------------------------------------------------------------ rule pass

_PLATFORM_TOKENS = {"tiktok": "tiktok", "tt": "tiktok", "instagram": "instagram", "reels": "instagram", "ig": "instagram",
                    "youtube": "youtube_shorts", "shorts": "youtube_shorts", "yt": "youtube_shorts"}
_TZ = {"EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7, "UTC": 0, "GMT": 0, "CET": 1, "BST": 1}
_COUNTRY = {"USA": "US", "US": "US", "UK": "UK", "CA": "CA", "AU": "AU", "NZ": "NZ", "IE": "IE"}


def _platforms_in(text: str) -> list[str]:
    found = []
    for token in re.findall(r"\b(tiktok|tt|instagram|reels|ig|youtube|shorts|yt)\b", text, re.I):
        p = _PLATFORM_TOKENS[token.lower()]
        if p not in found:
            found.append(p)
    return [p for p in PLATFORMS if p in found]


def _year_for(month: int, day: int, now: dt.datetime) -> int:
    """The brief gives no year: the next date on or after today. Marked `year_inferred` so the owner sees it."""
    year = now.year
    try:
        if dt.date(year, month, day) < now.date():
            year += 1
    except ValueError:
        pass
    return year


def rule_items(markdown: str, now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    t = _Text(markdown)
    P = t.plain
    out: list[dict] = []

    def find(pattern: str, flags: int = re.I):
        return re.finditer(pattern, P, flags)

    def add(key: str, value: Any, m, conf: float = 0.9) -> None:
        out.append(_it(key, value, t.unit(m.start(), m.end() - 1), conf))

    # identity: a first line that is only a link is the campaign title
    first = next((ln for ln in t.raw.replace("\r", "").split("\n") if ln.strip()), "")
    m_first = _MD_LINK.fullmatch(first.strip())
    if m_first:
        out.append(_it("identity.name", m_first.group(1).strip(), plain_text(first).strip(), 0.95))
        out.append(_it("identity.campaign_url", m_first.group(2), plain_text(first).strip(), 0.95))
        host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", m_first.group(2)).split("/")[0].lower())
        if host.endswith("clipster.com"):
            out.append(_it("identity.marketplace", "clipster", plain_text(first).strip(), 0.9))
    for m in find(r"\bcontent rewards\b"):
        if not any(i["key"] == "identity.marketplace" for i in out):
            add("identity.marketplace", "content_rewards", m, 0.6)

    # payout
    rate = None
    for pat in (r"\bCPM\b[^\n$]{0,40}\n?\s*\$\s*(\d+(?:\.\d+)?)", r"\$\s*(\d+(?:\.\d+)?)\s*(?:per|/)\s*1(?:,?000|\s*k)\b"):
        for m in find(pat):
            rate = rate or _num(m.group(1))
            add("payout.rate_per_1k", _clean(_num(m.group(1))), m)
            add("payout.model", "cpm", m)
    for m in find(r"\bmax(?:imum)?\s+(?:payout\s+)?per\s+(clip|creator|platform)\b[:\s]*\$\s*([\d,]+(?:\.\d+)?)"):
        add(f"payout.max_per_{m.group(1).lower()}", _clean(_num(m.group(2))), m)
    for m in find(r"\$\s*([\d,]+(?:\.\d+)?)\s*max(?:imum)?\s+per\s+(clip|creator|platform)\b"):
        add(f"payout.max_per_{m.group(2).lower()}", _clean(_num(m.group(1))), m)
    for m in find(r"\$\s*([\d,]+(?:\.\d+)?)\s*maximum\b(?!\s+per\s+(?:clip|creator))"):
        if re.search(r"per platform", P[m.end():m.end() + 20], re.I):
            add("payout.max_per_platform", _clean(_num(m.group(1))), m)
    for m in find(r"\$\s*(\d+(?:\.\d+)?)\s*minimum\b(?!\s+length)"):
        add("payout.min_payout", _clean(_num(m.group(1))), m)
    for m in find(r"payouts?\s+(?:begin|start)\s+at\s+([\d,]+)\s*views"):
        add("payout.min_qualifying_views", int(_num(m.group(1))), m)
    for m in find(r"total budget\s*:?\s*\$\s*([\d,]+(?:\.\d+)?)"):
        add("payout.budget_total", _clean(_num(m.group(1))), m)
    for m in find(r"completion\s*:?\s*(\d+(?:\.\d+)?)\s*%"):
        add("payout.budget_used_pct", _clean(_num(m.group(1))), m)
    for m in find(r"payment methods?\s*\n([^\n]+)"):
        methods = [x.strip() for x in re.split(r"\s*[·,|]\s*", m.group(1)) if x.strip()]
        if methods:
            add("payout.payment_methods", methods, m)

    # platforms
    for m in find(r"(?:\bplatforms?\b\s*:?|only be posted on|can only be posted on)\s*([^\n]+)"):
        plats = _platforms_in(m.group(1))
        if plats:
            add("platforms.allowed", plats, m, 0.95)

    # audience
    for m in find(r"\benglish\b(?!-speaking)"):
        add("audience.language", "en", m, 0.8)
        break
    for m in find(r"\barabic\b"):
        add("audience.language", "ar", m, 0.8)
        break
    for m in find(r"\bT([123])\s+audience"):
        add("audience.tier", f"T{m.group(1)}", m)
    seen_regions: dict[str, list[str]] = {}
    for m in find(r"\b(SEA|T[123]|southeast asia)\b[ -]?focused"):
        seen_regions.setdefault(t.unit(m.start(), m.end() - 1), []).append(m.group(1).upper() if len(m.group(1)) <= 3 else m.group(1))
    for quote, regions in seen_regions.items():
        out.append(_it("audience.forbidden_regions", regions, quote))
    for m in find(r"[^\n.]*\baudience\b[^\n]*"):
        text = m.group(0)
        pct = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if not pct or re.search(r"engagement", text, re.I):
            continue
        paren = re.search(r"\(([A-Z]{2,3}(?:\s*,\s*[A-Z]{2,3})+)\)", text)
        countries = [_COUNTRY.get(c.strip(), c.strip()) for c in paren.group(1).split(",")] if paren else \
            [_COUNTRY[c] for c in re.findall(r"\b(USA|US|UK|CA|AU|NZ)\b", text)]
        if not countries:
            continue
        label = "native English-speaking" if re.search(r"native english", text, re.I) else None
        value = {"pct": _clean(float(pct.group(1))), "countries": list(dict.fromkeys(countries)), "label": label}
        out.append(_it("audience.min_share", value, t.unit(m.start() + text.find(pct.group(0)), m.start() + text.find(pct.group(0)))))

    # content: categories, forbidden lists, safety flags
    for m in find(r"\bpost\s+(?:any\s+)?(?:english\s+)?(.+?)\s+(content|clips)\b(?:\s*\(([^)]*)\))?"):
        head = re.sub(r"\b(viral|english|any)\b", "", m.group(1), flags=re.I).strip()
        paren = [x.strip() for x in re.split(r",", re.sub(r"\betc\b\.?", "", m.group(3) or "", flags=re.I)) if x.strip()]
        names = [x.strip().lower() for x in re.split(r"\s+or\s+|,|/|\+", head, flags=re.I) if x.strip()] if head else []
        cats = names or [p.lower() for p in paren]
        if cats and not re.search(r"low quality|spam", head, re.I):
            add("content.allowed_categories", cats, m, 0.85)
    mode = None
    pos = 0
    for raw_line in P.split("\n"):
        line = raw_line.strip()
        lower = line.lower().lstrip("🚫✅ ")
        start = P.find(raw_line, pos) if raw_line else pos
        pos = start + len(raw_line)
        if not line:
            continue
        if re.match(r"not allowed\b", lower) or re.match(r"(?:prohibited|forbidden)\b[:\s]*$", lower):
            mode = "forbid"
            continue
        if re.match(r"allowed\b", lower):
            mode = "allow"
            continue
        if re.match(r"(?:campaign info|📋)", lower):
            mode = None
        m = re.match(r"(?:prohibited|forbidden)\s*:\s*(.+)", line, re.I)
        if m:
            first_sentence = re.split(r"(?<=[.!?])\s", m.group(1))[0].rstrip(".")
            for part in re.split(r",\s*|\s+or\s+", first_sentence):
                if part.strip():
                    out.append(_it("content.forbidden", part.strip(), t.unit(start), 0.9))
            continue
        if mode == "forbid":
            out.append(_it("content.forbidden", re.sub(r"^post\s+", "", line, flags=re.I).strip(), line, 0.9))
    for m in find(r"cannot be manipulated|(?:no|not)\s+(?:buying|bought) views"):
        add("content.forbidden", "manipulating views or engagement", m, 0.85)
    for pat, flag in ((r"\b(?:gamba|gambling|casino|betting|slots)\b", "gambling"), (r"\bcrypto(?:currency)?\b", "crypto"),
                      (r"suitable for a young audience|young audience|family[- ]friendly", "young_audience")):
        for m in find(pat):
            add("content.safety_flag", flag, m, 0.85)
            break
    for m in find(r"\bAI[- ]generated(?: content)?"):
        if re.search(r"prohibit|not allowed|forbidden|no\b|never", t.unit(m.start()), re.I):
            add("content.safety_flag", "ai_generated_forbidden", m, 0.9)
            break

    # length and performance
    for m in find(r"(\d+)\s*s(?:ec(?:onds?)?)?\.?[ ]+minimum\s+length|minimum\s+length[:\s]*(\d+)\s*s"):
        add("length.min_s", int(m.group(1) or m.group(2)), m)
    for m in find(r"max(?:imum)?\s+(?:length|duration)[:\s]*(?:of\s+)?(\d+)\s*s|(\d+)\s*s(?:ec(?:onds?)?)?\s+max(?:imum)?\s+length"):
        add("length.max_s", int(m.group(1) or m.group(2)), m)
    for m in find(r"(\d+(?:\.\d+)?)\s*%\s*minimum\s+engagement|minimum\s+engagement[:\s]*(\d+(?:\.\d+)?)\s*%"):
        add("performance.min_engagement_pct", _clean(float(m.group(1) or m.group(2))), m)

    # captions
    for m in find(r"[^\n.]*\b(?:must (?:include|contain|have)|required|has to include)\b[^\n]*"):
        text = m.group(0)
        if re.search(r"\b(?:never|do not|don'?t|no|not)\b", text, re.I) and not re.search(r"must", text, re.I):
            continue
        for tag in re.findall(r"#\w+", text):
            if tag.lower() in ("#ad", "#sponsored"):
                continue
            out.append(_it("captions.required_hashtags", tag, t.unit(m.start()), 0.95))
        for mention in re.findall(r"(?<!\w)@\w+", text):
            out.append(_it("captions.required_mentions", mention, t.unit(m.start()), 0.95))
    forbid_ad = re.search(r"\b(?:never|do not|don'?t|no)\s+(?:put\s+|use\s+|add\s+|include\s+|post\s+)?(?:an?\s+)?#?(?:ad|sponsored)\b|"
                          r"\bno\s+ad[- ]disclosure|without\s+(?:an?\s+)?#ad", P, re.I)
    require_ad = re.search(r"\bmust\s+(?:include|use|add|carry|disclose)\b[^.\n]{0,25}(?:#ad|#sponsored|paid partnership)|"
                           r"(?:#ad|paid partnership)\s+(?:is\s+)?required", P, re.I)
    if forbid_ad:
        add("captions.disclosure_policy", "forbidden", forbid_ad, 0.95)
    elif require_ad:
        add("captions.disclosure_policy", "required", require_ad, 0.95)
    for m in find(r"captions?\s*:\s*(no required copy[^\n]*)"):
        add("captions.copy_rules", m.group(1).strip(), m, 0.8)

    # assets
    for m in find(r"logos?\s+of\s+any\s+kind\s+(?:are\s+|is\s+)?not\s+allowed|\bno\s+logos?\b|logos?\s+(?:are|is)\s+(?:not allowed|forbidden|prohibited)"):
        add("assets.logo", {"requirement": "forbidden", "link": None}, m, 0.95)
    has_forbidden_logo = any(i["key"] == "assets.logo" for i in out)
    if not has_forbidden_logo:
        for m in find(r"\b(?:add|use|include|put|apply|place)\b[^.\n]{0,30}\blogos?\b"):
            quote = t.unit(m.start(), m.end() - 1)
            links = [u for text, u in t.links_for(quote) if "guideline" not in text.lower() and "guideline" not in u.lower()]
            out.append(_it("assets.logo", {"requirement": "required", "link": links[0] if links else None}, quote, 0.9))
            break
    guides = []
    for m in find(r"[^\n.]*\blogo\b[^\n.]*\bguidelines?\b[^\n]*|[^\n.]*\bguidelines?\b[^\n.]*\blogo\b[^\n]*"):
        quote = t.unit(m.start())
        pairs = t.links_for(quote) or [("", u) for u in _URL.findall(quote)]
        if pairs:   # a unit whose link has text ("LOGO GUIDELINES") beats a bare address
            guides.append((0 if any(txt for txt, _ in pairs) else 1, quote, pairs[0][1]))
    if guides:
        _, quote, url = min(guides, key=lambda g: g[0])
        out.append(_it("assets.logo_guidelines", {"link": url}, quote, 0.9))
    for m in find(r"\blogo\b[^\n.]*?\b(top|bottom|upper|lower)\s+half\b|\blogo\b[^\n.]*?\b(top|bottom)[- ](left|right)\b"):
        place = f"{(m.group(1) or m.group(2)).lower()}_half" if m.group(1) else f"{m.group(2).lower()}_{m.group(3).lower()}"
        add("assets.logo_placement", place.replace("upper", "top").replace("lower", "bottom"), m)
    for m in find(r"\blogo\b[^\n.]*\b(green[- ]?screen|blue[- ]?screen|animated|static)\b"):
        kind = m.group(1).lower()
        add("assets.logo_type", "green_screen" if "screen" in kind else kind, m, 0.8)
        break

    # sources
    for m in find(r"\b(?:no|never|not allowed|forbidden)\b[^.\n]{0,20}\bbroadcast\b|\bbroadcast footage\b[^.\n]{0,20}\b(?:not allowed|forbidden)"):
        add("sources.forbidden_class", "broadcast", m, 0.9)
        break

    # account requirements
    for m in find(r"\b([\w+]+)\s+branded\s+pages?"):
        add("account.requirements", {"type": "branded_page", "detail": m.group(1).upper() if len(m.group(1)) <= 4 else m.group(1)}, m)
    for m in find(r"only\s+dedicated\s+pages\s+in\s+the\s+(.+?)\s+niche"):
        add("account.requirements", {"type": "niche_page", "detail": m.group(1).strip()}, m)
    for m in find(r"\bbio\b[^.\n]{0,30}\b(?:must\s+)?(?:carry|include|contain|have)\b[^.\n]{0,25}\blink|\blink\s+in\s+(?:the\s+|your\s+)?bio\b"):
        add("account.requirements", {"type": "bio_link", "detail": "website link in the account bio"}, m)

    # deadlines
    for m in find(r"\bno\s+more\s+(.+?)\s+after\s+(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})\s*([AP]M)\s*([A-Z]{2,4})?"):
        month, day, hour, minute = int(m.group(2)), int(m.group(3)), int(m.group(4)) % 12, int(m.group(5))
        hour += 12 if m.group(6).upper() == "PM" else 0
        tz = (m.group(7) or "").upper()
        value: dict[str, Any] = {"type": "content_type_stop", "content_type": m.group(1).strip().lower(),
                                 "date_text": f"{month}/{day} {m.group(4)}:{m.group(5)}{m.group(6).upper()} {tz}".strip(),
                                 "timezone": tz or None, "at": None, "year_inferred": True}
        if tz in _TZ:
            year = _year_for(month, day, now)
            offset = dt.timezone(dt.timedelta(hours=_TZ[tz]))
            try:
                value["at"] = dt.datetime(year, month, day, hour, minute, tzinfo=offset).isoformat()
            except ValueError:
                pass
        add("deadlines.deadline", value, m, 0.85 if value["at"] else 0.6)
    for m in find(r"\b(?:deadline|ends?(?: on)?|end date|closes?)\s*:?\s*(\d{4}-\d{2}-\d{2})"):
        add("deadlines.deadline", {"type": "campaign_end", "content_type": None, "at": f"{m.group(1)}T23:59:00", "timezone": None,
                                   "date_text": m.group(1), "year_inferred": False}, m, 0.85)

    # submission, performance, enforcement
    for m in find(r"within\s+(\d+)\s+minutes?\s+of\s+(?:posting|publish\w*)"):
        add("submission.within_minutes", int(m.group(1)), m)
    for m in find(r"duplicate\s+(?:urls?|links?|posts?|videos?)\s+(?:are\s+)?rejected"):
        add("submission.duplicates_rejected", True, m)
    for m in find(r"\bone strike\b[^\n]*"):
        quote = t.unit(m.start(), m.end() - 1)
        consequence = quote.split(":", 1)[1].strip().rstrip(".") if ":" in quote else quote
        add("enforcement.strikes", {"strikes": 1, "consequence": consequence}, m)

    # rejection reasons and priority
    for m in find(r"why clips get rejected\s*:\s*"):
        for n, line in enumerate(P[m.end():].split("\n")):   # the list continues on the next lines until a blank or a new numbered section
            if not line.strip() or (n and re.match(r"\d+\s+[A-Z]", line)):
                break
            quote = t.unit(m.start(), m.end() + len(line) - 1) if n == 0 else line.strip()
            for part in re.split(r"\s*;\s*", line.strip().rstrip(".")):
                if part.strip():
                    out.append(_it("rejection.reasons", part.strip(), quote, 0.9))
    for m in find(r"\bprioriti[sz]e\b|\bgets?\s+priority\b"):
        quote = t.unit(m.start(), m.end() - 1)
        head = quote.partition(":")[0].strip() if quote.find(m.group(0)) < quote.find(":") else quote   # the label before a colon names it
        out.append(_it("priority.content", head, quote, 0.75))
    return out


# ------------------------------------------------------------------ LLM pass

def _coerce(key: str, value: Any, quote: str) -> Any:
    """The value in the catalog's type, or None when it does not fit. A number must appear in its own quote."""
    kind = CATALOG[key][0]
    if value is None:
        return None
    if kind == "num":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        digits = str(_clean(float(value))).replace(".0", "")
        return value if digits in quote.replace(",", "") else None
    if kind == "bool":
        return value if isinstance(value, bool) else None
    if kind == "str":
        return str(value).strip() or None
    if kind.startswith("enum:"):
        return value if value in kind[5:].split("|") else None
    if kind == "list":
        return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) and value else None
    if kind == "dict":
        return value if isinstance(value, dict) and value else None
    return None


def validate_llm(raw: Any, markdown: str) -> list[dict]:
    """Keep only items with a known key, a quote that is in the brief, and a value that fits and is supported by the quote."""
    brief = _Text(markdown).norm
    items = (raw or {}).get("items") if isinstance(raw, dict) else None
    out: list[dict] = []
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict):
            continue
        key = f"{entry.get('layer')}.{entry.get('key')}" if "." not in str(entry.get("key")) else str(entry.get("key"))
        quote = entry.get("quote") or entry.get("source_quote")
        if key not in CATALOG or not is_verbatim(quote, brief):
            continue
        value = _coerce(key, entry.get("value"), str(quote))
        if value is None:
            continue
        if key == "platforms.allowed":
            value = [p for p in value if p in PLATFORMS]
            if not value:
                continue
        try:
            conf = max(0.0, min(1.0, float(entry.get("confidence", 0.7))))
        except (TypeError, ValueError):
            conf = 0.7
        out.append({"id": uuid.uuid4().hex[:10], "layer": key.split(".")[0], "key": key, "value": value,
                    "source_quote": re.sub(r"\s+", " ", str(quote)).strip(), "confidence": round(conf, 2), "status": "stated",
                    "origin": "llm"})
    return out


# ------------------------------------------------------------------ merge

def _same(key: str, a: Any, b: Any) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) < 1e-9
    if isinstance(a, dict) and isinstance(b, dict):
        shared = [k for k in a if k in b and a[k] is not None and b[k] is not None]
        return all(norm(str(a[k])) == norm(str(b[k])) for k in shared)
    return norm(str(a)) == norm(str(b))


def merge_items(rule: list[dict], other: list[dict]) -> list[dict]:
    """Rule items first, then the other pass. A repeating key keeps every distinct value; a single key that the two passes
    answer differently becomes `conflict` (the rule value is shown, the other is kept as `alternative`)."""
    merged: list[dict] = []
    for new in rule + other:
        key = new["key"]
        twins = [m for m in merged if m["key"] == key]
        if not twins:
            merged.append(dict(new))
            continue
        if key in REPEATING:
            hit = next((m for m in twins if _same(key, m["value"], new["value"]) or
                        (m["source_quote"] and m["source_quote"] == new["source_quote"] and new["origin"] != m["origin"])), None)
            if hit:
                _agree(hit, new)
            else:
                merged.append(dict(new))
            continue
        first = twins[0]
        if _same(key, first["value"], new["value"]):
            _agree(first, new)
        elif first["status"] != "conflict" or new["origin"] != first["origin"]:
            first["status"] = "conflict"
            first["alternatives"] = [*first.get("alternatives", []), {"value": new["value"], "source_quote": new["source_quote"],
                                                                      "origin": new["origin"]}]
            first["confidence"] = round(min(first["confidence"], new["confidence"]) * 0.7, 2)
    return merged


def _agree(kept: dict, new: dict) -> None:
    if new["origin"] != kept["origin"]:
        kept["origin"] = "both"
        kept["confidence"] = round(min(1.0, max(kept["confidence"], new["confidence"]) + 0.05), 2)


def add_unclear(items: list[dict]) -> list[dict]:
    have = {i["key"] for i in items if i["status"] != "unclear"}
    out = list(items)
    for key in CORE:
        if key not in have:
            out.append(_unclear(key, "unspecified" if key == "captions.disclosure_policy" else None))
    return out


def blocking_keys(spec: dict) -> list[str]:
    """Unclear items the owner must answer (or mark not applicable) before the campaign is approved."""
    return [i["key"] for i in spec.get("items", []) if i["status"] == "unclear" and i["key"] in BLOCKING and not i.get("dismissed")]


# ------------------------------------------------------------------ entry points

def build_spec(markdown: str, llm_raw: Any = None, now: dt.datetime | None = None) -> dict:
    """Pure: rule pass, validated LLM output, merge, unclear fill. No I/O."""
    warnings = []
    if _INJECTION.search(markdown or ""):
        warnings.append("The text contains instructions addressed to an AI. They were ignored; only campaign facts were read.")
    items = merge_items(rule_items(markdown, now), validate_llm(llm_raw, markdown))
    items = add_unclear(items)
    from . import brief_links   # local import: brief_links imports this module's helpers

    spec = {"version": VERSION, "items": items, "links": brief_links.classify_all(markdown), "warnings": warnings}
    spec["blocking"] = blocking_keys(spec)
    return spec


def extract(conn, registry, markdown: str, owner_id=None, now: dt.datetime | None = None) -> dict:
    """Brief text to spec through the adapter registry. If the LLM is unavailable the rule pass still answers and the rest
    stays unclear (the declared fallback)."""
    if not (markdown or "").strip():
        raise AppError("Paste the brief text.", "empty_brief")
    raw: Any = {}
    cost = 0
    try:
        raw, cost = registry.call(conn, "llm", "complete_json", {"task": "brief_spec", "payload": {
            "brief_text": markdown[:30000], "catalog": {k: v[1] for k, v in CATALOG.items()}}}, owner_id)
    except AppError:
        raw = {}
    spec = build_spec(markdown, raw, now)
    spec["cost_usd"] = float(cost or 0)
    return spec


def merge_pasted(spec: dict, link_url: str, markdown: str, llm_raw: Any = None) -> dict:
    """The owner pasted the text of a link that needed it. Its items join the spec (an item the owner already stated wins; a
    different value for a single key becomes a conflict); the link is marked pasted."""
    extra = merge_items(rule_items(markdown), validate_llm(llm_raw, markdown))
    for i in extra:
        i["origin"] = f"paste:{link_url}"
    existing = [i for i in spec["items"] if i["status"] != "unclear"]
    gaps = [i for i in spec["items"] if i["status"] == "unclear" and i["key"] not in {e["key"] for e in extra}]
    merged = merge_items(existing, extra)
    out = {**spec, "items": add_unclear(merged + gaps)}
    out["links"] = [({**l, "fetch": {"status": "pasted", "note": "The owner pasted this page's text."}} if l["url"] == link_url else l)
                    for l in spec.get("links", [])]
    out["blocking"] = blocking_keys(out)
    return out


def validate_spec(spec: Any) -> dict:
    """A spec coming back from the browser (the owner may have edited it): keep the known shape only."""
    if not isinstance(spec, dict) or not isinstance(spec.get("items"), list):
        raise AppError("The extracted rules are missing. Read the brief again.", "bad_spec")
    items = []
    for entry in spec["items"]:
        if not isinstance(entry, dict) or entry.get("key") not in CATALOG:
            continue
        status = entry.get("status") if entry.get("status") in ("stated", "unclear", "conflict") else "unclear"
        items.append({"id": str(entry.get("id") or uuid.uuid4().hex[:10]), "layer": entry["key"].split(".")[0], "key": entry["key"],
                      "value": entry.get("value"), "source_quote": entry.get("source_quote"),
                      "confidence": float(entry.get("confidence") or 0), "status": status,
                      "origin": str(entry.get("origin") or "owner"), **({"alternatives": entry["alternatives"]} if entry.get("alternatives") else {}),
                      **({"dismissed": True} if entry.get("dismissed") else {})})
    clean = {"version": VERSION, "items": items, "links": [l for l in spec.get("links") or [] if isinstance(l, dict) and l.get("url")],
             "warnings": [str(w) for w in spec.get("warnings") or []]}
    clean["blocking"] = blocking_keys(clean)
    return clean


def require_answers(spec: dict) -> None:
    if spec.get("blocking"):
        names = ", ".join(k.split(".")[1].replace("_", " ") for k in spec["blocking"])
        raise AppError(f"Answer these first, or mark them not applicable: {names}.", "spec_unclear")


def values(spec: dict | None, key: str) -> list[Any]:
    """The stated values for a key (conflicts count: the rule value is shown and enforced until the owner decides)."""
    return [i["value"] for i in (spec or {}).get("items", []) if i["key"] == key and i["status"] in ("stated", "conflict")
            and i["value"] is not None and not i.get("dismissed")]


def first_value(spec: dict | None, key: str) -> Any:
    found = values(spec, key)
    return found[0] if found else None
