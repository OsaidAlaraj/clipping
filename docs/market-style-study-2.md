# Market style study 2, higher resolution, bigger sample (2026-11-20)

Redo of `docs/market-style-study.md` (9 clips at 144p). Method, numbers, contact sheets and manifest are in `docs/market-style/`: `summary-v2.json` (per niche and language), `measurements-v2.json` (per video), `manifest-v2.json` (every URL, date, deleted), contact sheets in `docs/market-style/v2/`. Proposed defaults: `docs/market-style/render-profile-defaults.md` (and `.json`).

## Method
- Public YouTube Shorts pages only, anonymous (no cookies, no logins), a JavaScript runtime for the player, one download every 18 to 28 s, 4 MB/s rate limit, internal measurement only.
- **Resolution fixed.** With a JS runtime (`--js-runtimes node`) anonymous YouTube gave 720x1280 to 1080x1920 (study 1 got 144p because it had none). Mix: 28 clips at 1080x1920, 3 at 1080x1906, 8 at 720x1280 (the 39 vertical Shorts), plus 14 landscape or square files kept as reference.
- Candidates came from channel Shorts tabs and search metadata, picked for views and niche, not from memory. Each video was downloaded, measured by script (`ffmpeg` frames at 5 per second, face detector, `ebur128`, audio envelope), a contact-sheet row saved, then **deleted**. Because the first analyser pass could not see punch-ins, 29 clips were downloaded a second time with the improved analyser (82 downloads for 53 clips).
- Measured: cuts and shot length, face size and punch-in or zoom steps, caption presence, vertical position, line height and change rate, accent colour share, loudness (integrated LUFS, true peak), quiet-floor music proxy, opening sound, first-second cuts, top overlays, end fade, `#ad` tags in title and description. I looked at 12 contact sheets (about 60 rows) to check the heuristics and to note layout, highlight and hook patterns.

## Sample (and its honest limits)
- **53 clips measured, 39 true vertical Shorts** used for the numbers: business EN 8, business AR 5, gaming EN 7, gaming AR 7, podcast EN 7, podcast AR 5. Three downloads failed (over the 125 s filter or format unavailable). 11 search results turned out to be 16:9 uploads (Lex Clips, Valuetainment, several Arabic videos) and are excluded from the stats.
- **YouTube only.** No TikTok or Instagram Reels: both forbid automated access, Reels needs a login, and the task said not to scrape where terms forbid it. Many of these creators cross-post the same edit, but platform-specific differences (TikTok caption styles, Reels safe zones) are **not measured**.
- **Not a random sample.** Channels were chosen for views and for being clip or short-form specialists. Gaming is creator content (Jynxzi, IShowSpeed, Valkyrae, Arabic challenge and Free Fire channels), not gameplay-montage edits. The Arabic podcast and business cells are small (5 each) and mix motivational channels, Thmanyah and a TV-style clip; they are weaker evidence than the English cells. n per cell is 5 to 8, so treat medians as direction, not rules.
- **Heuristics, not ground truth.** No OCR: caption words per caption are eyeballed (2 to 3 words typical), not counted. "Punch-in steps" come from face-width jumps of 12 percent or more in one 0.2 s step and are inflated when several faces are on screen (challenge videos, split screens). Report two cut numbers: **hard cuts** (histogram plus pixel jump, lower bound) and **all cuts** (hard plus punch-in steps, upper bound). Font size is read from the caption line height (text row band, outline included), so it is within about 15 percent of the real font size. Music is a quiet-floor proxy and cannot tell music from room tone or game audio. Layout notes (full frame, stacked, card on black) come from the sheets I viewed, not from a full manual pass.

