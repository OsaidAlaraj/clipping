"""The link layer of the brief extractor: classify every link, read only public pages, ask for a paste when a page needs a sign-in.

Rules: only https links to public hosts (`sources.check_public_https`, SSRF-safe, redirects checked by hand), depth 1 (a page's own
links are never followed), a size limit, no credentials, never a download from Drive or Frame.io (those are only classified).
Google Docs are read through `/mobilebasic`; a Notion public page is read when the server returns text, otherwise `needs_paste`."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse

from .core import AppError

MAX_BYTES = 1_000_000
MAX_TEXT = 20_000
MIN_TEXT = 200          # a page with less readable text than this is a shell (JavaScript or a sign-in wall)
_MD_LINK = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\((https?://[^)\s]+)\)")
_BARE = re.compile(r"https?://[^\s)\]>]+")
_SIGN_IN = re.compile(r"sign in|log in|login|request access|you need access|enable javascript|verify you are human", re.I)


def classify(url: str, text: str = "") -> str:
    """logo_asset_folder, guidelines_doc, content_bank_video, campaign_page, submission_page, social_profile, other."""
    u = urlparse(url)
    host = (u.hostname or "").lower().removeprefix("www.")
    path = u.path.lower()
    label = f"{text} {path}".lower()
    if host in ("f.io", "frame.io") or host.endswith(".frame.io") or (host == "drive.google.com" and "/folders/" in path) or \
            host in ("dropbox.com", "mega.nz", "we.tl", "wetransfer.com"):
        return "logo_asset_folder" if "logo" in label or host in ("f.io", "frame.io") or "/folders/" in path else "other"
    if "guideline" in label or host in ("notion.so", "app.notion.com", "notion.site") or host.endswith(".notion.site") or \
            (host == "docs.google.com" and "/document/" in path):
        return "guidelines_doc"
    if host in ("youtu.be",) or (host.endswith("youtube.com") and (path.startswith(("/watch", "/shorts"))) or
                                  (host.endswith("instagram.com") and path.startswith(("/reel", "/p/", "/tv/"))) or
                                  (host.endswith("tiktok.com") and "/video/" in path)):
        return "content_bank_video"
    if host.endswith("clipster.com") or "contentrewards" in host.replace(".", "") or "/campaign" in path or "/discover" in path:
        return "submission_page" if "submit" in label else "campaign_page"
    if "submit" in label:
        return "submission_page"
    if host.endswith("instagram.com") or host.endswith("tiktok.com") or host.endswith("youtube.com") or host in ("x.com", "twitter.com"):
        return "social_profile"
    return "other"


def links_in(markdown: str) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for text, url in _MD_LINK.findall(markdown or ""):
        found.setdefault(url.rstrip(".,;"), text.strip())
    for url in _BARE.findall(_MD_LINK.sub("", markdown or "")):
        found.setdefault(url.rstrip(".,;:!?"), "")
    return [(u, t) for u, t in found.items()]


READABLE = ("guidelines_doc", "campaign_page", "other")


def classify_all(markdown: str) -> list[dict]:
    out = []
    for url, text in links_in(markdown):
        kind = classify(url, text)
        status, note = ("pending", "") if kind in READABLE else ("classified_only", {
            "logo_asset_folder": "A shared folder: not downloaded. Add the logo file yourself.",
            "content_bank_video": "A source video: queued only when you add it as footage.",
            "social_profile": "A profile link: not opened.", "submission_page": "Where clips are submitted: not opened."}.get(kind, ""))
        out.append({"url": url, "text": text, "kind": kind, "fetch": {"status": status, "note": note}})
    return out


def readable_url(url: str) -> str:
    """The address that returns text: a Google Doc through /mobilebasic."""
    u = urlparse(url)
    m = re.match(r"(/document/(?:u/\d+/)?d/[\w-]+)(?:/.*)?$", u.path)
    if (u.hostname or "").lower() == "docs.google.com" and m:
        return f"https://docs.google.com{m.group(1)}/mobilebasic"
    return url


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe"}
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr", "section", "article", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("* ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html or "")
    text = "".join(parser.parts).replace("\xa0", " ")
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def fetch_public(url: str) -> tuple[int, str, str]:
    """(status, content_type, body text) for one public https page. Raises AppError for a refused address."""
    import httpx

    from .sources import check_public_https

    target = readable_url(url)
    with httpx.Client(timeout=httpx.Timeout(10, read=15), follow_redirects=False) as client:
        for _hop in range(4):
            check_public_https(target)
            with client.stream("GET", target, headers={"User-Agent": "clipping-brief-reader/1.0", "Accept": "text/html,text/plain"}) as r:
                if r.is_redirect and r.headers.get("location"):
                    target = urljoin(target, r.headers["location"])
                    continue
                ctype = r.headers.get("content-type", "")
                if not (ctype.startswith("text/") or "html" in ctype or "json" in ctype):
                    return r.status_code, ctype, ""
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                return r.status_code, ctype, body[:MAX_BYTES].decode(r.encoding or "utf-8", errors="replace")
    return 0, "", ""


def read_link(link: dict, fetcher: Callable[[str], tuple[int, str, str]] = fetch_public) -> dict:
    """Read one readable link. The result has `fetch.status`: fetched (with text) or needs_paste (with a plain sentence)."""
    if link["kind"] not in READABLE:
        return link
    need = {"status": "needs_paste", "note": "This page needs a sign-in or returns no text. Open it, copy its text and paste it here."}
    try:
        status, ctype, body = fetcher(link["url"])
    except AppError as e:
        return {**link, "fetch": {"status": "needs_paste", "note": f"{e}. Open it, copy its text and paste it here."}}
    except Exception:
        return {**link, "fetch": {**need, "note": "The page could not be reached. Open it, copy its text and paste it here."}}
    if status in (401, 403) or status >= 400:
        return {**link, "fetch": need}
    text = html_to_text(body) if "html" in ctype or "<html" in body[:500].lower() or "<body" in body[:2000].lower() else body.strip()
    if len(text) < MIN_TEXT or (_SIGN_IN.search(text[:400]) and len(text) < 1500):
        return {**link, "fetch": need}
    return {**link, "fetch": {"status": "fetched", "note": "Read the public page.", "text": text[:MAX_TEXT]}}


def read_all(links: list[dict], fetcher: Callable[[str], tuple[int, str, str]] = fetch_public, limit: int = 8) -> list[dict]:
    """Depth 1: only the links already in the brief, at most `limit` pages."""
    out, used = [], 0
    for link in links:
        if link["kind"] in READABLE and link["fetch"]["status"] == "pending" and used < limit:
            out.append(read_link(link, fetcher))
            used += 1
        else:
            out.append(link)
    return out
