# Clip quality diagnosis (2026-11-19)

Scope: the owner's three real videos and the clips made from them. Evidence only; no pipeline code was changed.
Scripts and raw outputs are in `L:\Projects\Clipping\.data\tmp\diag` (outside the repo). No `.env` value was printed.

Videos (from `source_assets`):
- **A. "ABtalks" talk, 109 min**, uploaded with language set to **Arabic**. Source of the "27 words".
- **B. "I Tried To Be An IRL Streamer… Kubz Scouts", 40 min, 720p 60 fps**, language English, profile **podcast** (the campaign niche).
- **C. Arabic interview, 8 min**, language Arabic. Works (892 words); used only as a control.

## 1. Transcript: root cause found

**The video is English, but it was uploaded as Arabic.** Deepgram was asked for Arabic and heard nothing it could use.

| Test | Result |
|---|---|
| Stored transcript of A | 27 words in 6518 s (first word at 1295 s, average confidence 0.65) |
| Whisper large-v3-turbo language detection, 9 samples of 45 s across A | English every time, probability 0.99 to 1.00 |
| Speech level in those samples | RMS 0.058 to 0.103 (healthy), about 2.4 words per second |
| Same 5-min slice (17:00 to 22:00), Deepgram nova-3, `language=ar` | HTTP 200, **0 words** |
| Same slice, Deepgram nova-3, `language=en` | **877 words** |
| Same slice, local Whisper, `en` | 872 words, 26 s on the GTX 1660 Ti |
| Same slice, local Whisper, forced `ar` | 718 words of invented Arabic (it translates and hallucinates instead of failing) |

Deepgram audio used: 10 minutes (5 min × 2).

Ruled out: audio extraction (16 kHz mono WAV, 209 MB, same code path worked on the slice), file size or length limits (5-min slice and 8-min control fine), volume, missing audio track (AAC stereo 44.1 kHz present), vendor errors (all jobs "done", no alerts).

Why nothing noticed: no check that the transcript matches the audio. A 109-minute video with 27 words is accepted as a success. The moment finder then ran on **loudness and audio peaks only** (no text), so the "moments" are noise. Side effects of the wrong language: the burned-in label was the Arabic `#إعلان` on an English video, and the topic engine named a topic "اوكي الله شاء".

Expected transcript if run correctly: about 15,000 words. Time to transcribe locally: about 5 min per hour of audio at the measured speed (est. 20 min for A).

## 2. Cropping: confirmed weak, and wrong for gaming

Code (`backend/clipping/adapters/media.py`): `face_focus` samples **3 frames at 96×54**, builds a **skin-colour mask**, and takes the **median x** of the skin pixels. `crop_filter` then uses **one fixed x for the whole clip**. No face detector, no tracking, no smoothing, no use of the speaker labels. The code comment itself says "a stand-in until a face detector is installed". OpenCV, MediaPipe and similar are **not installed** (only numpy and onnxruntime).

Measured on video B (10 clips, `face_focus` re-run): the crop is **404 px of 1280 px, 31.6 % of the picture**.
- 5 of 10 clips found no "face" and used the centre crop (x 438 to 842).
- The other 5 followed skin-coloured **game characters** (x between 0.24 and 0.55), not the streamer.
- The streamer's real facecam (top-left corner, about x 0 to 16 %) is **never in the frame**.