## Findings (vertical Shorts, medians and quartiles)
| Cell | n | Length s | Hard cuts per 10 s | All cuts per 10 s | Cut in first 3 s | Face width | LUFS | Steady bed* |
|---|---|---|---|---|---|---|---|---|
| Business EN | 8 | 41 (32 to 69) | 0 | 1.9 | 50% | 0.22 | -22 | 75% |
| Business AR | 5 | 82 (77 to 91) | 0.1 | 0.9 | 20% | 0.36 | -19 | 0% |
| Gaming EN | 7 | 25 (17 to 28) | 2.1 | 5.7 | 86% | 0.17 | -13 | 71% |
| Gaming AR | 7 | 42 (30 to 56) | 2.6 | 5.9 | 86% | 0.32 | -14 | 43% |
| Podcast EN | 7 | 28 (21 to 43) | 1.5 | 4.4 | 43% | 0.32 | -15 | 43% |
| Podcast AR | 5 | 25 (23 to 57) | 0 | 0 | 20% | 0.33 | -10 | 60% |
| **All** | 39 | **35.5 (25 to 64)** | 0.8 | 3.8 | 54% | 0.30 | -14.7 | 51% |
*quiet-floor 0.25 or more of the median level. Full numbers (quartiles, shares) in `summary-v2.json`.

**Duration.** 41 percent are 30 s or shorter, 31 percent 30 to 60 s, 28 percent over 60 s. Gaming EN is the shortest (86 percent at 30 s or under); business is longest (54 percent over 60 s; Arabic business median 82 s, driven by Thmanyah-style clips).

**Captions.**
- 67 percent of clips burn in spoken captions that change every 0.5 to 1 s (median 1.2 changes per second, 2 to 3 words typical, 1 to 2 for the Arabic word-by-word style); another 15 percent carry only a static title card (Jynxzi style) and the rest have none or only baked-in text.
- **Position: median caption centre at 0.55 of the frame height (quartiles 0.49 to 0.63).** Only 3 percent sit in the bottom 22 percent where the platform buttons and title live. Business EN 0.55, gaming EN 0.44 (title cards near 0.32), podcast EN 0.57, Arabic 0.60 to 0.63.
- **Size: line height about 3 percent of frame height (quartiles 2 to 4)**, Arabic and one-word styles 4 to 6 percent.
- **Highlight:** about a third to a half use an accent on the active or key word (yellow or green or cyan, a bold weight, or a white box around the keyword in Thmanyah); the rest are plain white with a soft shadow (Codie Sanchez, Theo Von). No heavy multi-colour karaoke. Arabic word-by-word channels use one big word at a time with a yellow active word.
- **Our current default puts the caption centre near 0.80 (bottom aligned, 16 percent margin), which is where the market does not put it.**

**Cut pace and zoom.** Median shot about 2 s (all cuts). Gaming 1.0 s (5.7 to 5.9 cuts per 10 s), EN podcast 1.8 s, business EN 2.7 s, Arabic business and podcast slow (1 or less per 10 s, three of five Arabic podcast clips are one take). Hard-cut-free clips: 31 percent overall, so 7 in 10 use real cuts; punch-in or punch-out steps are present in most talk clips (median 4 per minute; gaming EN 12 per minute, inflated by multiple faces). Smooth zooms are rare (median 0 per minute).

**Hook (first second).**
- Hard cut inside the first second: almost none (median 0). Inside 3 s: 54 percent, gaming 86 percent.
- A text overlay is already on screen at 0.5 s in 79 percent (business 100 percent) and a face in 95 percent; 82 percent open with sound at or above the median level (no silent lead-in).
- The recurring pattern is a **static title or question card in the first 1 to 3 s over a talking face** (Hormozi red box, Codie white box, Thmanyah question, Arabic big title, Jynxzi and Valuetainment quote boxes), then word captions.
- Overlays held for most of the clip (title top or upper third) in 38 percent of clips.

