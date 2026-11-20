# Music bed bake-off (decision 32, CC0 only)

Owner rule (2026-11-20): CC0 / no-attribution tracks only, and music that suits short-form feeds. All 37 catalog tracks are by HoliznaCC0 on Free Music Archive; each track's own page was checked live for the CC0 1.0 licence link and the "Instrumental" genre. The earlier CC BY set (Kevin MacLeod) is gone.

**Fit criteria** (listed in the catalog under `fit_criteria`, with a `fit_why` line per track): instrumental; clear pulse (3.4+ onsets/s) and tempo 70-135 BPM, matched to the clip's energy and cut pace (half and double time count as a match); steady level (swing 17 dB or less) so ducking follows speech; the bed starts on a bar line after the measured intro, so the first 1-2 s are the track at full strength; tagged per niche and mood; never the same track on two clips in a row. Refused on measurement: Bouncing (under 90 s), Fresh Fit (weak pulse), Sky Scrapers (tempo), ONLY HUMAN (level swings 167 dB), Doodles, Mutant Club, Side Quest to Nowhere (no Instrumental tag).

**Selector score** = energy closeness (energy comes from the speech rate, loudness spread, laughter and cuts per 10 s) + tempo fit to the wanted BPM + mood match + niche priority - a small cost for a long intro. Podcasts get no bed when speech fills more than 62 % of the clip.

**Mix settings by measurement** (real speech, 24 s of `../interview_en_new_yunet.mp4`, TikTok -14 LUFS; 12 settings on reference track `Morning Coffee`): feasible = voice 18 dB or more above the bed while speaking, 10-26 dB above it in the pauses, final loudness within 1 LU, peak under -1 dBFS. Winner: bed at -22 LUFS, ratio 14, threshold 0.06, release 350 ms: 25.9 dB speech-to-music while speaking, 10.0 dB in the pauses, ducking depth 16 dB, final -14.1 LUFS, peak -1.5 dBFS.

**Per-track check under that mix:** all 37 tracks keep the voice 20.8 dB or more above the bed while speaking (`report.json`).

**Proof clips:** `before_no_music.mp4` (voice only) and `after_with_music.mp4` (`Still Life`, picked by the selector), 24 s, one encode each.

**Where the free catalog is thin:** one artist only; no current pop/EDM-style or bright corporate beds, nothing regional or Arabic in feel; business has 4 high-energy tracks, podcasts none; only gaming has tense or mysterious beds. "Sounds current on TikTok or Instagram" is the curator's judgement, not a measurement. A paid library with trend tags would cover these gaps: the owner decides later.

**Limits:** the proxies are numbers, not ears: listen to the proof clips once. One speech source; game audio in a gaming clip is not ducked separately. Platform loudness (-14 LUFS TikTok/Instagram/YouTube, -16 Facebook) is an assumption.
