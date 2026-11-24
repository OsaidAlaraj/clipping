# Market corpus: method, results and honest limits (2026-11-24)

Store: `L:\Projects\Clipping\.data\market\` (not in git). Scripts: `docs/market/build_corpus.py` (collect) and `docs/market/analyse.py` (summary table).
Every row has `collected` and `expires` (30 days). Anonymous yt-dlp only, no API key, no video download, slow on purpose.

## What was collected
- `youtube_curated.jsonl`: for 10 niche/language cells, the newest 30 Shorts of 2 to 6 known channels per cell, each scored as views / that channel's median
  (the outlier score, so a winner is separated from a big channel); top 12 per cell kept. Cells: gaming en/ar, podcast en/ar, business en/ar, sports en,
  comedy en, movies en, general en. **Missing: Arabic sports, comedy, movies, general** (no channel list I trusted; add handles to `CURATED`).
- `youtube.jsonl`: the first attempt, discovery by keyword search. **Discarded as evidence**: it returned kids, DIY and music channels (the word "shorts"
  matched them). Kept only as proof of what did not work.
- Hook-style numbers below come from titles only.

## What did not work (and why)
| Item | Result |
|---|---|
| First 5 seconds of spoken text from auto-captions | **0 of about 100 videos** returned captions through yt-dlp today; the column is empty. Needs a different route (owner-approved download + local Whisper, or the YouTube Data API key) |
| Duration per video | not in the flat channel listing; lengths are taken from `docs/market-style-study-2.md` (39 clips measured at 720p to 1080p) |
| Offset of a clip inside its podcast source | not findable from public metadata; not collected |
| Clipster and Content Rewards leaderboards | not read: they need the owner's signed-in pages and a per-campaign walk; left for a session that has the owner at the keyboard |
| TikTok Creative Center | public page redirects to a login wall; not read |
| Instagram | no usable public data; skipped as agreed |

## What the titles say (title style, n = 12 per cell, outlier = views / channel median)
Generated table: run `python docs/market/analyse.py`. Findings that held in the data:
- **No winner title uses POV, Nobody or Me when** (0% in every cell). The winners are plain statements, a number, a name or a question (8 to 33% have a `?`).
  This agrees with the 2025 reports that the tag trend faded ([research](viral-templates-research.md)); titles are not on-screen hooks, so it is a hint, not proof.
- ALL CAPS shouting appears in 100% of the English cells and about 0 to 17% of Arabic ones (Arabic has no capitals, so that is expected).
- Biggest outliers are event or person names (a concert, an award, an incident) and a curiosity line ("What did the 49ers show?"), not generic advice.
- Arabic business/podcast winners are short quotes from the speaker (a strong line said in the clip), which supports hooks taken from the clip's own words.
- Small samples (12 per cell, 2 to 6 channels): treat as direction only.

## How the corpus feeds the work
- Hook lexicon: tag formats are restricted to light niches and lower weight; curiosity gap, number, contradiction, outcome-first stay on top. The judge bake-off (`docs/bakeoff/hooks-viral/`) is the check.
- Template choice: `AUTO_BY_NICHE` now covers comedy, sports, movies, general (blind stills round, `docs/bakeoff/templates-2/results.md`).
- Trends carry an `expires`; re-run `build_corpus.py` monthly.
