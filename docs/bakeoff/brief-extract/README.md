# Brief extractor: real-model bake-off (Azure gpt-5-mini through the adapter registry)

Test set: the five real briefs in `docs/briefs/real-briefs.md`, scored against their "Must extract" lists (52 checks, per layer).
Scoring code: `backend/tests/test_30_brief_spec.py` (`MUST`). Run: `python ../docs/bakeoff/brief-extract/run_real.py` from `backend/` (`ONLY=Topps` for one brief).
"Invented" = a stated item whose source quote is not in the brief. Every LLM item must carry a verbatim quote and a number found in that quote, or it is dropped.

| Run | Result | What happened |
|---|---|---|
| 1 (first code) | 49/52, invented 0 | Topps missed 3: the model's quotes were shortened with "..." or its enum answers were sentences, so the validator dropped them (the validator was right to be strict, the prompt and the quote check were too narrow). |
| 2 (after fixes: ellipsis quotes, typographic quotes, enum values listed to the model, lower-case enums) | **51/52, invented 0** (`results.md`) | Only miss: Topps content-bank whitelist. The model returned no `sources.whitelist` item in that call (the same brief gave them in run 1 and in a diagnostic call): LLM variance, not a validator drop. |
| 3 (Topps only, after the whitelist prompt hint + rule for linked source videos) | **13/13, invented 0** (`results-topps.md`) | |

Other facts from the runs: 0 invented values in all 15 brief-runs; the model gave 13 to 44 items per brief and the validator dropped 0 to 70 % of them
(dropped = no verbatim quote or number not in its quote; early runs dropped the most); where rules and model disagreed the item became `conflict`
with both readings (for example the model answered `["tiktok","instagram"]` for a platform line that lists YouTube too: the rule reading won and the model's was kept as the alternative);
fields the brief does not state were reported `unclear` (CSGOBIG payout model and rate; Reach and Topps language, length, budget).
Known limit: a content bank given as names only (no links) depends on the model, so it can be missed in a run; the review screen shows nothing there and the owner adds the list by hand or pastes the page with its links (links become the whitelist by rule).
Run 1 detail is in `run1-before-fixes.md`; per-brief specs with the raw model output are in `spec-*.json`.
