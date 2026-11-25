"""Blind round-robin of the nine caption looks on the same real clips, judged from the contact sheets (6 frames each) by the project's
vision-capable LLM: every pair, both orders, labels hidden. Writes results.md (win rate per look per clip, overall rank).
Run from backend/ after make_sheets2.py:  ..\\.venv\\Scripts\\python.exe ..\\docs\\bakeoff\\templates-2\\judge_templates.py
Limits: stills show colour, size, placement and legibility, not motion; the judge is one model; two clips only (English, Arabic)."""
from __future__ import annotations

import base64
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "backend"))
from clipping import config  # noqa: E402
from clipping.adapters import caption_render, vendors  # noqa: E402
from clipping.adapters.base import check_response, http_client  # noqa: E402

config.load_dotenv(Path(r"L:\Projects\Clipping\.env"))
PROMPT = ("You are a heavy TikTok and Reels viewer who also edits short videos. Image A and image B are contact sheets of the SAME clip (six frames "
          "each) with two different caption looks. Pick the look you would rather watch on a phone: easy to read at a glance, energetic without "
          "being messy, polished like the best short-form editors, not covering the speaker's face, text inside the safe area. Judge only the "
          "caption look, not the video. Reply with JSON only: {\"winner\":\"A\"|\"B\",\"reason\":string}.")


def ask(a: Path, b: Path) -> str:
    for attempt in range(4):   # a slow vision call times out now and then: retry, never lose the run
        try:
            return _ask(a, b)
        except Exception:
            if attempt == 3:
                raise


def _ask(a: Path, b: Path) -> str:
    endpoint = config.env("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    deployment = config.env("AZURE_OPENAI_VISION_DEPLOYMENT") or config.env("AZURE_OPENAI_DEPLOYMENT")
    img = lambda p: {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()}}  # noqa: E731
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": PROMPT}, {"type": "text", "text": "Image A:"}, img(a),
                                                       {"type": "text", "text": "Image B:"}, img(b)]}], "response_format": {"type": "json_object"}}
    if not vendors._reasoning_model(deployment):
        body["temperature"] = 0
    with http_client(300) as client:
        r = client.post(f"{endpoint}/openai/deployments/{deployment}/chat/completions", params={"api-version": "2024-10-21"},
                        headers={"api-key": config.env("AZURE_OPENAI_API_KEY", "")}, json=body)
    return "A" if str(json.loads(check_response(r)["choices"][0]["message"]["content"]).get("winner", "")).upper().startswith("A") else "B"


def main() -> None:
    names = list(caption_render.STYLES)
    score: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    games: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for tag in ("en", "ar"):
        sheet = {n: HERE / f"sheet_{tag}_{n}.jpg" for n in names if (HERE / f"sheet_{tag}_{n}.jpg").exists()}
        for x, y in itertools.combinations(sheet, 2):
            for first, second in ((x, y), (y, x)):
                winner = first if ask(sheet[first], sheet[second]) == "A" else second
                score[tag][winner] += 1
                games[tag][x] += 1
                games[tag][y] += 1
            print(tag, x, y, flush=True)
    lines = ["# Caption looks, blind round-robin (stills of the same clip, both orders)", "",
             "Win rate = games won of games played (every pair twice, A/B swapped). Judge: the project's vision LLM, one fixed instruction. "
             "Stills do not show motion, so this ranks colour, size, placement and legibility only.", ""]
    total: dict[str, list[float]] = defaultdict(lambda: [0, 0])
    for tag, label in (("en", "English talking head"), ("ar", "Arabic interview")):
        lines += [f"## {label}", "", "| Look | Wins | Games | Win rate |", "|---|---|---|---|"]
        for n in sorted(score[tag] | {k: 0 for k in games[tag]}, key=lambda n: -score[tag][n]):
            lines.append(f"| {n} ({caption_render.STYLES[n]['label']}) | {score[tag][n]:.0f} | {games[tag][n]} | {score[tag][n] / max(games[tag][n], 1) * 100:.0f}% |")
            total[n][0] += score[tag][n]
            total[n][1] += games[tag][n]
        lines.append("")
    lines += ["## Both clips", "", "| Look | Win rate |", "|---|---|"]
    lines += [f"| {n} | {w / max(g, 1) * 100:.0f}% |" for n, (w, g) in sorted(total.items(), key=lambda kv: -kv[1][0] / max(kv[1][1], 1))]
    (HERE / "results.md").write_text("\n".join(lines), encoding="utf-8")
    (HERE / "results.json").write_text(json.dumps({"score": score, "games": games}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
