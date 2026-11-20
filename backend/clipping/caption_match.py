"""Caption-versus-video match checker. A caption may only say what the clip says: every number, quote and claim has to be
supported by the transcript, and clickbait the clip does not deliver is rejected.

Deterministic checks run first and always. A model's claim check (`caption_check` task) can only add rejections, and a claim it
calls supported counts only when it quotes words that really are in the transcript, so a model can never wave a caption through
on its own say-so (same rule as Agent B and Agent C)."""
from __future__ import annotations

import re

from .caption_text import content_stems, norm, numbers, stem, strip_tags, WORD

QUOTED = re.compile(r"[\"“«]([^\"”»]{3,})[\"”»]")

# Words a teaser frame adds ("what happens ...? watch to the end"). They promise nothing about the content.
_FRAME = {
    "en": "watch wait listen hear happens happen find truth answer video clip explains explain reason story end until here actually "
          "heres learn see".split(),
    "ar": "شوف شوفوا شاهد شاهدوا استمع اسمع الجواب الفيديو المقطع النهايه للاخر الاخر لين القصه يصير بيصير صار يحدث ماذا وش شو "
          "ليش لماذا السبب".split(),
}
FRAME_STEMS = {lang: {stem(w) for w in words} | {norm(w) for w in words} for lang, words in _FRAME.items()}

# Clickbait that is allowed only when the transcript really contains the same idea.
CLICKBAIT = {
    "en": ["you won't believe", "you wont believe", "shocking", "gone wrong", "exposed", "blow your mind", "mind-blowing", "changed my life",
           "changes everything", "they don't want you to know", "nobody talks about", "secret", "insane", "illegal", "banned", "destroyed",
           "100%", "guaranteed", "went viral", "must watch", "wait for it"],
    "ar": ["لن تصدق", "ما راح تصدق", "ما رح تصدق", "صادم", "صدمة", "فضيحة", "فضيحه", "سر خطير", "سر ", "خطير", "انفجر", "ممنوع",
           "يغير حياتك", "مضمون", "لا يفوتك", "انتظر النهاية", "انتظر النهايه"],
}
# Absolute words: fine when the speaker said them, a claim of ours when they did not.
ABSOLUTES = {
    "en": ["best", "worst", "never", "always", "everyone", "nobody", "proven", "only way", "number one", "#1"],
    "ar": ["الافضل", "الاسوأ", "ابدا", "دايما", "دائما", "الجميع", "كل الناس", "ما حد", "ولا واحد", "الاول"],
}

MIN_COVERAGE = {"matched": 0.7, "curiosity": 0.6, "commentary": 0.0}


def _present(needle: str, haystack_norm: str, stems: set[str]) -> bool:
    n = norm(needle)
    return n in haystack_norm or stem(needle) in stems


def _split_body(text: str) -> str:
    return strip_tags(text)


def check(text: str, transcript: str, language: str, kind: str = "matched", claims: list | None = None) -> dict:
    """Judge one caption body (hashtags and mentions are ignored; the disclosure belongs outside the body).
    kind: "matched" (the clip says it), "curiosity" (an honest open question the clip answers) or "commentary"
    (the owner's own take: opinion is allowed, numbers, quotes and clickbait are still checked, coverage is not).
    Returns {"ok", "reasons": [{"code", "detail"}], "coverage", "unsupported"}."""
    body = _split_body(text)
    spoken = norm(transcript)
    spoken_stems = {stem(w) for w in WORD.findall(spoken)}
    reasons: list[dict] = []

    def reject(code: str, detail: str) -> None:
        reasons.append({"code": code, "detail": detail})

    if not body:
        reject("empty", "The caption has no text.")
    # 1. numbers must be said in the clip
    said = numbers(transcript)
    for number in sorted(numbers(body) - said):
        reject("unsupported_number", f"The number {number} is not in the transcript.")
    # 2. anything in quotation marks is a quote: the words must be in the transcript
    for quote in QUOTED.findall(body):
        if norm(quote) not in spoken:
            reject("invented_quote", f"The quoted words \"{quote[:60]}\" are not in the transcript.")
    low = norm(body)
    # 3. clickbait the clip does not deliver
    for phrase in CLICKBAIT.get(language, CLICKBAIT["en"]):
        p = norm(phrase).strip()
        if p and p in low and p not in spoken:
            reject("clickbait", f"\"{phrase.strip()}\" promises something the clip does not say.")
    # 4. absolute claims need the speaker's own words
    if kind != "commentary":
        for word in ABSOLUTES.get(language, ABSOLUTES["en"]):
            w = norm(word)
            if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low) and w not in spoken:
                reject("unsupported_absolute", f"\"{word}\" is a claim the transcript does not make.")
    # 5. coverage: the meaningful words of a caption should come from the clip
    frame = FRAME_STEMS.get(language, set())
    words = [w for w in content_stems(body, language) if w not in frame]
    unsupported = [w for w in dict.fromkeys(words) if not (w in spoken_stems or any(len(w) >= 5 and (s.startswith(w) or w.startswith(s)) and min(len(s), len(w)) >= 5 for s in spoken_stems))]
    coverage = 1.0 if not words else round(1 - len([w for w in words if w in set(unsupported)]) / len(words), 2)
    need = MIN_COVERAGE.get(kind, 0.7)
    if words and coverage < need:
        reject("low_coverage", f"Only {int(coverage * 100)}% of the caption's key words are in the transcript (needs {int(need * 100)}%): "
                               f"not said: {', '.join(unsupported[:5])}.")
    # 6. a model's claim check can only add rejections
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        quote = " ".join(str(claim.get("quote") or "").split())
        if claim.get("supported") is not True:
            reject("claim_unsupported", f"Claim not supported: {str(claim.get('claim') or '')[:80]}")
        elif not quote or norm(quote) not in spoken:
            reject("claim_quote_missing", f"A claim was called supported without a quote from the transcript: {str(claim.get('claim') or '')[:80]}")
    return {"ok": not reasons, "reasons": reasons, "coverage": coverage, "unsupported": unsupported}


def hashtag_supported(tag: str, transcript: str, extra: set[str] | None = None) -> bool:
    """A made-up hashtag must be a word the clip says (or a given niche word); required campaign tags skip this check."""
    token = norm(tag.lstrip("#").replace("_", " "))
    spoken = norm(transcript)
    stems = {stem(w) for w in WORD.findall(spoken)} | {stem(w) for w in (extra or set())}
    parts = [p for p in token.split() if p]
    return bool(parts) and all(p in spoken or stem(p) in stems for p in parts)
