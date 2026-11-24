# Shot selection bake-off (status 2026-11-24)

## Why 26 candidate moments gave one clip (confirmed from the code, not from the owner's database)
`pipeline._schedule_candidates` queues only candidates with `rank <= room`. `room` = weekly cap (10 per campaign) minus clips made, then
`_portfolio_room`: when the owner has another approved non-practice campaign, one campaign may take at most 40 % of the week's clips,
and `allowed = (0.4 * week - made) // 0.6`, never below 1 when `made == 0`. With a week that has 0 to 1 clips in total, `allowed = 0`, lifted to **1**.
So a second approved campaign plus a quiet week gives exactly one cut clip and "N moments were kept but not cut" (alert `weekly_clip_cap`).
This is deliberate ("one clip to start", `tests/test_8_phase_c_complete.py:171`), so I did not change it. Decision for the owner: apply the share only after
the week has some history (for example 10 clips), or raise the bootstrap from 1 to 3. The weekly cap of 10 alone would not explain one clip.

## What was built
- `moments_scout.py` + engine `scout` (LLM reads the episode as numbered sentences, answers with sentence numbers, code computes times and edges), fake adapter, tests (`tests/test_12c_scout.py`).
- The LLM now also rates the scout's own best proposals (`SCOUT_RATE`), so heuristic features cannot hide them.
- Evaluation tools in `tools/shots_eval/` (`prep.py` ground truth, `run_engine.py`, `score.py`, `judge.py`).

## Evidence so far
Smoke test with the real providers on two small local samples (8-minute Arabic interview, 10-minute English podcast), base route vs route + scout:
blind pairwise judge (both orders), scout wins 0.70 of 5 judged pairs (1 identical). Far too small to claim anything.

## Blocked: YouTube ground truth
The ground-truth set (heatmaps, the channels' own Shorts, view counts) needs YouTube. After the first prep burst YouTube answered every anonymous request with
"Sign in to confirm you're not a bot". I did not work around it (no cookies, no client switching). A poller retries politely every 4 minutes (`tools/shots_eval/auto_prep.py`).
