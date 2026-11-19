# Benchmark and training plan (moment choice, hooks, crop gate)

Status: plan only, nothing downloaded or built. Follows `docs/quality-diagnosis.md` sections 7 and 8 and the download decision in `docs/decisions.md`.
Owner choices recorded 2026-11-19: up to 1000 videos may be downloaded for internal measurement; LLM calls are allowed (Azure for Students credit, about $0.15 spent this month; check the credit balance in the Azure portal); everything runs anonymously and slowly.

## 0. Honest target

"Perfect" is not reachable and I will not promise it: the labels are proxies for what viewers reward. A realistic, measurable target, fixed **before** tuning:

- **Moment choice:** hit@3 against the crowd peaks at least 15 points above today's engine and above the open baseline model, on a **test set we never touch while tuning**.
- **Crop and cuts:** at least 95 % of produced clips pass the section 7 gate without repair (measured, not judged).
- **Hooks:** the hook pattern order beats a random order on the held-out shorts (measured by views relative to the channel's own median).

If a step does not beat the baseline on the held-out set, it does not ship. The report states the numbers either way.

## 1. Data: what gives labels, with no big downloads where possible

| Data | Source | Download needed | Count |
|---|---|---|---|
| **Replay heatmaps** (inner timing of what viewers rewatch) | YouTube metadata via yt-dlp | **No video** (metadata only) | up to 1000 videos with 50k+ views |
| **Audio for those videos** (features) | yt-dlp audio-only, 64 kbps | about 10 to 25 MB each | the same 1000 |
| **Twitch clips against VOD offsets** | Official Helix API (app token), clips list per VOD | metadata only; VOD audio only for 60 VODs | 60 VODs, about 600 clips |
| **Finished shorts with views** (hook, length, title, caption patterns) | YouTube Shorts, TikTok, Reels, Facebook: public metadata | **No video** | thousands of rows, as far as anonymous access allows |
| **Low-res video** (360p, for layout and crop checks) | yt-dlp | about 100 MB per hour | 150 videos only |

Sizes: audio-only for 1000 videos is about 15 to 25 GB over the whole run; deleted after the features are extracted, so the disk peak stays under about 2 GB (a queue of at most 5 files). Metadata rows are small.

Mix (1000 YouTube videos): 40 % gaming (commentary, IRL, game playthrough with face-cam), 30 % podcasts and interviews (English 70 %, Arabic 30 %), 20 % business and talks, 10 % other. At most 5 videos per channel so the model learns content, not one channel.

Platform notes: YouTube and Twitch are the core. TikTok, Instagram and Facebook run only anonymously and only as extra metadata; if one refuses, we stop on it (no workaround, no accounts).

## 2. Pace and safety (the owner's guardrails)

- At most **2 downloads a minute**, random 20 to 40 s spacing, a hard daily cap of 350 videos, runs at night. 1000 videos take about **3 nights**.
- Anonymous only: no cookies, no logins, no tokens from the owner's accounts. If YouTube answers "sign in to confirm you are not a bot", the run stops and reports; no cookie trick.
- **Bulk waits until the owner confirms their social accounts exist.** Before that: the first batch of at most 10 videos (4 YouTube, 2 Twitch, 1 per other platform as an access probe), list approved first.
- Manifest `.data/benchmark/manifest.json`: URL, platform, date, size, hash, status (`queued`, `downloaded`, `features_done`, `deleted`). A video is deleted as soon as its features are saved. Nothing is posted, shared or re-uploaded.

## 3. What we extract per video (then the video is deleted)

Per candidate window (30 to 60 s windows, stride 5 s) so the model scores every possible clip:

1. **Existing features** (the nine in `profiles.FEATURES`: hook, standalone, payoff, emotion, value, energy, peaks, laughter, length fit).
2. **Audio:** loudness curve, speech rate, pause structure, laughter and applause probability, music share, speaker changes.
3. **Text:** transcript statistics, question density, named entities, sentiment arc, first-sentence pattern, and a small sentence-embedding vector of the window.
4. **Section 7 metrics** (cut quality, speech density) so we learn how they relate to the crowd signal.
5. **LLM ratings** (Azure gpt-5-mini, the same prompt the engine uses): hook, payoff, standalone, emotion, 1 to 10, plus the reason. Costed below.

Feature files are small Parquet or JSON, kept; the raw media is deleted.

## 4. Compute budget (honest)

Measured on this laptop: local Whisper large-v3-turbo transcribed a 5-min slice in 26 s (about 11.5x real time). 1000 videos at an average of about 40 min is about 670 hours of audio: **about 58 hours of GPU time at that setting, which is too long**. So two tiers:

| Tier | Videos | Transcription | LLM ratings | Time |
|---|---|---|---|---|
| **A: full pipeline** | 200 | large-v3-turbo (best quality, same as the app) | yes, all windows over the pre-filter | about 12 hours of GPU |
| **B: scale tier** | 800 | Whisper **small** int8, about 40 to 60x real time (an estimate; I will measure it), or no text for audio-heavy features only | LLM on the top 20 windows per video only | about 10 to 14 hours of GPU |

Total GPU time about 1.5 days of machine time, overnight and in the background. **LLM cost:** about 200 videos × 120 windows pre-filtered to 40 × about 1.2k tokens plus 800 × 20 windows × 1.2k tokens, roughly 30M tokens in total. At gpt-5-mini prices that is on the order of **$10 to $25** (I will compute it exactly from the price in the `adapters` table before the run and stop at a budget set by the owner). Covered by the student credit if it is still valid; the app's monthly cap ($20) does not apply to the benchmark script, which has its own budget.

## 5. Training: smart, and hard to fool ourselves

**Labels per window:** from YouTube, the mean replay value of the window and whether it overlaps a top-10 % replay peak; from Twitch, whether a viewer clip starts inside the window, weighted by the clip's views. The replay signal is **removed from the engine's inputs** during the benchmark (otherwise the test is circular).

**Models, in order of ambition (stop when the held-out score stops improving):**

1. **Today's engine** with its default weights: the baseline to beat.
2. **Weight search** on the nine existing features per profile (coordinate search, random restarts). Cheap, safe, shippable as profile defaults.
3. **Learning to rank:** gradient boosted trees (LightGBM `lambdarank`, about 5 MB install) over all windows of one video, using every feature above. Per-profile models (gaming, podcast, business) plus a general one. Output scores are used as an extra feature of the engine, with the existing safety rules and dedupe kept.
4. **Hook model:** from the public shorts metadata, relate the first sentence's pattern, length and the title style to views divided by the channel's median views; re-order `hook_patterns` by that evidence.
5. **Calibration:** isotonic calibration so a score of 0.8 really means a high chance of being a replay peak; the cut-off per episode is then set from the calibrated value.

**Protection against overfitting:**
- Split **by channel**, never by window: 60 % train, 20 % validation (for choosing settings), 20 % **test, frozen and locked before any tuning**; the test set is evaluated once per model family at the end.
- Nested cross-validation for the small tiers; confidence intervals by bootstrap over videos; report per niche and per language.
- Each tuning run records its settings and score in `.data/benchmark/runs.jsonl`, so the number of tries is visible.
- Ship rule: a change goes into the engine only if it beats the baseline on validation **and** test by more than the bootstrap interval.

**Open baseline:** Moment-DETR or UniVTG as a reference (section 8.3 of the diagnosis; checkpoint, licence and GPU fit must be verified first). It is a reference number, not a dependency.

## 6. Crop and cut quality (no crowd labels; geometric standards)

The crop is not "trained" on views. It is built to the section 7 standards and **tested on the 150 low-res videos** of the set:

- Face detector (OpenCV YuNet) with smoothing and hold time, speaker choice by diarization, split layout for gaming. Benchmark: percentage of frames with the subject inside the crop, crop jitter, number of moves vs. speaker changes, on 150 videos and about 600 clips. Compared against today's skin-mask crop (expected to fail on gaming).
- The gate (section 7.5) runs on every benchmark clip; its pass rate is a headline number.

## 7. Steps, order and honest sizes

| Step | Work | Size |
|---|---|---|
| 0 | Install yt-dlp (about 15 MB), LightGBM; register a Twitch app (owner, in `.env`, never pasted in chat); anonymous probe of each platform with one public video; model smoke tests. Verify the open baseline checkpoint and licence. | 3 to 4 h |
| 1 | Manifest, rate-limited downloader with delete-after-extract, resume, daily cap. | 3 to 4 h |
| 2 | First batch of up to 10 videos (list approved first), end to end through extraction. Fix what breaks. | 3 h |
| 3 | Feature extraction per window (tier A and B), label builders, the frozen split. | 8 to 10 h |
| 4 | Bulk download (3 nights, after the owner confirms the accounts) and extraction runs in the background. | about 3 nights of machine time; 1 to 2 h of attention |
| 5 | Evaluation harness: metrics, bootstrap intervals, baselines, report. | 6 to 8 h |
| 6 | Models 2 to 5 above; ablations (which feature groups matter). | 2 to 3 days |
| 7 | Crop detector and gate on the 150-video subset (section 6). | 2 to 3 days |
| 8 | Ship what beats the baseline: new profile defaults, the ranker as a feature, hook order, the gate stage. Re-test on the locked test set once. | 1 to 2 days |

About **8 to 10 working days of build** in total, with most elapsed time as background compute. The order that gives visible results soonest: language check, sentence-end snap and the free gate checks (1 to 1.5 days, independent of the benchmark), then steps 0 to 3, then 7, then 5 to 6.

## 8. What this cannot tell us

- **Replays are not virality on TikTok and Reels.** They reward the first 1 to 3 seconds, completion and rewatch of a short, plus account history, trends and posting time. A moment that wins a YouTube replay vote may still make a mediocre short.
- Videos with heatmaps are popular videos; small channels and Arabic content are under-represented, so the Arabic result will be weaker and must be reported separately.
- Hooks are learned from public metadata of finished shorts, which mixes in thumbnails, channel size and luck; treat the hook model as a mild prior.
- The market moves; re-run every few months.
- The final truth is the view data of our own posts (phase F). The benchmark exists so the first real posts are not wasted.

## 9. What I need from the owner

1. **Confirm the Azure for Students credit is still active** (portal, Cost Management, Credits), or set a budget for the LLM part.
2. **Tell me when the social accounts exist**; the bulk downloads start after that.
3. **Create a free Twitch developer application** (client id and secret into `.env`; do not paste them in chat).
4. **Approve the first batch list** when I send it (up to 10 videos, about 6 minutes of downloading), and the installs: yt-dlp, LightGBM, OpenCV plus the YuNet model (about 40 MB in total, models under `.data/models`).
