# Quality bake-off (2026-11-19)

- **Tested:** reframing on 15 real 30 s segments (gaming stream, 109-min two-person interview, Arabic slide lecture): skin-colour baseline vs OpenCV YuNet vs MediaPipe BlazeFace vs MediaPipe FaceLandmarker with mouth activity. Contact sheet: `docs/bakeoff/reframe/index.html`; survey: `docs/survey-table.md`.
- **Interview winner: YuNet + smoothing** (speaker in the crop 89 % of frames vs 10 % baseline; about 1.8 s CPU per 30 s). Now the crop adapter (`backend/clipping/faces.py`), tracked path with jumps at cuts; old focus is the fallback.
- **Gaming: crop is the wrong tool.** Face-follow chases game characters; YuNet does find the face-cam (100 % vs 0 %), so the fix is a stacked layout using that rectangle. Not built yet. Lecture slides have no face: centre crop is right.
- **Fixes added:** alert when a long video's transcript is too thin (wrong language), `language_check.detect_language` (Whisper, 3 samples), sentence-end snap for clip ends. All with tests; relevant backend tests pass.
- **Limits:** the reference face is a detector consensus (favours YuNet); no wide two-shot in the sample, so mouth-activity speaker choice and Light-ASD are untested; survey details for projects not run are from memory.
- **Remaining:** stacked gaming layout with the YuNet face-cam box (about 3 h); wire `detect_language` into upload and auto-retry transcription (about 2 h); visual verification of one rendered clip per type (1 h); dead-air and karaoke captions bake-offs (about 4 h).
- **Estimated cost to finish:** roughly 10 h of work, about one fresh session per item.
- Nothing pushed. Models and clones live under `.data/benchmark/` (drive L), deletable.
