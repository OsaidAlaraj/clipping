# Market style study, first batch (2026-11-19)

Method: top-viewed Shorts from clipper or podcast channels found by search metadata (not memory), downloaded anonymously (no cookies), one every 30 s, measured by script, media deleted afterwards. Manifest: `.data/benchmark/manifest.json`. Numbers: `docs/market-style/measurements.json`; one contact sheet per niche in `docs/market-style/` (frames at 0, 1, 2, 3, 5 s and the last second).

**Limits, read first.** YouTube gave anonymous clients only **144p video** (the 360p formats were not offered), so caption size and fine layout are rough. The sheets were **not eyeballed**: the layout, caption position and punch-in numbers are heuristics and can be wrong (caption position is unreliable). Several "Shorts" are 90 to 170 s long. One podcast clip (CNN) failed to merge and is not measured: 9 of 10 measured. Samples are tiny: these are patterns to test, not rules.

## Videos (views at pick time)
- Gaming: Kai Cenat Live: [4o3N0dqeoxU](https://www.youtube.com/shorts/4o3N0dqeoxU) 4.5M, [6rsPRMXT09A](https://www.youtube.com/shorts/6rsPRMXT09A) 2.2M, [PEOl5luGQrs](https://www.youtube.com/shorts/PEOl5luGQrs) 1.6M
- Podcast and interview: Lex Fridman [IbEUbykf9zw](https://www.youtube.com/shorts/IbEUbykf9zw) 1.4M; Louisiana Channel [G2PLKDNEmWk](https://www.youtube.com/shorts/G2PLKDNEmWk) 170k; CNN [k8s6aiwkr78](https://www.youtube.com/shorts/k8s6aiwkr78) 448k (not measured)
- Business: The Diary Of A CEO [jQvw3PDo1Do](https://www.youtube.com/shorts/jQvw3PDo1Do) 1.7M, [vYi8WGdI_s0](https://www.youtube.com/shorts/vYi8WGdI_s0) 1.5M
- Arabic: Mahfouf [vSuIFQ4fqBY](https://www.youtube.com/shorts/vSuIFQ4fqBY) 276k (Gulf channel, English title), Thmanyah [581smMkWj34](https://www.youtube.com/shorts/581smMkWj34) 180k (Saudi)

## Measured numbers (per niche, range over measured clips)
| Niche | Length s | Cuts per 10 s | Cuts in first 3 s | Layout (heuristic) | Face width share | Loudness LUFS | Music floor* | Ends fading |
|---|---|---|---|---|---|---|---|---|
| Gaming (3) | 12 to 17 | 6.3 to 12.1 | 1 to 3 | stacked (2), blurred pad (1) | 0.25 to 0.26 | -15 to -22 | 0.5 to 0.7 | no |
| Podcast (2) | 111 to 172 | 0.6 to 1.0 | 0 | stacked or split (2) | 0.47 | -23 to -24 | 0.06 to 0.08 | yes |
| Business (2) | 94 to 98 | 3.4 to 3.7 | 0 to 2 | blurred pad (2) | 0.45 to 0.49 | -17 to -25 | 0.35 to 0.42 | mixed |
| Arabic (2) | 52 to 107 | 1.3 to 1.9 | 0 | full frame, stacked | 0.42 to 0.43 | -14 to -16 | 0 to 0.07 | yes |

*Music floor: quiet-moment energy as a share of the median; high means a steady bed (music or game audio).

## What the market does (patterns to test)
**Gaming:** very short (12 to 17 s); a cut every 1 to 1.5 s; a cut or two inside the first 3 s; small face-cam (about a quarter of the width) with the gameplay shown; loud and dense audio bed; ends without a fade (loop-like).
**Podcast and interview:** long (2 to 3 min on these); calm, under 1 cut per 10 s; the face fills almost half the width; no early cut; quiet audio floor, so speech only; ends by fading out.
**Business:** about 95 s; a cut every 3 s; 16:9 picture centred on a blurred 9:16 pad; face about half the width; a steady music bed in the pauses.
**Arabic:** 50 to 100 s; slow cuts (about one per 6 s); one speaker with a large face; louder than the others (about -15 LUFS); fades out at the end.
**Across niches:** none of the nine opened with more than 3 cuts in 3 s; the face is large in every talking niche; everything except gaming is slower than our current default cadence.

## Proposed defaults
`docs/market-style/render-profile-defaults.json`: caption position, cut cadence, hook duration, layout per niche. **Not wired into the render stage**: the render has no per-niche cut-cadence or layout switch yet (stacked layout is not built), so wiring is more than a small change. Re-measure at 360p or higher before trusting any number.

## What blocked
Anonymous YouTube access worked, but only at 144p. Higher quality likely needs a JavaScript runtime or a login, which are off limits.
