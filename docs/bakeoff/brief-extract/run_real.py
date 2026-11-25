"""One real run of the brief extractor with the real Azure adapter on the five real briefs (docs/briefs/real-briefs.md).
Writes results.md and spec-<brief>.json next to this file. Keys are read from the repo .env and never printed.
Run from backend/:  python ../docs/bakeoff/brief-extract/run_real.py"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend" / "tests")]
env_file = next((p for p in (ROOT / ".env", ROOT.parents[2] / ".env") if p.exists()), None)
for line in (env_file.read_text(encoding="utf-8").splitlines() if env_file else []):
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        if k.strip().startswith("AZURE_OPENAI_"):
            os.environ[k.strip()] = v.strip().strip('"').strip("'")
os.environ["CLIPPING_USE_FAKES"] = "0"

import test_30_brief_spec as T   # noqa: E402  (the MUST lists)
from clipping import brief_spec   # noqa: E402
from clipping.adapters import vendors   # noqa: E402

adapter = vendors.AzureOpenAILLM({"config_json": {"api_version": os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")}})
assert not adapter.health()["missing_env"], "Azure keys missing"

rows, lines = [], []
ONLY = os.environ.get("ONLY")
for name, text in T.BRIEFS.items():
    if ONLY and ONLY.lower() not in name.lower():
        continue
    try:
        raw = adapter.complete_json("brief_spec", {"brief_text": text, "catalog": brief_spec.llm_catalog()})
    except Exception as e:   # the failure is the result: report it, never hide it
        lines.append(f"## {name}\nThe real call failed: {type(e).__name__}: {str(e)[:200]}\n")
        continue
    returned = len((raw or {}).get("items") or [])
    accepted = brief_spec.validate_llm(raw, text)
    spec = brief_spec.build_spec(text, raw)
    (HERE / f"spec-{name.split(' ')[0].lower()}.json").write_text(json.dumps({"spec": spec, "raw_items": returned, "raw": raw}, ensure_ascii=False, indent=1), encoding="utf-8")
    checks = [(layer, what, T._safe(check, spec)) for layer, what, check in T.MUST[name]]
    missed = [f"{layer}: {what}" for layer, what, ok in checks if not ok]
    brief = brief_spec._Text(text)
    invented = [i for i in spec["items"] if i["status"] != "unclear" and not brief_spec.is_verbatim(i["source_quote"], brief.norm)]
    conflicts = [i for i in spec["items"] if i["status"] == "conflict"]
    only_llm = [i for i in spec["items"] if i["origin"] == "llm"]
    rows.append((name, len(checks) - len(missed), len(checks), len(invented), returned, len(accepted), len(conflicts)))
    lines.append(f"## {name}\n- Must-extract found: {len(checks) - len(missed)}/{len(checks)}\n- Invented values (quote not in brief): {len(invented)}\n"
                 f"- LLM items returned {returned}, accepted {len(accepted)}, dropped {returned - len(accepted)} (no verbatim quote, or number not in its quote)\n"
                 f"- Conflicts between rules and LLM: {len(conflicts)}\n- Items only the LLM found: {len(only_llm)}\n")
    by_layer: dict[str, list[bool]] = {}
    for layer, _what, ok in checks:
        by_layer.setdefault(layer, []).append(ok)
    lines.append("Per layer: " + ", ".join(f"{l} {sum(v)}/{len(v)}" for l, v in by_layer.items()) + "\n")
    lines.append("Missed:\n" + ("\n".join(f"- {m}" for m in missed) or "- none") + "\n")
    for c in conflicts:
        lines.append(f"Conflict {c['key']}: rule {json.dumps(c['value'], ensure_ascii=False)[:80]} vs {json.dumps(c['alternatives'], ensure_ascii=False)[:160]}\n")
    unclear = [i["key"] for i in spec["items"] if i["status"] == "unclear"]
    lines.append("Reported unclear: " + (", ".join(unclear) or "none") + "\n")

head = ["# Brief extractor: one real run (Azure gpt-5-mini through the adapter, strict quote validation)\n",
        "| Brief | Must-extract found | Invented | LLM items (returned / accepted) | Conflicts |", "|---|---|---|---|---|"]
head += [f"| {n} | {f}/{t} | {inv} | {r} / {a} | {c} |" for n, f, t, inv, r, a, c in rows]
(HERE / (f"results-{ONLY.lower()}.md" if ONLY else "results.md")).write_text("\n".join(head) + "\n\n" + "\n".join(lines), encoding="utf-8")
print("\n".join(head))
