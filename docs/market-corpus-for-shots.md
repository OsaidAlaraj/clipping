# Market corpus for the best-shots session (short, 2026-11-24)

Evidence quality: lengths and cut rates are **measured** (39 Shorts, `docs/market-style-study-2.md`); title/outlier patterns are from 10 curated cells in
`.data/market/youtube_curated.jsonl` (small samples). First-5-second text, clip offset inside the source and per-campaign accepted clips are **not
collected yet** (see `docs/market-corpus.md`, "What did not work"). Do not tune shot selection on what is not measured.

| Cell | Typical length (median, range) | Hard cuts per 10 s | Cut in first 3 s |
|---|---|---|---|
| Business EN | 41 s (32 to 69) | 0 | 50% |
| Business AR | 82 s (77 to 91) | 0.1 | 20% |
| Gaming EN | 25 s (17 to 28) | 2.1 | 86% |
| Gaming AR | 42 s (30 to 56) | 2.6 | 86% |
| Podcast EN | 28 s (21 to 43) | 1.5 | 43% |
| Podcast AR | 25 s (23 to 57) | 0 | 20% |
| All | 35.5 s (25 to 64) | 0.8 | 54% |

What the first seconds contain (measured): a title card in the first second; no clip opened with more than 3 cuts in 3 s; face width 0.17 to 0.36 of the frame.
What the winning titles lead with (corpus): a person or event name, a number, a curiosity question, or a quote from the speaker; no POV/Nobody tags.
Hook formats offered per niche live in `backend/clipping/assets/hook-lexicon.json`; every returned hook carries its `format`, so a cold-open teaser can use
the same line as the title.