**Framing.** Seen on the sheets: full-frame vertical crop on the speaker (most Hormozi, Codie, Theo Von, Impaulsive, Thmanyah, Arabic motivation); a rounded card on black with a title above (Gary Vee); facecam stacked over gameplay or a reaction cam with a title box (Jynxzi); two-person stacked split with a green seam line (Impaulsive); a TV-in-a-room frame (one Arabic clip). Blurred 16:9 padding, the style study 1 saw in business, was not seen in the sheet rows I viewed (the closest is Gary Vee's rounded card on black).

**Audio.** Median -14.7 LUFS (quartiles -18.6 to -12.4); 46 percent louder than -14.5, 23 percent quieter than -20 (all English business: raw, unmastered). Gaming -13.7, podcast -14.2, Arabic motivation and challenge clips -9 to -14 and several exceed 0 dBTP (over-compressed, not worth copying). Our pipeline already targets -14 LUFS, TP -1.5. 46 percent end with a fade (Arabic business and podcast mostly; gaming 0 percent, loop-like). Music: about half have a steady bed, mostly business EN (75 percent) and gaming (57 percent).

**#ad.** 2 of 39 (both Jynxzi, 5 percent) carry `#ad` and it is in the **title** only; I saw no on-frame disclosure label in those frames. Our on-frame label (top corner) is stricter than the market and stays.

## Versus study 1 (what changed and why)
| Item | Study 1 (9 clips, 144p) | Study 2 (39 clips, 720p to 1080p) | Meaning |
|---|---|---|---|
| Podcast length | 111 to 172 s | median 28 s (EN 28, AR 25) | study 1 sampled over-length clips; shorter is the norm |
| Podcast cuts / 10 s | 0.6 to 1.0 | EN 4.4 (hard 1.5), AR 0 | EN podcast clips are punched up; the calm style held only for Arabic |
| Business cuts / 10 s | 3.4 to 3.7 | EN 1.9 (hard 0) | study 1 (long DOAC clips) paced faster than the clipper accounts here |
| Gaming cuts / 10 s | 6.3 to 12.1 | 5.7 to 5.9 | same direction, a bit calmer |
| Gaming length | 12 to 17 s | EN 25 s, AR 42 s | wider |
| Layout | stacked 3, blurred pad 3 | full frame dominant, blurred pad rare | study 1 layout calls were heuristic at 144p |
| Face width | 0.25 to 0.49 | 0.17 to 0.36 | different detector and resolution, not comparable |
| Loudness | -14 to -25 | -9.5 to -22, median -14.7 | confirms -14 as a safe target |
| Captions | not measured (unreliable at 144p) | centre 0.55, 3 percent line height, 2 to 3 words | new, and it contradicts our 0.80 default |
| Hook | none opened with more than 3 cuts in 3 s | confirmed (max 4); title card in first second | new |

## What to do next
Wire the caption position first (small change, large mismatch). The cut-cadence and layout switches need the render stage first. TikTok and Reels need a separate, owner-approved method (screen recording of the app or the platform's own API), not scraping.

## Videos measured
Views were taken from the channel listing at pick time ("k" = thousand). Hard cuts for landscape files come from the first analyser pass; "All cuts" is only shown for vertical clips.

| Video | Niche/lang | Channel | Views | Res | Len s | Hard cuts/10s | All cuts/10s* | LUFS | Layout note |
|---|---|---|---|---|---|---|---|---|---|
| [meQEXSrMkdE](https://www.youtube.com/shorts/meQEXSrMkdE) | business/ar | various | 22k | 1920x1080 (landscape, ref only) | 116.9 | 0.0 | - | -21.1 |  |
| [a3zn9T1TINk](https://www.youtube.com/shorts/a3zn9T1TINk) | business/ar | Thmanyah | 9k | 1080x1920 | 76.5 | 0.13 | 0.92 | -18.8 |  |
| [nXCCJkmntGU](https://www.youtube.com/shorts/nXCCJkmntGU) | business/ar | various | 8k | 1920x1080 (landscape, ref only) | 81.8 | 2.44 | - | -15.3 |  |
| [jM9Yfv-5Fxg](https://www.youtube.com/shorts/jM9Yfv-5Fxg) | business/ar | Thmanyah | 7k | 1080x1920 | 82.4 | 0.12 | 1.7 | -25.8 |  |
| [Ae_EHnpCvlY](https://www.youtube.com/shorts/Ae_EHnpCvlY) | business/ar | various | 7k | 1080x1080 (landscape, ref only) | 60.0 | 3.16 | - | -10.5 |  |
| [domDnAQ5a7Y](https://www.youtube.com/shorts/domDnAQ5a7Y) | business/ar | various | 4k | 1920x1080 (landscape, ref only) | 81.8 | 0.0 | - | -24.3 |  |
| [M_ociPHkC7U](https://www.youtube.com/shorts/M_ociPHkC7U) | business/ar | Khodr w Business | 3k | 1080x1920 | 123.2 | 0.16 | 0.16 | -20.4 |  |
| [GGptNaKL-xg](https://www.youtube.com/shorts/GGptNaKL-xg) | business/ar | Thmanyah | 3k | 1080x1920 | 53.4 | 0.37 | 3.75 | -16.4 |  |
| [-PH8CvgfJ6I](https://www.youtube.com/shorts/-PH8CvgfJ6I) | business/ar | Annajah | 1k | 1080x1920 | 91.2 | 0.11 | 0.11 | -11.7 |  |
| [h-KHWUq3B7I](https://www.youtube.com/shorts/h-KHWUq3B7I) | business/en | Valuetainment | 6.3M | 1920x1080 (landscape, ref only) | 100.1 | 2.2 | - | -18.1 |  |
| [SX1DoqqXk1g](https://www.youtube.com/shorts/SX1DoqqXk1g) | business/en | Codie Sanchez | 540k | 1080x1920 | 68.2 | 0.0 | 0.73 | -20.5 |  |
| [eXbtll8q7nA](https://www.youtube.com/shorts/eXbtll8q7nA) | business/en | Codie Sanchez | 125k | 1080x1920 | 70.1 | 2.0 | 3.85 | -25.3 |  |
| [Klcl8qEdMaw](https://www.youtube.com/shorts/Klcl8qEdMaw) | business/en | Y Combinator | 109k | 1080x1920 | 84.0 | 0.0 | 1.19 | -24.0 |  |
| [CEIXJWt1X-Q](https://www.youtube.com/shorts/CEIXJWt1X-Q) | business/en | Alex Hormozi | 45k | 1080x1920 | 32.4 | 0.0 | 0.62 | -24.7 |  |
| [Sdi8C56nuq8](https://www.youtube.com/shorts/Sdi8C56nuq8) | business/en | Alex Hormozi | 44k | 720x1280 | 46.9 | 3.84 | 5.33 | -14.9 |  |
| [NBwqKG0odHM](https://www.youtube.com/shorts/NBwqKG0odHM) | business/en | Alex Hormozi | 32k | 1080x1920 | 35.5 | 0.0 | 0.56 | -25.3 |  |
| [I7CGIlUVB4U](https://www.youtube.com/shorts/I7CGIlUVB4U) | business/en | Gary Vee | 31k | 1080x1920 | 30.6 | 0.33 | 2.61 | -14.7 |  |
| [gLrN8g1CUsE](https://www.youtube.com/shorts/gLrN8g1CUsE) | business/en | Gary Vee | 25k | 1080x1920 | 29.5 | 0.0 | 3.05 | -12.3 |  |
| [HRRyecpeEw4](https://www.youtube.com/shorts/HRRyecpeEw4) | gaming/ar | Abo Flah | 34.3M | 1080x1920 | 68.3 | 2.19 | 7.75 | -14.8 |  |
| [VNItgnBIqBQ](https://www.youtube.com/shorts/VNItgnBIqBQ) | gaming/ar | Abo Flah | 11.0M | 1080x1920 | 41.8 | 1.2 | 7.18 | -14.3 |  |
| [YUOAyDn-kOs](https://www.youtube.com/shorts/YUOAyDn-kOs) | gaming/ar | Abo Flah | 4.4M | 1080x1920 | 67.1 | 1.34 | 5.67 | -20.3 |  |
| [5RwtTm6iyBc](https://www.youtube.com/shorts/5RwtTm6iyBc) | gaming/ar | SA7RAWI | 1.9M | 1080x1906 | 28.6 | 4.2 | 4.2 | -3.4 |  |
| [SwdeR-IpqKc](https://www.youtube.com/shorts/SwdeR-IpqKc) | gaming/ar | Iraqi | 1.5M | 1548x1080 (landscape, ref only) | 27.5 | 9.81 | - | -13.8 |  |
| [vIIiZHbvGtk](https://www.youtube.com/shorts/vIIiZHbvGtk) | gaming/ar | SA7RAWI | 1.4M | 1080x1906 | 27.2 | 5.52 | 5.88 | -3.3 |  |
| [S8PkgiZv6tY](https://www.youtube.com/shorts/S8PkgiZv6tY) | gaming/ar | SA7RAWI | 971k | 1080x1906 | 30.9 | 2.59 | 3.57 | -9.1 |  |
| [uoztYPsHOFA](https://www.youtube.com/shorts/uoztYPsHOFA) | gaming/ar | EVOO YT | 320k | 720x1280 | 44.3 | 5.87 | 6.32 | -18.0 |  |
| [nDxYSoFZxAM](https://www.youtube.com/shorts/nDxYSoFZxAM) | gaming/en | IShowSpeed | 11.1M | 1080x1920 | 25.1 | 2.79 | 9.58 | -14.2 |  |
| [Ys-CZeXgE1A](https://www.youtube.com/shorts/Ys-CZeXgE1A) | gaming/en | Jynxzi | 6.7M | 720x1280 | 25.1 | 0.0 | 8.37 | -8.7 |  |
| [lNFwWcVl3Ng](https://www.youtube.com/shorts/lNFwWcVl3Ng) | gaming/en | Jynxzi | 5.4M | 720x1280 | 19.6 | 0.0 | 2.04 | -25.5 |  |
| [Gmq8L3Xa4jM](https://www.youtube.com/shorts/Gmq8L3Xa4jM) | gaming/en | IShowSpeed | 4.3M | 1080x1920 | 14.7 | 2.05 | 8.19 | -12.0 |  |
| [H2N0eHGOi_w](https://www.youtube.com/shorts/H2N0eHGOi_w) | gaming/en | ClownY | 1.6M | 1920x1080 (landscape, ref only) | 17.9 | 7.83 | - | -16.7 |  |
| [OmHxUzfKnlM](https://www.youtube.com/shorts/OmHxUzfKnlM) | gaming/en | Jynxzi | 1.2M | 720x1280 | 30.0 | 2.67 | 5.67 | -12.5 | #ad in title |
| [gQzxzAIBh1c](https://www.youtube.com/shorts/gQzxzAIBh1c) | gaming/en | Jynxzi | 867k | 720x1280 | 59.5 | 0.67 | 5.55 | -15.1 | #ad in title |
| [8YOUgppbwT0](https://www.youtube.com/shorts/8YOUgppbwT0) | gaming/en | Valkyrae | 153k | 1080x1920 | 12.8 | 2.34 | 5.46 | -13.1 |  |
| [CiI_PxJhhMk](https://www.youtube.com/shorts/CiI_PxJhhMk) | gaming/en | Jett Shorts | 81k | 1920x1080 (landscape, ref only) | 37.8 | 3.44 | - | -13.8 |  |
| [-TpNq6Ns8CY](https://www.youtube.com/shorts/-TpNq6Ns8CY) | podcast/ar | Rami Abdulhai | 1.9M | 1920x1080 (landscape, ref only) | 27.6 | 0.36 | - | -13.1 |  |
| [6DeKehm7m0s](https://www.youtube.com/shorts/6DeKehm7m0s) | podcast/ar | Mohamed Fares | 209k | 1280x720 (landscape, ref only) | 49.5 | 4.04 | - | -17.6 |  |
| [L4J96uItQsM](https://www.youtube.com/shorts/L4J96uItQsM) | podcast/ar | Arab Motivation | 67k | 1920x1080 (landscape, ref only) | 38.6 | 0.78 | - | -15.9 |  |
| [23S0m0Ctz1M](https://www.youtube.com/shorts/23S0m0Ctz1M) | podcast/ar | Arab Motivation | 50k | 1920x1080 (landscape, ref only) | 53.7 | 0.0 | - | -16.3 |  |
| [PanRm8hWqwo](https://www.youtube.com/shorts/PanRm8hWqwo) | podcast/ar | Arab Motivation | 38k | 1080x1920 | 15.8 | 0.0 | 1.26 | -9.2 |  |
| [_1OKAw6S3Go](https://www.youtube.com/shorts/_1OKAw6S3Go) | podcast/ar | Arab Motivation | 14k | 1080x1920 | 23.2 | 0.0 | 0.0 | -5.8 |  |
| [sPBh9OA9smQ](https://www.youtube.com/shorts/sPBh9OA9smQ) | podcast/ar | BrightSpeech | 12k | 720x1280 | 57.2 | 0.0 | 1.75 | -15.2 |  |
| [yyQ4flItbdM](https://www.youtube.com/shorts/yyQ4flItbdM) | podcast/ar | Arab Motivation | 10k | 1920x1080 (landscape, ref only) | 39.0 | 0.77 | - | -15.3 |  |
| [DmOxX3Dm5uk](https://www.youtube.com/shorts/DmOxX3Dm5uk) | podcast/ar | BrightSpeech | 6k | 1080x1920 | 24.7 | 0.0 | 0.0 | -9.5 |  |
| [tsszW2p3mcE](https://www.youtube.com/shorts/tsszW2p3mcE) | podcast/ar | Kian | 2k | 720x1280 | 90.8 | 0.0 | 0.0 | -14.1 |  |
| [anyN8lj51Ds](https://www.youtube.com/shorts/anyN8lj51Ds) | podcast/en | Lex Clips | 493k | 1920x1080 (landscape, ref only) | 111.9 | 0.27 | - | -16.4 |  |
| [xLVRN7kB8QA](https://www.youtube.com/shorts/xLVRN7kB8QA) | podcast/en | Theo Von | 288k | 1080x1920 | 45.3 | 1.54 | 1.54 | -15.1 |  |
| [xL2xzf3AuGw](https://www.youtube.com/shorts/xL2xzf3AuGw) | podcast/en | Theo Von | 209k | 1080x1920 | 39.9 | 2.76 | 3.26 | -18.3 |  |
| [VrAhVJgTa7s](https://www.youtube.com/shorts/VrAhVJgTa7s) | podcast/en | Theo Von | 164k | 1080x1920 | 61.2 | 0.82 | 4.41 | -14.2 |  |
| [bSJv10gjKvU](https://www.youtube.com/shorts/bSJv10gjKvU) | podcast/en | Impaulsive | 43k | 1080x1920 | 22.5 | 0.89 | 4.45 | -14.6 |  |
| [xP7Ja1GXqfM](https://www.youtube.com/shorts/xP7Ja1GXqfM) | podcast/en | Impaulsive | 39k | 1080x1920 | 13.7 | 1.47 | 5.13 | -13.9 |  |
| [tD7GUPyLwW8](https://www.youtube.com/shorts/tD7GUPyLwW8) | podcast/en | Valuetainment/PBD | 37k | 1080x1920 | 28.4 | 2.11 | 4.58 | -16.1 |  |
| [k_9i4yxC5Gc](https://www.youtube.com/shorts/k_9i4yxC5Gc) | podcast/en | Impaulsive | 35k | 1080x1920 | 18.6 | 1.62 | 4.31 | -13.7 |  |

*All cuts = hard cuts plus punch-in steps (upper bound). Contact sheets: `docs/market-style/v2/<niche>_<lang>_<n>.jpg` (frames at 0, 0.5, 1.5, 3 s, middle, last second).
