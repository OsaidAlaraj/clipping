"""Orchestration for the review screen: extract the spec, read the public links once (depth 1), merge what they say, and take
text the owner pasted for a link that needed it. Uses the adapter registry for the LLM; nothing here writes to the database."""
from __future__ import annotations

from typing import Any

from . import brief_links, brief_spec
from .core import AppError


def _llm(conn, registry, text: str, owner_id) -> Any:
    try:
        raw, _cost = registry.call(conn, "llm", "complete_json", {"task": "brief_spec", "payload": {
            "brief_text": text[:30000], "catalog": brief_spec.llm_catalog()}}, owner_id)
        return raw
    except AppError:
        return {}


def add_pasted_text(conn, registry, spec: dict, url: str, text: str, owner_id=None, status: str = "pasted") -> dict:
    """Merge the text of one link (pasted by the owner, or read from a public page) into the spec."""
    if not (text or "").strip():
        raise AppError("Paste the text of that page first.", "empty_brief")
    spec = brief_spec.validate_spec(spec)
    return brief_spec.merge_pasted(spec, url, text, _llm(conn, registry, text, owner_id), status=status)


def read_brief(conn, registry, markdown: str, owner_id=None, read_links: bool = True, fetcher=None) -> dict:
    spec = brief_spec.extract(conn, registry, markdown, owner_id)
    if not read_links:
        return spec
    links = brief_links.read_all(spec["links"], *([fetcher] if fetcher else []))
    spec = {**spec, "links": links}
    for link in links:
        if link["fetch"]["status"] == "fetched":
            text = link["fetch"].pop("text")   # the page text is merged, not shipped to the browser
            spec = brief_spec.merge_pasted(spec, link["url"], text, _llm(conn, registry, text, owner_id), status="fetched")
    return spec
