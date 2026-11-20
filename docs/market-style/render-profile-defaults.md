# Render profile defaults, version 2 (2026-11-20)

Status: **proposed, NOT wired into the render stage.** Measured on 39 vertical YouTube Shorts at 720p to 1080p (6 cells of 5 to 8 clips, EN and AR, business, gaming, podcast). Version 1 was a first guess from 9 clips at 144p; its file is `render-profile-defaults.json` (now updated to version 2 with the same keys plus new ones). Read the sample limits in `docs/market-style-study-2.md` first: YouTube only, small cells, heuristics, no OCR. Numbers: `docs/market-style/summary-v2.json`. Contact sheets: `docs/market-style/v2/`.

Marks: **CHANGED** (old value to new, why), `kept`, `new` (not in version 1). Keys follow the version 1 file. `cuts_per_10s` counts hard cuts plus punch-in jumps (a punch-in is a 12 percent or larger face-size step without a scene change); the hard-cut-only value is in brackets. `caption_y` is the caption centre as a share of the frame height from the top.

## Shared by all cells
| Key | Value | Mark and reason |
|---|---|---|
| `caption_y` | **0.58** (centre; with bottom alignment: bottom margin about 38 percent of the height instead of 16 percent) | **CHANGED 0.70 (and the render's real 0.80) to 0.58.** Market median 0.55 (quartiles 0.49 to 0.63); only 3 percent of clips put the caption in the bottom 22 percent, which is where the platform buttons and the title sit. Never below 0.72. |
| `caption_font_pct` | **3.6** of the frame height (range 3 to 4.5) | new. Market line height about 3 percent (quartiles 2 to 4). Current template default 4.2 is at the high end: lower it for talk niches, keep 4.2 to 5 for one-word styles. |
| `caption_words` | 2 to 3 per caption (Arabic word-by-word: 1 to 2), change every 0.5 to 1 s | new. Median 1.2 caption changes per second. Our `max_words` 4 and `max_s` 1.8 are slightly slow; use 3 words and 1.2 s. |
| `caption_style` | white, bold, soft shadow, accent colour on the active or key word | kept (accent `#FFC400` fits: yellow was the commonest accent). One third to a half of clips highlight; the rest are plain white. No multi-colour karaoke. |
| `hook` | title or question card in the first 1 to 3 s (about 3 s), face visible, sound from frame 0, no hard cut in the first second | new. Text on screen at 0.5 s in 79 percent of clips, face in 95 percent, no hard cut inside the first second in almost all. Our title card (0 to 3 s, `fad`) already matches. |
| `loudness_lufs` | **-14**, true peak -1.5 | **CHANGED -15 to -18 (version 1, varied by cell) to -14 for every cell.** Market median -14.7 (quartiles -18.6 to -12.4), and the pipeline already normalises to -14. Do not copy the -9 to -6 clips (over 0 dBTP) or the -25 unmastered ones. |
| `disclosure` | on-frame label first seconds to end, as now | kept. 2 of 39 clips had `#ad` and only in the title; no on-frame label seen, so ours is stricter than the market. |
| `end` | no fade for gaming; a 0.4 s fade or end card for the talk niches | new. Gaming 0 percent end-fade (loop style); Arabic business 100 percent, Arabic podcast 80 percent, business EN 75 percent. |
| `punch_in` | 3 to 4 per minute for talk niches, 8 to 12 per minute for gaming (upper bound) | new. 12 percent size step on the face; smooth zoom is rare (median 0). |

## Per cell
```json
{
 "status": "proposed v2, NOT wired into the render stage (39 vertical Shorts, 720p+, YouTube only, cells of 5 to 8; see study 2 limits)",
 "business_en": {"clip_length_s": [30, 70],  "cuts_per_10s": 2,   "hard_cuts_per_10s": 0,   "cuts_in_first_3s": 1, "layout": "full_frame_9x16_on_speaker", "caption_y": 0.58, "caption_font_pct": 3.4, "caption_words": 3, "loudness_lufs": -14, "background_music": "optional, low (75% have a steady bed)", "hook": "title box first 3 s"},
 "business_ar": {"clip_length_s": [45, 90],  "cuts_per_10s": 1,   "hard_cuts_per_10s": 0.1, "cuts_in_first_3s": 0, "layout": "full_frame_9x16_on_speaker", "caption_y": 0.6,  "caption_font_pct": 3.6, "caption_words": 3, "loudness_lufs": -14, "background_music": "none (0% have a bed)", "caption_direction": "rtl", "caption_style_extra": "white box behind keyword", "top_label": "show tag top corner", "end": "end card with fade"},
 "gaming_en":   {"clip_length_s": [12, 30],  "cuts_per_10s": 6,   "hard_cuts_per_10s": 2,   "cuts_in_first_3s": 2, "layout": "full_frame_reaction_or_stacked_facecam_over_gameplay", "caption_y": 0.55, "caption_font_pct": 3.8, "caption_words": 3, "loudness_lufs": -14, "background_music": "game audio or music bed (71%)", "hook": "title card at top third (0.32) or word captions", "end": "no fade, loop"},
 "gaming_ar":   {"clip_length_s": [25, 55],  "cuts_per_10s": 6,   "hard_cuts_per_10s": 2.5, "cuts_in_first_3s": 1, "layout": "full_frame_reaction_or_scene", "caption_y": 0.63, "caption_font_pct": 4.0, "caption_words": 2, "loudness_lufs": -14, "background_music": "bed (43%)", "caption_direction": "rtl", "top_label": "round title in top third (0.17)", "end": "no fade"},
 "podcast_en":  {"clip_length_s": [15, 45],  "cuts_per_10s": 4,   "hard_cuts_per_10s": 1.5, "cuts_in_first_3s": 1, "layout": "full_frame_9x16_or_two_person_stacked_split", "caption_y": 0.57, "caption_font_pct": 3.6, "caption_words": 3, "loudness_lufs": -14, "background_music": "optional (43%)", "hook": "title or question box first 3 s"},
 "podcast_ar":  {"clip_length_s": [15, 60],  "cuts_per_10s": 1,   "hard_cuts_per_10s": 0,   "cuts_in_first_3s": 0, "layout": "full_frame_9x16_on_speaker", "caption_y": 0.6,  "caption_font_pct": 4.5, "caption_words": 1.5, "loudness_lufs": -14, "background_music": "optional", "caption_direction": "rtl", "caption_style_extra": "one big word at a time, active word yellow", "hook": "big title on first frame", "end": "fade"}
}
```

## What changed and why (versus version 1)
| Cell, key | Version 1 | Version 2 | Evidence |
|---|---|---|---|
| all, `caption_y` | 0.67 to 0.70 | **0.55 to 0.63** | 39 clips, median 0.55, 3 percent in the platform UI zone; current render sits near 0.80 |
| all, `loudness_lufs` | -15 to -18 | **-14** | median -14.7, pipeline already -14 |
| podcast EN, `clip_length_s` | 45 to 90 | **15 to 45** | median 28 s (study 1 clips were 111 to 172 s outliers) |
| podcast EN, `cuts_per_10s` | 1 | **4** (hard 1.5) | 7 EN clips, median 4.4; calm style only held for Arabic podcast |
| podcast AR, `clip_length_s` | 45 to 90 (as podcast) | **15 to 60** | median 25 s |
| podcast, `cuts_in_first_3s` | 0 | **1 (EN)**, 0 (AR) | 43 percent of EN clips cut within 3 s |
| business EN, `cuts_per_10s` | 3.5 | **2** (hard 0) | median 1.9; most clips are one take with punch-ins |
| business EN, `clip_length_s` | 30 to 90 | **30 to 70** | median 41, upper quartile 69 |
| business AR, `clip_length_s` | 30 to 60 | **45 to 90** | median 82 (Thmanyah, Annajah, Khodr clips), n 5 |
| business, `layout` | blurred 16:9 pad | **full frame on speaker** | blurred pad not seen in viewed sheets |
| gaming EN, `clip_length_s` | 12 to 20 | **12 to 30** | median 25 s, 86 percent at 30 s or under |
| gaming, `cuts_per_10s` | 7 | **6** | 5.7 to 5.9 (hard 2.1 to 2.6) |
| gaming AR, `clip_length_s` | (not separate) | **25 to 55** | median 42 s, n 7 (challenge and Free Fire creators) |
| Arabic business and podcast, `cuts_per_10s` | 1.7 | **1** | medians 0.9 and 0 |
| gaming, `layout` | stacked | **full-frame reaction or stacked** | stacked seen in 3 Jynxzi clips, a reaction cam in 3 others |
| kept: `cuts_in_first_3s` for gaming 2, Arabic business 0, rtl direction | | | confirmed |

## Not decided (needs more data or the render stage)
- Exact caption font: the line height gives about 3 percent, the true font size is uncertain by about 15 percent without OCR. Test 3.4 to 3.8 against 4.2 in the owner's own compare view.
- TikTok and Reels: not measured; their top and bottom safe zones differ from Shorts. Until measured, keep the caption centre at 0.55 to 0.60, inside every platform's safe area.
- Cut cadence, stacked layout and blurred pad are not switchable in the render yet; the per-niche cadence stays advisory.
