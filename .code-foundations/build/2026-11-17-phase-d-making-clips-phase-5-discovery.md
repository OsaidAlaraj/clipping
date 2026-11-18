# Discovery + Design: Phase 5 - One encode, originality layer, covers

## Files Found
- backend/clipping/adapters/media.py (FFmpegRender.render = cut+crop+loudnorm encode 1; FFmpegCaptions._burn = ASS burn, encode 2; CentreCropRender and PlainTextCaptions are the declared fallbacks)
- backend/clipping/ffmpeg.py (run, probe, frame_png, streams); backend/clipping/pipeline.py (stage_render, stage_captions, `_make_cover` JPEG pilot cover, pick_account, run_compliance)
- backend/clipping/signals.py (series loudness/laughter/scene/face per second; face = skin-share, no x position); profiles.py + candidates.style {"crop","audio","edit"}
- review.py `_edit` copies render_meta to the new clip version and re-enqueues `render`; review._decorate reads render_meta.cover_key for cover_url
- Tests touching the old flow: test_3_pipeline.py (job count incl. captions, render_key pixel test, missing-disclosure test, FFmpegCaptions.burn_all), test_10b_pilot.py (JPEG cover, `_make_cover`)
- web/components/Places.tsx `Profile()` lists accounts with AudienceField; web/tests/round2.test.tsx

## Current State
Two encodes per clip (render.mp4 then final.mp4 with ASS). Arabic label uses the same Alignment 7 (top-left) as English. Cover = one JPEG frame at 1.2 s, no text. No templates, no persona. cv2 not installed (face_x never worked); NVENC h264 works on the GTX 1660 Ti (probe encode 0.9 s).

## Gaps
- Account is chosen only at package time (after render), but the template and platform caption style need it at render: pick_account moves into stage_render (package keeps an already-set account).
- Signals have no face x position: face x is estimated from the skin mask centroid of a few frames (numpy only), centre crop when nothing is found.
- Adapters must not import domain modules: template normalisation lives in adapters/media.py, templates.py (domain, DB) imports it.
- Plan file scope says migration 012_*; 012 is taken, the plan header says 013_templates.sql (used).

## Code Standards
Adapter classes with stage/provider, domain logic in backend/clipping/<area>.py, tables with RLS in the same migration, AppError(message, code) for refusals, tests named in full sentences, `stage_<name>` handlers, heavy imports lazy.

## Test Infrastructure
pytest + embedded Postgres per test, real FFmpeg, fakes for vendors; conftest base_template sets render config (270x480, ultrafast, crf 30); API tests via TestClient with `api._owner`; web: vitest + Testing Library with mockApi.

## DW Verification

| DW-ID | Done-When Item | Status | Test Cases |
|-------|---------------|--------|------------|
| DW-5.1 | One encode, captions and disclosure burned in | COVERED | test_DW_5_1_a_clip_is_one_encode_with_captions_and_disclosure_burned_in (spies ffmpeg.run, counts video-encode calls); pipeline job list has no captions job |
| DW-5.2 | Arabic disclosure top right, English unchanged | COVERED | test_DW_5_2_arabic_disclosure_sits_top_right_and_english_stays_top_left (ASS alignment + pixel check on a flat-colour source) |
| DW-5.3 | Two templates give measurably different frames | COVERED | test_DW_5_3_two_templates_give_measurably_different_frames (mean pixel difference); pipeline test: two accounts, different template_id in render_meta |
| DW-5.4 | Cover PNG with the hook text | COVERED | test_DW_5_4_each_clip_has_a_cover_png_with_the_hook_text (PNG magic, size, differs from hook-less cover in the text band); pipeline sets clips.cover_path |
| DW-5.5 | Owner edits template on Profile, next clip uses it | COVERED | test_DW_5_5_a_template_edit_through_the_api_is_used_by_the_next_clip; web: Profile template editor saves and shows persona |
| DW-5.6 | Versioned data, rotation, revert | COVERED | test_DW_5_6_a_new_template_is_used_by_the_next_render..., ..._rotates..., ..._revert_restores... |

