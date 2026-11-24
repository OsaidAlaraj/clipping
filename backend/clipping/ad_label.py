"""The campaign's ad-label switch (owner notes 2, note 2).

The label is the "Ad" line that starts the caption and is burned onto the picture. The owner can switch it off per campaign.
A brief that requires the label keeps it on: no setting overrides a rule from the brief (hard rule). Compliance checks the
same thing again at render time, so a clip never goes out with a required label missing."""
from __future__ import annotations

# What a clip carries when the label is off. `required` says whether the brief forbids switching it off.
OFF_LABEL = {"id": None, "caption_text": "", "overlay_text": None, "label_off": True}


def required_by_brief(rules: dict | None) -> bool:
    return bool(((rules or {}).get("disclosure") or {}).get("required_by_brief"))


def is_off(campaign: dict | None) -> bool:
    return (campaign or {}).get("ad_label") is False


def off_label(rules: dict | None) -> dict:
    return {**OFF_LABEL, "required": required_by_brief(rules)}
