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

Detector benchmark: **not run**, because it needs a download (`opencv-python` about 40 MB plus the YuNet ONNX model about 0.3 MB) and the owner said to ask first. Request is in section 7.

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
| 3 | **Real face detector with tracking.** OpenCV YuNet on CPU (about 5 to 10 ms per frame at 640 px), sampled at 3 to 5 fps, smoothed path (exponential smoothing plus a dead zone so the crop does not jitter), crop moved with an ffmpeg expression. For interviews use the speaker labels to pick the speaking face. Fallback stays the centre crop. | 6 to 8 h | Podcasts and interviews: crop follows the person. Needs the download approval in section 7. |
| 4 | **Moment quality.** De-duplicate across engines; stop "topic" clips from outranking rated ones; drop junk topic labels; hook prompt told when the video is a game so it does not write first-person real-life claims; skip spans of known narration (low priority). | 3 to 4 h | Fewer mediocre and duplicate clips. |
| 5 | **Sentence-end snap.** Move the end to the nearest sentence end inside the length window; reject a clip that cannot end on one. | 1 to 2 h | Removes the 21 % mid-sentence ends. |
| 6 | **Visual safety flag** for adult or violent frames (sample frames, a small classifier). | 4 to 6 h | Brand safety; can wait. |

Suggested order: 1, then 5 (cheap), then 2, then 3, then 4. Items 1 and 5 together are about half a day and fix the two clearest defects.

## 7. What we lack, and the smallest way to get it

We have **no reference set**: nothing tells us what the owner considers a good clip, so every score and threshold is a guess. The only signal so far is accept/reject taps (rank 1 and 5 rejected, others accepted), with no reasons for most.

Smallest way to get one (about 30 minutes of the owner's time):
1. Owner opens the 19 clips of video B in Review and marks each **good / okay / bad** with one word on why (the Review screen already stores accept/reject with a reason; use "bad" plus the reason chips).
2. Owner sends **5 to 10 links to shorts they wish ours looked like** (same genre: gaming and talk).
3. We then compare our scores with their marks, find which signals separate good from bad, and tune weights and cut rules against that set, as a small repeatable script.

Approval needed from the owner before step 3 of the plan: download `opencv-python` (about 40 MB) and the YuNet model (about 0.3 MB, from the OpenCV Zoo) to `L:\Projects\Clipping\.data\models`, so the detector can be benchmarked on videos A and B.
