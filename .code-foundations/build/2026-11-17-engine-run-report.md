# Engine run report (Phase 4, moment engine)

Run on 2026-11-18 with the real providers, through the system's own code (`CLIPPING_USE_FAKES=0`): Deepgram Nova-3 for the transcript, Azure OpenAI gpt-5-mini through the adapter registry, local FFmpeg for the signals. Throwaway database and storage under `L:\Projects\Clipping\.data\engine-run` (the dev `.data/pg` was not touched). Script: `.data/engine-run/run_engine.py`; raw results: `.data/engine-run/results.json`. Keys were read from `.env` by the system's own code and never printed. The run stops after the moments job: nothing was rendered or hooked.

Both videos ran as practice campaigns with the **podcast** profile (30 to 60 s, target 3 clips for videos of this length). Route: `own`, `topic`, `vendor`. The vendor engine stayed silent (no Vizard or Klap key, fakes off), as designed.

## Timings, LLM calls and cost

| | Arabic interview (485.8 s) | English podcast, first 10 min (600 s) |
|---|---|---|
| Transcribe (Deepgram) | 23.1 s | 23.3 s |
| Signals | 1.3 s | 16.4 s |
| Moments (engine incl. 3 LLM calls) | 67.5 s | 50.6 s |
| LLM calls in the moments job | 3 (1 rating, 2 comparison orders) | 3 |
| Cost recorded on the moments job | $0.0185 | $0.0183 |
| Candidates stored | 3 kept, 6 filtered | 3 kept, 4 filtered |
| Engines that proposed (stored) | own 8, topic 1 | own 7 |
| Alerts | none | none |

The LLM calls are bounded (rate the top 12 once, then compare the top 8 in both orders, at most 12 pairs per order), so cost grows only with the text of those 12 windows and not with video length: about $0.02 per source here against the $0.10 per hour target (the adapter's estimate over-counts reasoning tokens, so the real bill is at or below it).

## Arabic interview: kept

| Rank | Cut | Score | Why (engine's reasons, first two) | Hook (pattern) |
|---|---|---|---|---|
| 1 | 16.5 to 66.5 s | 0.85 | A specific, moving personal story ending on a painful point; won its head-to-head comparisons | none |
| 2 | 160.4 to 204.1 s | 0.68 | A strong claim that the state supplies the torture tools; mixed head-to-head results | none |
| 3 | 92.3 to 143.6 s | 0.54 | Reveals a hidden legal tactic that affects proving torture | "لا أحد يخبرك بهذا: ..." (nobody_tells_you) |

Filtered (shown under "Filtered" in Review, rescuable): layer 2, below this episode's cut-off of 0.48: 54.0 to 100.0 s (0.47, a near miss), 412.4 to 462.4 s (0.38), 371.4 to 425.6 s (0.32), 233.9 to 277.6 s (0.31), 330.4 to 378.0 s (0.28, from the topic engine); layer 3: 194.7 to 241.6 s (0.50, ranked 4th, "this type keeps the top 3").

## English podcast: kept

| Rank | Cut | Score | Why | Hook (pattern) |
|---|---|---|---|---|
| 1 | 555.4 to 596.4 s | 0.89 | Clear, useful insight about the two-minute view metric; won its comparisons | "Nobody tells you this: ..." (nobody_tells_you) |
| 2 | 0.0 to 41.3 s | 0.87 | Tense true-crime opening with an immediate emotional hook; won its comparisons | none |
| 3 | 326.9 to 377.4 s | 0.36 | Surprising industry detail about Netflix acquiring adaptation rights; lost its comparisons | "I got this wrong for years: ..." (mistake_confession) |

Filtered, all layer 2 against a cut-off of 0.36: 489.7 to 544.8 s (0.35), 424.2 to 472.9 s (0.34), 57.8 to 112.5 s (0.31), 114.3 to 158.9 s (0.28; the AI called it funny but no laughter was heard, so it was discounted, rule R5 and the cross-modal check).

The third clip of each video sits at or just above its cut-off and was kept to reach the profile's minimum of three; the reason list says so.

## What this shows and what it does not

- Works end to end with the real providers: valid JSON from gpt-5-mini every time, no content-filter refusals, no retries, no alerts.
- Picks agree broadly with the earlier pilot picks (the Arabic opening story, the government-claim passage, the Netflix metric and acquisition passages), now with sentence-snapped cuts inside 30 to 60 s, measured subscores, head-to-head ordering and filtered alternatives with reasons.
- Not measured here: quality against owner approvals (no labels exist yet; learning starts at 30 labels), the hype and pilot engines on a real stream (the podcast route does not use them), the vendor engine (no key), and videos longer than 10 minutes (long-episode chunking is covered by the 45-minute synthetic test, not a real run).
- Several kept moments have no hook because the model's hook was missing or not paid off inside the clip (rule R4); the hooks stage still writes its own hooks for them.