**All items COVERED:** YES (6/6)

## Design Decisions
- media.render_clip(src, clip, words, template, label, out): one `-filter_complex` graph (layout -> optional zoom -> ass -> yuv420p) and one encode; the ASS file carries captions, title card, label, progress bar (an animated drawing, no extra filter). Zoom = `scale eval=frame` + centre crop before the ASS so captions and the label never zoom. User text never enters the filter string (only the fixed ASS file name does); ASS text is escaped.
- Layouts by the profile crop style: follow_speaker/follow_action = static 9:16 crop at the estimated face x; centre = centre crop; facecam_over_game = full frame fitted to the width over a blurred fill (nothing cut away, so the facecam stays).
- Encoder: cfg `encoder` auto|x264|nvenc (default auto). auto probes NVENC once (cached 2 min); a failed NVENC encode retries once with x264. `encodes` counts finished encodes (always 1).
- Rotation: least-recently-used per account (`templates.last_used_at`, null first, then created order). A new, edited or reverted template gets last_used_at = null, so it is used by the next clip. An account with no template gets three starter variations of one recognisable family (same font and primary colour, different accent/position/progress bar).
- Versioning: templates + template_versions (append-only, new row per change), revert = new version copying an older spec. Account persona is computed on read (look, posts per week, best hours from its own posts once at least 6 exist; otherwise typical hours, marked as such).
- Captions (R6): TikTok and Reels use the social style (. , ; : removed, ? ! kept, loud words in the accent colour); YouTube Shorts keeps punctuation and no highlights. Words are never changed.
- Cover (R7): candidate seconds scored from face/laughter/loudness minus scene change; the sharpest of the top 3 (Laplacian variance) becomes the frame; the hook line (cut to 60 chars at a word) is drawn in the template's cover style. PNG next to the clip; a cover failure never blocks the clip.
- Old `captions` job type stays registered: no-op when the clip already has its file, otherwise re-queues `render`.

## Prerequisites
- [x] Files exist  - [x] FFmpeg with libass/NVENC  - [x] numpy

## Recommendation
BUILD

## Build notes (added after implementation)
- Rotation is "fresh first, then least recently used": a template that was just added, edited or reverted is `fresh` (one per account) and goes to the next clip; the account's starter set is three variations of one look (same font and main colour), the first one fresh.
- Not done (out of the plan's list): the profile `edit` style (dead_air_out) is not applied in the render; the crop is one static 9:16 window per clip (median face position), not a moving one.
- Extra file: web/components/Look.tsx (the Profile look and posting plan panel), imported by Places.tsx. Test file: web/tests/look.test.tsx.

## Real check (engine-run throwaway dir, real data, 1080x1920)
Re-rendered through `pipeline.stage_render` (full stage: account, template pick, signals, one encode, cover) from the stored real runs: the Arabic interview (50 s clip, TikTok account) and the English podcast (41 s clip, Reels account), each with two templates (A: Arial, white/gold, captions lower, progress bar, cover on a gold band; B: Tahoma, mint, captions middle, no bar, outlined cover). Frames and covers are in .data/tmp/p5/.
- Encoder: NVENC (GTX 1660 Ti) on all four renders; each clip is one `-c:v h264_nvenc` call plus one PNG cover frame.
- Render time: 7.0 to 8.2 s per clip including the cover and signals read (the other test run was still using the machine for part of this); the first run before the full suite measured 5.3 to 8.0 s. About 6 to 7 times faster than real time.
- Looked at: Arabic label top right (in both templates); English label top left; Arabic title card wrapped to two lines and shrunk; captions in Arabic and English in the template's font and position; the speaker centred by the face crop; progress bar present in A and absent in B; covers sharp and expressive with the hook line (long Arabic hook cut at a word with an ellipsis, three lines, outlined in B).
- The Arabic "#إعلان" label reads "#" on its left because the label line is laid out left to right by the renderer (same as before this phase).
