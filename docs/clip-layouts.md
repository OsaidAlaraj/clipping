# Clip layouts (single, gaming, split, reaction)

Code: `backend/clipping/layouts.py` (planning, safe areas, suggestion, filtergraph), wired into `adapters/media.py` (`render_clip`), `review.edit(layout=...)`,
`pipeline.stage_render`. UI: `web/components/LayoutPicker.tsx`, inside the per-clip hooks sheet in `Feed.tsx` (the same editor as the hook, one place).
Measurement: `tools/measure_layouts.py`; numbers and frames under "Measured" below.

## What the field does (research, before building)

| Tool | Layouts | How it picks | What we took |
|---|---|---|---|
| OpusClip | Fill, Fit (4:3 on blurred bars), Split (two people), Three, Four, ScreenShare, Gameplay (30/70) | The owner can tick several layouts and the AI picks per scene; headline position is decided from layout and face position | One suggestion per clip from signals, always overridable; headline kept clear of faces |
| OpenShorts (open source) | SPLIT: two speakers stacked, captions on the seam; MediaPipe and YOLOv8 for faces | Face count per scene | Stacked panels, captions on the seam |
| opensource-clipping | Podcast mode: Pyannote speakers, 9:16 top/bottom split | Speaker count | Same shape; we use detected faces (already in the repo) instead of a new diarization model |
| Clipify, OpenClipper | Hard-cut pan to the speaker, or split when two people do not fit one crop | Two people do not fit | Same rule: a split only when two faces are clearly apart |
| CapCut templates | Split screen with a second feed (gameplay, reaction) | Manual | A second feed from a feeds folder |

Nothing found that does picture-in-picture reaction with measured safe areas, so that part and the safe-area check are ours.

## The four looks (1080x1920)

- **single**: the existing crop that follows the speaker (or the blurred pad). Default for one speaker.
- **gaming**: the existing face-cam over gameplay (`gaming_layout.py`), unchanged.
- **split**: two panels of 1080x960 with a 12 px seam. *Two people* in the same two-shot: the left person on top, the right below, each crop keeps the neighbour out. *One person and a feed*: the person on top, a looped file from `.data/feeds/` below (sound unused). Captions are forced onto the seam; no emphasis zoom.
- **reaction**: the face fills the frame (or a sharp inset webcam does, at least 480 px tall), the whole picture sits in a white-framed card (52 percent of the width, 44 percent when the first size has no clear place). The card takes the first slot that covers no face, title, caption or label.

## Safe areas (shares of the 1920 px height)

| Band | Share | Why |
|---|---|---|
| Label and future logo strip | 0 to 7 % | the ad label (top left, top right for Arabic); a logo uses the same strip |
| Hook title | 8.5 to 16.5 % | `build_ass`, top position |
| Seam captions (split) | 44 to 56 % | captions on the seam |
| Lower captions | 70 to 85 % | margin 16 %, up to three lines |
| Bottom controls | 86 to 100 % | progress bar and the platform's own buttons |

`layouts.violations()` computes where each face and the card land in output pixels and names what is under what. A plan with any violation is refused: the clip renders as the single crop and the reason is kept in `render_meta.layout_fallback` and shown in the editor. Faces in a split sit at 52 % of their panel at 26 % of the panel width, which clears the title and the seam.

## Suggestion (auto) and override

`layouts.suggest`, in this order: vertical picture means single; gaming niche means gaming; a clean two-shot (two clearly apart faces of similar size, no third comparable face) with a plan that passes the safe areas means split; the campaign's edit-style text (`brief_wiring.edit_style`) asking for a split screen, with a feed available, means split; asking for a reaction with a plan that passes means reaction; otherwise single. Conservative on purpose: a wrong split looks worse than a plain crop. The owner's pick is stored in `render_meta.layout` (auto removes the override), one tap makes one new version, and the render reports `layout_used`, `layout_suggested` (with the reason) and `layout_fallback`.

## Measured

See the section at the end of this file (filled from `tools/measure_layouts.py` runs).

### Measured (2026-11-26)

Footage: `.data/benchmark/shots/{en_business, en_podcast, en_stream, ar_podcast}/ep.mp4` (854x480, so every crop is a hard case: a 1080p source gives the planner more room) and `.data/samples` (a 1080p podcast and a 240p Arabic interview). Real renders through `FFmpegRender.render_clip` (one encode, ASS captions, hook title), 14 s windows, `tools/measure_layouts.py`. Frames: `docs/clip-layouts/en_business_346_single_split_reaction.jpg` (single, split, reaction of the same moment).

- **Planning only:** a scan of 16 windows in each of 4 episodes found a clean two-shot in 6 windows, and a split plan that passed the safe areas in 4 of them, all in en_business (the 2 in en_podcast were refused: three people in the picture). Reaction (3 windows in each of 6 sources, 18 windows): a plan in 7, the rest refused (close-up faces, heads too high in the picture, no face). Every refusal named its reason; no plan with a safe-area violation reached a render.
- **Rendered (6 windows, 3 layouts each):** every requested layout that had a plan rendered in one encode (`encodes = 1`, Spy test as well); safe-area violations in rendered plans: 0. A layout without a plan fell back to single with the reason (en_podcast split: not a two-shot; cordkillers reaction: close-up face, no room for a card).
- **Faces still found in the output frames** (detector on four 360 px frames per clip; 2 persons possible in split): en_business @346 split 2,2,2,1 against single 1,1,1,1; @115 split 1,1,2,2; @1501 split 2,1,2,2 against 1,1,1,0. Both people are found in 8 of 12 sampled split frames, against 0 of 12 for the single crop, which can only hold one.
- **Render time:** split 15 to 21 s, reaction 18 to 26 s per 14 s clip at 1080x1920 (the single crop in the same runs was 42 to 60 s, dominated by the speaker model; not caused by the layouts).
- **Bug found by the measurement and fixed:** the first reaction render centred on a point between two men, because the median of "the biggest face per frame" flips between people. `main_face` now groups faces by position and takes the person seen most; the second render shows the face whole.
- **Found, not fixed (not this task):** the existing single crop at en_business @346 cuts the right-hand man at the frame edge (frame 1 of the sheet).
- **Not measured:** a 1080p two-shot (none in the library), real gameplay feeds, Arabic captions on the seam, and a reaction from an inset webcam over content (no such source on disk; the planner refuses a webcam under 480 px and its geometry is unit-tested).
