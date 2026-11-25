# Shot selection: research (2026-11-24)

Question: how do others choose the best moments of a long video, and what should ClipMind adopt? Facts below were read on the dates shown (GitHub API on 2026-11-24; web pages via search). Where a source could not be verified it says so.

## 1. Open-source projects (stars, last push, licence)

| Project | Stars | Last push | Licence | How it picks moments | Verdict for ClipMind |
|---|---|---|---|---|---|
| [FujiwaraChoki/supoclip](https://github.com/FujiwaraChoki/supoclip) | 4145 | 2026-11-24 | NOASSERTION (check) | LLM "lead editor" prompt over a numbered **sentence/clause listing** (`#id time +dur speaker: text`, silence markers); the model answers with unit ids only, code computes times; edge rules: start on a sentence opener (never he/they/that/so/but), end where a sentence ends | **Adopt the idea** (id protocol + edge rules). Busiest repo; most issues are plumbing (AssemblyAI, queue), not selection |
| [Anil-matcha/AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) (a.k.a. SamurAIGPT) | 6524 | 2026-11-21 | MIT | One GPT call over the transcript asking for the most engaging 2 min; no signals | Too simple; ClipMind already does more. Issues: "dependency issues", "is this abandoned?" |
| [jipraks/yt-short-clipper](https://github.com/jipraks/yt-short-clipper) | 1109 | 2026-09-20 | MIT | LLM over the transcript; prompt demands EXACTLY N clips (forces filler), prefers conflict/confession/punchline/complete arc, "search the whole transcript, spread the picks" | Useful prompt wording (arc, spread). The forced count is the opposite of what ClipMind wants (cut-off with floor) |
| [modelscope/FunClip](https://github.com/modelscope/FunClip) | 6628 | 2026-11-01 | MIT | FunASR transcript + LLM clip suggestion + speaker/hotword search | Interesting for Mandarin; for Arabic and English no gain over Deepgram + gpt-5-mini |
| [ClipsAI/clipsai](https://github.com/ClipsAI/clipsai) | 646 | **2024-01-17** | MIT | TextTiling-style topic segmentation on embeddings, then resize | Stale for 2+ years; ClipMind already has topic segmentation. Skip |
| [RayVentura/ShortGPT](https://github.com/RayVentura/ShortGPT) | 8201 | 2025-02-10 | MIT | Generates new videos from scripts; not a clipper | Not relevant |
| [Aseiel/VideoHighlighter](https://github.com/Aseiel/VideoHighlighter) | 504 | 2026-11-23 | AGPL-3.0 | Scores scene, motion, audio, objects, transcript and shows a per-signal timeline "why" | Confirms the "show why" idea; vision signals are heavy for a GTX 1660 Ti |
| [AnIntellectualBeing/GTA-Auto-Clipper](https://github.com/AnIntellectualBeing/GTA-Auto-Clipper) | 192 | 2026-11-24 | MIT | Ranks stream chunks by live chat reactions, falls back to audio and speech | Same idea as ClipMind's `chat` and `hype` signals (already there) |
| [line/lighthouse](https://github.com/line/lighthouse) | 275 | 2026-11-01 | Apache-2.0 | Query-based video moment retrieval and highlight detection (QD-DETR, CLIP/SlowFast features) | Needs a text query and heavy visual features; not a fit for talking footage |
| [Breakthrough/PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | 5635 | 2026-11-24 | BSD-3 | Shot boundaries | Useful only for visual cut points; talking-head shots rarely have them. Not for selection |
| [soCzech/TransNetV2](https://github.com/soCzech/TransNetV2) | 1083 | 2023-12-04 | MIT | Neural shot boundaries | Same; stale |
| [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) | 6074 | 2026-11-24 | Unlicense | Cuts silence and motion-less parts | Editing, not selection; ClipMind has `dead_air_out` |
| [linto-ai/whisper-timestamped](https://github.com/linto-ai/whisper-timestamped) | 2860 | 2026-09-28 | AGPL-3.0 | Word timestamps | ClipMind has Deepgram word times already |

Small or new repos (twitch highlight detectors, "laughter detection", clipforge variants) have 0 to 5 stars: no evidence of quality, none is worth adopting.

**What users say (issues).** ClipsAI: "Is this project abandoned?", dependency and ffmpeg errors. supoclip: queue stuck, transcription provider API changes, "Whisper support?". Nobody reports on selection quality in issues; they report plumbing. Selection quality is judged in review articles of the commercial tools instead (below).

## 2. Commercial tools: what they score

- **OpusClip**: a 0 to 99 score from four parts: Hook, Flow ("progresses logically to a satisfying conclusion"), Value (value and emotional resonance), Trend. Weights are not published; its own guidance says to use it to sort clips, not to pick winners ([help centre](https://help.opus.pro/docs/article/virality-score)). Quso uses different categories; WayinVideo uses Hook, Story, Value, Engagement, Reach ([help](https://intercom.help/wayinvideo/en/articles/16209720-what-does-clip-quality-or-viral-score-mean)).
- **Klap**: 0 to 100 "based on what performs on TikTok, Reels, Shorts"; a third-party comparison says detection relies heavily on speech. **Vizard** v2 advertises "full context clips" and a per-dimension breakdown ([Vizard v2](https://vizard.ai/blog/meet-vizard-model-v2-full-context-clips-that-the-algorithm-actually-rewards)). **Submagic, 2short, Munch**: no published scoring found; treat as unverified.
- A secondary blog says visual analysis only helps framing; highlight choice is almost entirely transcript-driven (unverified, vendor-adjacent).
- **What users complain about** (reviews, [eesel](https://www.eesel.ai/blog/opusclip-reviews), [HeyGen comparison](https://www.heygen.com/blog/opus-pro-alternatives), G2): clips that cut a joke before the punchline, end mid-sentence, miss the real hook; "two or three usable clips out of twenty" on a 42-minute podcast. I found no Reddit thread directly (search did not surface one); these are review-site summaries and some authors sell competitors.
- **What editors say they do by hand** ([Descript](https://www.descript.com/blog/article/how-to-choose-clips-for-social-media), [Soundstripe](https://www.soundstripe.com/blogs/how-to-clip-a-podcast), Kapwing, vidIQ): the hook lands in the first 2 to 3 seconds; the clip must make sense without the episode (reject anything that depends on a name or a callback; move the in-point later); the payoff comes before the end; start on the problem, not on "number two"; 20 to 45 s performs best; captions are mandatory. Cutting on sentence boundaries is common sense but no source measured it.

## 3. Papers

- **Rhapsody** (COLM 2025, [arXiv 2505.19429](https://arxiv.org/abs/2505.19429)): 13K podcast episodes with YouTube "most replayed" labels; best method hit rate 49.0 %; a fine-tuned model using text plus audio beats zero-shot GPT-4o and Gemini by a wide margin. Lesson: zero-shot LLM taste on a podcast is weak; audio adds signal.
- **Text Barely Beats Timing** (NeurIPS 2026, [page](https://neurips.cc/virtual/2026/159082)): on 13,008 videos a plain **position** baseline is within 0.03 mAP of a fine-tuned RoBERTa; most predictable replay structure is temporal. Lesson for our evaluation: any replay-heatmap metric must be reported next to a position baseline, or it measures "early in the video" instead of taste.
- **TF-SELECTOR / SVHighlights** (KDD 2026, [alphaXiv 2606.06926](https://www.alphaxiv.org/abs/2606.06926)): training-free, merges adjacent shots with the same meaning into segments, then asks a foundation model; sports. Lesson: segment first by meaning (ClipMind's topic engine), then judge.
- Related: Mr. HiSum (NeurIPS 2023, 31,892 videos, replay labels), YTMR500, [Video Repurposing from UGC](https://arxiv.org/abs/2412.08879) (AAAI 2025), [Minimal Clips, Maximum Salience](https://arxiv.org/abs/2512.11399).

## 4. What ClipMind already has (so it is not rebuilt)

Own sliding-window engine with sentence edges, topic engine, hype engine (loudness, laughter, reaction, chat, replay), optional vendor engine, whole-episode context, features hook / standalone / payoff / emotion / value / energy / peaks / laughter / length fit, the LLM's rating of the top 12 corroborated against signals, pairwise comparison in both orders with Bradley-Terry, dedupe by overlap and topic, hard gates, relative cut-off with floor, learning from the owner's decisions, three cut variants.

**What it does not have in practice:** by default the LLM never *reads the episode to find* moments. It only rates the 12 best windows that a heuristic scan proposed. (An LLM picker, `moments_pilot` / the `pilot` engine, exists but is not on the podcast or stream route, asks only 3 moments per 10 minutes and has the model write times, not sentence numbers.) Every LLM clipper above does the opposite (the model reads the whole numbered transcript and proposes), and the complaints about commercial tools ("misses the real hook") are about this step. The heuristic scan can only find what its word-level features rank high.

## 5. Shortlist (ranked by expected gain and cost)

1. **LLM scout engine over the whole transcript with a unit-id protocol** (built: `moments_scout.py`, engine `scout`; generalises the unused `pilot` engine: about 0.8 asks per minute instead of 3 per 10 minutes, sentence numbers instead of times, edge rules in code) (supoclip, yt-short-clipper; prompt rules from creators). The model sees numbered sentences with speaker and silence markers, answers with id ranges and a one-line reason; code computes times and enforces edges. Added as a fourth engine behind the adapter registry; its proposals go through the same gates, features, rating and pairwise layers. Cost: a few calls per episode (chunked).
2. **Boundary rules at the proposal and cut level**: start on a sentence opener (no weak openers), hook inside the first 2 s, end on a complete thought, never mid-word; check the finished window and nudge it. Mostly present as `WEAK_OPENERS`, sentence snapping; measure the share of windows that break each rule before and after.
3. **Replay-peak prior as a validation signal, with a position baseline** (Rhapsody, Text Barely Beats Timing): use YouTube's heatmap where it exists (already weight 0.1 in `hype_map`); never trust it alone.
4. **Plain-words "why this moment"** per candidate from the scout's reason plus the strongest features (the review screen shows it); VideoHighlighter and every commercial tool show a reason or a breakdown.
5. **Not adopted:** PySceneDetect/TransNet (visual cuts do not mark talking-head highlights), auto-editor (already `dead_air_out`), ClipsAI (stale, same topic segmentation), lighthouse/TF-SELECTOR (visual feature extraction, no Arabic, heavy for 6 GB), "exactly N clips" prompting (forces filler).
