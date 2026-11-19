# Survey: open-source parts for clip quality

Written from public knowledge of each project plus what was run here; "last update" and "evidence" for projects not run today are from memory of their READMEs and were **not** re-verified online (the session had no budget for it). Hardware: GTX 1660 Ti, 6 GB. Private local tool: GPL/AGPL is fine, non-commercial licences are not.

## (a) Vertical reframing and active speaker

| Candidate | Licence | Fit on 1660 Ti | Install | Evidence |
|---|---|---|---|---|
| OpenCV YuNet (opencv_zoo) | MIT | CPU, 1.5 s per 30 s at 3 fps | 0.3 MB model, `opencv-python-headless` about 50 MB | **Run today.** Best face finder of the three tested; finds a small face-cam in 100 % of frames |
| MediaPipe BlazeFace | Apache-2.0 | CPU, 0.3 s per 30 s | 0.2 MB model, mediapipe wheel about 100 MB | **Run today.** Fast, but misses small faces (face-cam 12 %) and is jumpier on cuts |
| MediaPipe FaceLandmarker (mouth opening) | Apache-2.0 | CPU, 2 s per 30 s | 3.8 MB model | **Run today.** Mouth-activity speaker pick: no gain over YuNet on single-face shots; untested on wide two-shots (none in the sample) |
| Skin-colour centroid (current code) | ours | CPU, instant | none | **Run today.** Baseline: 10 % in crop on the interview, 0 % face-cam |
| OpenShorts (MediaPipe + YOLO fallback + stabilisation) | MIT (as remembered) | CPU/GPU | repo clone | Same detector family as tested; idea: fallback chain and stabilisation. Not run |
| wjs-reframing-video (mouth activity) | not checked | CPU | small | Same idea as the mouth-activity run above. Not run |
| clipsai resize | MIT (as remembered) | needs pyannote and a HF token | heavy | Audio diarisation plus face; heavy dependency chain, not run |
| Google AutoFlip | Apache-2.0 | MediaPipe C++ graph, Bazel build | very heavy on Windows | Not run: build cost out of proportion |
| TalkNet / Light-ASD | MIT / MIT (as remembered) | GPU ok | torch plus weights | The real audio-visual active-speaker answer for crowded multi-speaker video. Not run: needs a labelled multi-speaker test set we do not have yet |

Pick to test next: YuNet (done, wins), Light-ASD (only when real wide two-shots appear), FaceLandmarker mouth (kept as a tie-break).

## (b) Dead air and filler

| Candidate | Licence | Fit | Note |
|---|---|---|---|
| Word timings from our transcript | ours | free | Cut gaps over about 0.5 s between words and clip filler words ("um", "uh"); no new dependency |
| Silero VAD | MIT | CPU, 2 MB | Better than energy threshold when music or game audio is loud |
| auto-editor | Unlicense | CPU, ffmpeg based | Does silence cuts with margins; we already have `ffmpeg.silences` |

Pick: word timings first (free), Silero VAD as the second test.

## (c) Animated word-by-word captions

| Candidate | Licence | Note |
|---|---|---|
| libass karaoke tags (`\k`, `\kf`) | ISC | Already in our ffmpeg; one pass, no extra install |
| Remotion / moviepy-style overlays (what several clippers use) | MIT / commercial terms | Separate render pass, slower |
| pysubs2 to generate ASS | MIT | Convenience only |

Pick: libass karaoke with per-word highlight (already our stack).

## (d) Moment finding (ideas only)

clipsai scores segments from transcript topic shifts (TextTiling-style) and our engine already has several engines plus a learner. FunClip picks by an LLM over the transcript with a speaker filter. Ideas worth taking: segment on topic boundaries; end only on sentence ends (a fix already in the diagnosis); give the LLM the video type so a game is not described as real life.