Frames (left = source at the clip's middle, right = our 9:16 output):
- `docs/quality-frames/c5cc_r3_face.png`: crop shows half a bedroom and a bit of the chat box; the speaking character and the facecam are cut out.
- `docs/quality-frames/c5cc_r2_centre_crop.png`: centre crop shows the officer's profile, the player character is gone.
- `docs/quality-frames/5e8e_r2_face.png` (video A): a single static speaker; the crop is acceptable here. Single-person talking heads are the one case that works.

Why B is cropped like a podcast: B has **profile `podcast`** (campaign niche). Gaming profiles exist (`012_moments.sql`: `facecam_over_game`, `follow_action`) but nothing selects them: the upload form has no profile choice and there is no auto-detection. Even `facecam_over_game` only fits the whole 16:9 picture over a blurred fill; it does not build the usual stacked layout (facecam on top, gameplay below).

Detector benchmark: **not run**, because it needs a download (`opencv-python` about 40 MB plus the YuNet ONNX model about 0.3 MB) and the owner said to ask first. The request is in section 9.

## 3. Cut boundaries (video B, 19 clips)

Good news: the cutter is decent.
- 17 of 19 start on a sentence start; 15 of 19 end on a sentence end (punctuation) with a 0.0 to 1.0 s gap to the next word.
- Lengths 38 to 58 s, 109 to 189 words each. Sensible for Shorts.
- Hooks: each clip has a burned-in title; they match the content. The first sentence is usually a question or a hard statement, so the first 2 to 3 seconds are mostly fine.

Problems:
- 4 clips end mid-sentence: rank 2 ("go to twinge con, everybody. I"), 3 ("I'm out"), 5 ("You"), 17 ("Because").
- Rank 1 (396 to 446 s) mixes a joke with the **game's tutorial voice-over** ("Two moderators available… Assign up to three mods…"). It was rejected. The engine cannot tell game UI narration from the streamer.
- Hooks invent a real-life claim from game dialogue: rank 9 title "I caught my wife with a neighbor — she told me she ain't stopping" is an NPC line in the game. For a real creator account this is misleading and a rejection risk.
- Several scenes are in-game adult scenes (rank 3 frame). No visual compliance check exists.

Video A: no usable boundaries (no words). One clip is 7 s long ("kept to reach the minimum of 3 clips").

## 4. Moment choice (video B)

Engines for the 19 clips: 6 "topic", 13 "own". Scores 0.60 to 0.82.
- All 6 "topic" clips score 0.80 to 0.81 (saturated, no real ranking) and their stated reason is junk keywords ("The strongest stretch of a topic: get annoying who", "bro get dude", "house way bro"). They are chosen for being a stretch of a topic, not for being good.
- The "own" engine with LLM ratings gives readable reasons ("dramatic dollar amount hooks viewers", "doxx reveal and absurd actions…") and I judge those better: ranks 8, 10, 11, 12 are plausible comedy beats of a joke-streamer game.
- Honest judgement (from the transcript text only; I cannot judge comedy without watching): **mediocre overall**. About one third are real beats (8, 10, 11, 12, 14), one third are banter that depends on the visuals, one third are tutorial or filler. The content type (a game with scripted NPC dialogue) fits our "podcast" scoring badly.
- Moments of the same time range appear several times (video A: rank 2, 3, 4 are the same 5226 s window from "own" and "topic"; the engine does not de-duplicate across engines).

Video A: all 8 moments come from loudness only (scores 0.46 to 0.54, reasons "Loud stretch", "Picked on its overall signals"). Not a real moment choice.

## 5. Ranked root causes (biggest visible damage first)

1. **Wrong language accepted silently.** Video A produced 27 words instead of about 15,000, so every later step ran blind. Also wrong label and Whisper hallucination risk.
2. **Gaming and non-talking-head video is treated as a podcast.** Fixed vertical crop of 32 % of the picture, no facecam, no gameplay layout, chosen by campaign niche only.
3. **The "face" detector is a skin-colour guess with one static x per clip.** Works for a single person in a static shot; fails with any other skin tone or content (game characters), and never tracks or smooths.
4. **Moment scoring for non-podcast content:** saturated "topic" scores with junk reasons, no de-duplication across engines, no filter for game narration, hooks that present fiction as a first-person claim.
5. **Cut ends:** 4 of 19 end mid-sentence; no check against the clip's maximum length.

## 6. Fix plan, ordered by visible improvement per hour

| # | Fix | Size | Gain |
|---|---|---|---|
| 1 | **Language check.** At upload: detect the language with local Whisper on 3 samples of 30 s (about 5 s on the GPU), pre-select it, warn if the owner's choice disagrees. After transcription: if words per minute is under about 20 for a video with speech, raise an alert and retry once in the detected language. Never run local Whisper with a forced language that disagrees with detection. | 2 to 3 h | Fixes video A completely; prevents the whole class of failure. |
| 2 | **Content type + gaming layout.** Let the owner pick the video type at upload (podcast, gaming, other) and default it from the title and a quick sample. For gaming build the standard **stacked layout**: facecam on top (about 1/3), gameplay centre crop below. The facecam rectangle is detected, or the owner marks it once per video (draw a box on one frame). | 4 to 6 h | Biggest visual change for gaming clips. |
| 3 | **Real face detector with tracking.** OpenCV YuNet on CPU (about 5 to 10 ms per frame at 640 px), sampled at 3 to 5 fps, smoothed path (exponential smoothing plus a dead zone so the crop does not jitter), crop moved with an ffmpeg expression. For interviews use the speaker labels to pick the speaking face. Fallback stays the centre crop. | 6 to 8 h | Podcasts and interviews: crop follows the person. Needs the download approval in section 9. |
| 4 | **Moment quality.** De-duplicate across engines; stop "topic" clips from outranking rated ones; drop junk topic labels; hook prompt told when the video is a game so it does not write first-person real-life claims; skip spans of known narration (low priority). | 3 to 4 h | Fewer mediocre and duplicate clips. |
| 5 | **Sentence-end snap.** Move the end to the nearest sentence end inside the length window; reject a clip that cannot end on one. | 1 to 2 h | Removes the 21 % mid-sentence ends. |
| 6 | **Visual safety flag** for adult or violent frames (sample frames, a small classifier). | 4 to 6 h | Brand safety; can wait. |

Suggested order: 1, then 5 (cheap), then 2, then 3, then 4. Items 1 and 5 together are about half a day and fix the two clearest defects.

## 7. Objective clip standards (no owner input needed)

Every standard is a number that can be measured on the finished clip. "Exists" means the code already does it; "missing" means nothing checks it. Time per clip is an estimate for a 45 s clip on this laptop (GTX 1660 Ti), to be confirmed when built.

### 7.1 Crop

| Standard | Today | How to measure automatically | Time |
|---|---|---|---|
| Subject inside the 9:16 frame in at least 95 % of sampled frames | **Missing.** `media.py:face_focus` guesses one x from a skin mask and never checks the result. | Detect faces (YuNet) in the **source** at 3 fps over the clip, compute the crop window per frame, count the frames where the main face box is fully inside. Pass at 95 % or more. For gaming: face-cam box and gameplay box both visible. | about 1.5 s |
| The crop moves only on speaker changes, smoothly, with a hold time, no jitter | **Missing.** One static x per clip, so no movement at all. | From the crop path: count moves (should equal speaker changes from diarization), the maximum speed (px per s), the minimum hold between moves (at least 2 s), and the high-frequency jitter (variance of the second difference). | free once the path exists |
| Gaming: gameplay and face-cam as a split layout, not a centre crop | **Missing.** `layout_graph` has `facecam_over_game` (whole picture over a blurred fill) but it is chosen by nothing, and video B was cropped as a podcast (section 2). | Layout name in `render_meta` must be `split` for a gaming profile; check that the face-cam region and the gameplay region are both non-empty in the output frames. | free |

### 7.2 Cuts

| Standard | Today | How to measure | Time |
|---|---|---|---|
| Starts on a sentence start, never inside a word | **Partly.** `pipeline.snap` and `moment_rank.BREATH_BEFORE/AFTER` snap to sentence boundaries. Measured on video B: 17 of 19 start on a sentence, 15 of 19 end on one. No final check. | From the word JSON: the previous word ends with `. ? !` or a gap of at least 0.3 s; the start time falls in a gap, never inside a word's span. Ends: last word's end plus a tail of 0.15 to 0.4 s. | free |
| No dead air or filler in the first 2 s | **Missing** (the `dead_air_out` edit cuts silences inside the clip, but the opening is not checked). | Silence share and filler words ("um", "uh", "you know", "يعني") in the first 2 s; pass when speech starts within 0.5 s and silence is under 30 %. | free |
| Self-contained: no unresolved reference at the start ("he said that…") | **Partly.** The engine rates "makes sense on its own" with the LLM (`standalone` feature), but there is no hard check per clip. | Rule list on the first sentence (starts with a pronoun, "and", "but", "so", "that", no named subject within 8 words) plus one small LLM question: "Can a stranger understand the first sentence? yes/no". | about 2 s, under $0.001 |
| Length within the profile | **Exists:** `compliance` duration check (5 to 90 s) and profile `length_min_s/max_s`. | Already measured. Tighten to the profile range, not only the global one. | free |
| No duplicate or overlapping moments | **Partly.** `moment_rank.dedupe` and `overlap_share` work **inside one engine run** (published clips are also checked). Video A had the same window picked by two engines. | Overlap share of any two clips from one source (limit 0.2) and a text similarity check (shared 8-grams) across engines and across variants. | free |

### 7.3 Audio and video

| Standard | Today | How to measure | Time |
|---|---|---|---|
| Loudness about -14 LUFS, no clipping | **Applied, never verified.** `media.py:399` runs `loudnorm=I=-14:TP=-1.5`. | Second pass `ebur128` on the output: integrated loudness within ±1.5 LU of -14, true peak at or under -1 dBTP. | about 1.5 s |
| Minimum resolution and sharp | **Partly.** Output is 1080×1920 (fixed). `media.py:sharpness` exists but is only used to pick the cover. | Check output size; compare the source's height (a 480p source scaled to 1080 is soft): fail below 720 px source height on the used crop, sharpness (Laplacian variance) under a floor measured from good clips. | about 0.5 s |
| No letterbox or pillar bars | **Missing.** | `cropdetect` on the source at 3 points; if the picture area is smaller than the frame, crop it first. Re-run on the output: the black border must be under 2 %. | about 1 s |
| Captions readable, in sync, inside platform safe zones | **Partly.** Fit and contrast logic exists (`fit_size`, `_contrast`); no check for sync or safe zones. | Sync: caption event times vs the word times of the transcript (maximum drift 0.25 s; free), or re-transcribe the clip with Whisper for clips cut from a bad transcript (about 3 s on the GPU). Safe zones: caption box inside the central area (top 12 %, bottom 22 %, right 12 % free; TikTok and Reels UI), measured from the ASS style margins. Readability: minimum font height of 4 % of the frame, contrast ratio at least 4.5. | about 0.5 s to 3 s |
| No intro music, credits, ads | **Missing.** | Music share from a music-versus-speech classifier on the audio (or the existing `audio` signal plus spectral flatness); credits by the transcript lacking speech while the frames show scrolling text (frame difference plus OCR is overkill: use silence plus low speech density at the clip start and end); ad reads by a keyword list ("sponsored by", "use code") on the transcript. | about 1 s |

### 7.4 Content

| Standard | Today | How to measure | Time |
|---|---|---|---|
| Minimum speech density; nothing to say means dropped | **Partly.** `moment_rank.py:459` drops a moment under `MIN_SPEECH_WORDS` when the profile needs speech. Video A (27 words for 109 min) shows the transcript can be empty without anything noticing. | Words per second in the clip (spoken content should be at least 1.5 words per second for talking content; gaming "natural" profiles can allow less), and the transcript-versus-audio check from section 1 for the whole source. | free |

### 7.5 Proposal: a pre-review gate

Run all standards after the render and before the clip reaches Review. Each clip gets a `quality_report` row (JSON, next to `compliance_report`) with every metric and a pass or fail.

1. **Pass:** the clip goes to Review and Review shows two or three metrics ("subject in frame 98 %", "-14.2 LUFS", "starts on a sentence"). Nothing is hidden: a failed or repaired clip is listed in Filtered with its reason, like set-aside moments today.
2. **Repairable fail:** one automatic repair, then re-measure. Repairs available without new models: move the cut to the nearest sentence boundary, re-normalise loudness, re-crop with the tracked path, drop the first words that are filler, re-time captions.
3. **Not repairable** (nothing to say, duplicate, subject never in frame, ad): dropped, with the reason kept.
4. The whole gate costs about **8 to 12 seconds per clip** on this laptop (2 to 3 s of it only when the crop detector and the caption re-check run), against about 10 to 20 s for the render itself. It runs as its own job stage (`quality`) between `render` and `compliance`, as the code standards describe.
5. The numbers double as the training data for the benchmark below (clip features against the crowd signal).

Size: the gate framework plus the free checks (cuts, speech density, loudness, sharpness, letterbox, safe zones, duplicates): 1 to 1.5 days. The crop checks need the detector first (section 8, step 3 of the fix plan).

## 8. Market calibration benchmark (replaces "ask the owner for clips they like")

The owner's taste is not the target. The target is what viewers and the platform algorithms reward. We measure that with public crowd signals on existing videos, and tune the engine against it.

### 8.1 Ground truth by platform

Platforms were not probed yet; this table is what each platform offers and what I expect anonymous access to give. It needs a real anonymous test (step 0 below).

| Platform | Crowd signal | Access | Usable as a label? |
|---|---|---|---|
| **YouTube** | "Most replayed" heatmap per video (about 100 segments), view count, likes, comment count | `yt-dlp` metadata, anonymous. The engine already parses the `heatmap` field (`adapters/heatmap.py`). yt-dlp is **not installed** now. | **Yes, the best one.** Replay share per segment is a direct measure of what viewers keep watching. Only on videos with enough views; shorts and small channels have none. |
| **Twitch** | Clips with view counts and the VOD offset they were cut from | Official Helix API (needs an app client id and secret; app tokens only, no user login). Clip metadata includes `vod_offset` and `view_count`. | **Yes.** Clips are viewer-made highlights of a VOD: a clip's offset is a human "this was the moment" vote, its views are a strength. Metadata only; the VOD is needed only for feature extraction. |
| **TikTok** | View, like, comment and share counts per public video | yt-dlp, anonymous, often blocked or rate-limited | **Weak.** One number per finished short, no inner timing. Tells us which finished clips travel, not which moment inside a source. |
| **Instagram** | Likes and views per public Reel | yt-dlp anonymously usually refuses (login wall) | **Probably none.** Do not force it. |
| **Facebook** | Views and reactions on public videos | yt-dlp anonymous sometimes works for public pages, often blocked | **Weak or none.** Do not force it. |

Hard rules from the owner's decision (also in `docs/decisions.md`): anonymous access only, no cookies or tokens from the owner's real accounts, at most about 2 downloads a minute, internal measurement only, delete videos, frames and audio after the features are extracted, keep numbers and small feature files, everything under `.data/benchmark/`, with a manifest. If a platform refuses anonymous access we report it and stop; no workaround.

### 8.2 Benchmark design

- **Set:** 50 to 100 videos, in the owner's niches: gaming (40 %), podcasts and interviews (40 %), business and talks (20 %). YouTube long videos with a heatmap are the core (about 50). Twitch: 20 VODs that have at least 5 clips. TikTok, Instagram, Facebook: only as many as anonymous access allows; they are extras, not the core.
- **What we run:** our moment engine on each video, with the **replay signal switched off for YouTube** (the engine reads the heatmap as an input; scoring it against the heatmap would be circular).
- **Metrics:** hit@k (does any of the engine's top k moments overlap a top-k replayed segment), overlap share with the top replayed segments, Spearman rank correlation between our score per segment and the replay value, and for Twitch the fraction of human clips that our moments cover. Report each per niche.
- **Tuning:** the router, the soft-score weights (`profiles.py` weights) and the hook patterns, searched against the metric on a **training split of 70 %**, checked on a **held-out 30 %** that is never used for tuning. Report the gap; stop when the held-out number stops improving. With only 50 to 100 videos, tune few parameters (the nine feature weights per profile, cut-off and length) and prefer simple search (coordinate search or random search) over anything fancy.
- **Gate metrics as features:** for every candidate we also store the section 7 metrics, so we can check whether clips that pass the gate also score better against the crowd.

### 8.3 Open baseline model

An open highlight detector gives a reference point for the number we are tuning. Candidates I know from the literature (QVHighlights benchmark): **Moment-DETR** (CLIP plus SlowFast features) and **UniVTG** (CLIP features, a unified model for highlights, moments and summaries). Both are small transformers and should run on a 6 GB GTX 1660 Ti for inference (feature extraction with CLIP is the heavy part, roughly real time or faster). **I have not verified the current checkpoints, licences or install status here**, and neither is installed. Before recommending one, step 0 below checks: the licence of the weights, whether a pretrained QVHighlights checkpoint is downloadable, and a 2-minute smoke test on the GPU. They are trained on short English video with queries, so expect them to do poorly on long Arabic speech; that is itself a result.

### 8.4 Steps, sizes and costs

| Step | What | Size | Cost |
|---|---|---|---|
| 0 | Install `yt-dlp` (about 15 MB) in the venv. Anonymous probe of each platform with **one** public video per platform (no cookies), record what works. Register a Twitch app (client id and secret) in `.env` (owner does this, I never read it). Smoke test the baseline model. | 2 to 3 h | none |
| 1 | Manifest and downloader with a hard rate limit (2 per minute), delete-after-extract, hash and size in the manifest. First batch (below). | 3 h | none |
| 2 | Evaluator: crowd label extraction (heatmap, Twitch clip offsets), metrics, train and held-out split, report. | 6 to 8 h | none |
| 3 | Run our engine over the set. Local transcription at about 5 min per hour of audio on the GPU. 60 videos averaging 1 h: about 5 h of machine time, overnight. LLM rating cost at the moment budget: under $5 total. Deepgram is not needed (use local Whisper). | 1 day elapsed, 2 h of work | about $5 |
| 4 | Baseline model run on the same set and comparison. | 4 to 6 h | none |
| 5 | Tune on the training split, check on held-out, record the weights as new profile defaults. | 1 to 2 days | about $5 to $10 |

Total: about 4 to 5 working days spread over a week, mostly waiting for transcription.

### 8.5 First batch (proposal, nothing downloaded yet)

To be approved before the first download:

- **Up to 10 videos, one every 30 seconds with random jitter, total under 5 GB, about 6 minutes of downloading.** Fewer if platforms refuse.
- 4 YouTube videos with a heatmap (1 gaming, 1 podcast, 1 interview, 1 business talk), at 360p or 480p video plus audio only: the benchmark needs audio and a few frames, not high quality, and smaller files are lighter on the network.
- 2 Twitch VODs with their clips (metadata through the official API, VOD at 360p).
- 1 each of TikTok, Instagram, Facebook as the anonymous probe; if one refuses, stop on it and report.
- I will propose the exact list of URLs for approval first; I do not pick videos from memory.
- Per the owner's guardrails, the bulk waits until the owner confirms their accounts exist.

### 8.6 What the benchmark cannot tell us

- **Replays are not virality.** A YouTube replay peak is a moment people rewatch inside a long video. TikTok and Reels reward the first 1 to 3 seconds, completion and rewatch of a **short** video, and the algorithm adds account history, trends and posting time. A moment that wins on replay may not make a good short.
- **Survivorship and size bias.** Videos with heatmaps are popular ones; Twitch clips are made by viewers of big streamers; small niches and Arabic content are under-represented.
- **Hooks and captions are not measured.** The benchmark scores which moment, not how the clip opens or what the title says.
- **Taste of the market moves.** The numbers go stale; re-run every few months.
- **It is a proxy.** The only real test is our own posted clips' view data (phase F). The benchmark gets the engine to a sensible starting point so the first real posts are not wasted.
- **Cropping is not covered** by crowd signals at all; it is covered by the standards in section 7.

## 9. Smallest owner input still useful

Not a reference set of taste, only two checks: confirm the video type and language at upload (section 5, fixes 1 and 2), and approve the downloads in section 8.5 and the OpenCV and YuNet download (about 40 MB and 0.3 MB, to `.data/models`).
