"""The five real briefs from docs/briefs/real-briefs.md, read from the file so the test set is the owner's own text."""
from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[2] / "docs" / "briefs" / "real-briefs.md"


def briefs() -> dict[str, str]:
    text = DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"^## \d+\. (.+?)\n.*?```\n(.*?)```", text, re.S | re.M)
    return {title.split(" (")[0].strip(): body.strip() for title, body in blocks}
