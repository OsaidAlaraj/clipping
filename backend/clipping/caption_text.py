"""Text helpers shared by the caption lab, the match checker and the packaging: tokens, Arabic normalising, number words,
transcript windows. Pure functions, no database."""
from __future__ import annotations

import re

WORD = re.compile(r"[\w؀-ۿ]+", re.U)
TAGS = re.compile(r"[#@][\w؀-ۿ]+", re.U)
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_TASHKEEL = re.compile("[ً-ٰٟـ]")
_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")

EN_STOP = set("""a an the and or but if so of to in on at by for with from as is are was were be been being it its this that these those
i you he she we they me him her us them my your his our their not no do does did done have has had will would can could should may
might just than then there here what when where which who whom why how about into over under out up down off very more most some any
all each every also too only own same such get got like really thing things say said says""".split())
AR_STOP = set("""في من على الى إلى عن مع هذا هذه ذلك تلك هو هي هم هن انا أنا انت أنت نحن كان كانت يكون ما لا لم لن قد كل بعض او أو ثم لكن
اذا إذا الي التي الذي الذين هناك هنا كيف لماذا ليش شو وين متى""".split())


def norm(text: str) -> str:
    """Lower case, Arabic-Indic digits to ASCII, Arabic letter variants and diacritics folded, spaces collapsed."""
    text = text.translate(_DIGITS).lower()
    text = _TASHKEEL.sub("", text)
    text = re.sub("[أإآ]", "ا", text).replace("ى", "ي").replace("ة", "ه")
    return " ".join(text.split())


def stem(word: str) -> str:
    word = norm(word)
    if re.search("[؀-ۿ]", word):
        for prefix in _AR_PREFIXES:
            if word.startswith(prefix) and len(word) - len(prefix) >= 3:
                return word[len(prefix):]
        if word.startswith("و") and len(word) >= 5:
            return word[1:]
        return word
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def strip_tags(text: str) -> str:
    return " ".join(TAGS.sub(" ", text).split())


def content_stems(text: str, language: str) -> list[str]:
    """The stems of the meaningful words (no stop words, no very short words, no numbers, no hashtags or mentions)."""
    stop = {norm(s) for s in (AR_STOP if language == "ar" else EN_STOP)}
    out = []
    for raw in WORD.findall(strip_tags(text)):
        word = norm(raw)
        if word in stop or len(word) < 3 or word.isdigit():
            continue
        out.append(stem(raw))
    return out


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100,
                "thousand": 1000, "million": 1000000, "billion": 1000000000, "واحد": 1, "اثنين": 2, "ثلاثه": 3, "اربعه": 4,
                "خمسه": 5, "سته": 6, "ستين": 60, "عشره": 10, "ميه": 100, "الف": 1000, "مليون": 1000000}


def numbers(text: str) -> set[str]:
    """Digit numbers in the text (commas removed), plus spelled-out numbers as digits."""
    flat = norm(text)
    found = {m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", flat)}
    for word in WORD.findall(flat):
        if word in NUMBER_WORDS:
            found.add(str(NUMBER_WORDS[word]))
    return found


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?؟])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def transcript_text(words: list[dict], start_s: float, end_s: float) -> str:
    """The spoken words of a clip window, as plain text."""
    return " ".join(w["text"] for w in words if float(w["start"]) >= float(start_s) - 0.05 and float(w["end"]) <= float(end_s) + 0.15).strip()


def clip_to(text: str, limit: int) -> str:
    """Cut at a word boundary to at most `limit` characters."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-–—")
    return cut or text[:limit]


# ------------------------------------------------------------------ stray tokens in a provider's words

_PREFIX_ONLY = frozenset({"ال", "وال", "بال", "فال", "كال", "لل"})   # the article and its common joined forms: never a word alone
STRAY_GAP_S = 0.6


def repair_stray_tokens(words: list[dict]) -> list[dict]:
    """Deepgram's Arabic words sometimes arrive as a lone punctuation mark (a "،" with its own time, which then takes a place in
    a caption line and starts it early) or with the article cut off its noun ("ال" then "خبراء"). Punctuation joins the word
    before it (it keeps that word's time), and an article joins the word after it. Nothing else changes. Pure: returns new dicts."""
    out: list[dict] = []
    open_prefix = False      # the last word kept is an article waiting for its noun
    for word in words:
        text = str(word.get("text") or "")
        if not text.strip():
            continue
        if not re.search(r"\w", text, re.U):      # punctuation only
            if out:
                out[-1] = {**out[-1], "text": out[-1]["text"] + text.strip()}
            continue
        if (open_prefix and out and float(word["start"]) - float(out[-1]["end"]) <= STRAY_GAP_S
                and out[-1].get("speaker") == word.get("speaker")):
            prev = out[-1]
            confs = [c for c in (prev.get("confidence"), word.get("confidence")) if c is not None]
            out[-1] = {**word, "text": prev["text"] + text, "start": prev["start"], "confidence": min(confs) if confs else None}
            open_prefix = False
            continue
        out.append(dict(word))
        open_prefix = text in _PREFIX_ONLY
    return out
