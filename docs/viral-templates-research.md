# Viral hooks and caption templates: research (2026-11-24)

Goal (principle 8): hooks and caption/edit looks that match what top short-form editors ship, reusing what exists. Sources were read on
2026-11-24. Where the evidence is thin or comes from vendors, it says so. Nothing here is a view-count guarantee; the measured part is in
`docs/bakeoff/hooks-viral/summary.md` and `docs/bakeoff/templates-2/`.

## A. Hooks

### What the sources say works now
| Finding | Source | Strength |
|---|---|---|
| The best hook type shows the finished result in the first 2 seconds ("product/outcome"), about 6,000 views average, roughly double the lowest type; "story setup" openers ("let me tell you about the time I...") are the weakest (34,635 clips) | [OpusClip, 15 TikTok hooks 2026](https://www.opus.pro/blog/tiktok-hooks-hijacking-algorithm-2026), [anatomy of a viral TikTok, 13.5M clips](https://www.opus.pro/blog/anatomy-of-a-viral-tiktok-2026) | vendor data, large sample, one vendor |
| Text trends like `POV:` and `tell me why` lost effectiveness in 2025 because viewers learned to skip them | [Reddit creators synthesis](https://wildandfreetools.com/blog/best-tiktok-hooks-reddit-2026/) | anecdote, not measured |
| `Nobody:` and `Me when` appear in template lists, with no performance data | [DEV 50 fill-in-the-blank hooks](https://dev.to/wedgemethoddev/free-viral-video-hooks-50-fill-in-the-blank-templates-2026-download-1ngg), [PostIgniter 25 openers](https://postigniter.com/blog/tiktok-hook-templates-25-viral-openers-for-every-niche-in-2026), [BlitzCut 12 hook types](https://blitzcutai.com/blog/tiktok-hook-types) | listicles |
| Numbered, curiosity-gap, contradiction, challenge, reaction hooks are the standard families in every guide | same listicles plus [Socialync 64 hooks](https://www.socialync.io/viral-hooks-library), [DEV 25 formulas](https://dev.to/wedgemethoddev/tiktok-hook-formulas-for-more-views-25-proven-patterns-that-stop-the-scroll-1ifn) | consistent across guides |
| Hook window: 2.4 s to 7 s depending on the source; our 3 to 5 s on screen sits inside it | [first 3 seconds](https://www.opus.pro/blog/viral-tiktoks-first-3-seconds-2026) | sources disagree |

**What we do with it:** the owner asked for the trend tags, so `POV:`, `Nobody:`, `Me when:` and friends are in, but at a moderate starting
weight (0.8 to 0.9) and with an `expires` date (2027-03-01) so a stale trend drops out; result-first, curiosity gap and contradiction
start highest (1.1 to 1.2). The owner's own results (`hook_pattern_stats`) and the thumbs in the hook editor move every weight from there.
That is the honest answer to "trend or no trend": nobody has measured it, so the tool measures it per owner.

### Arabic (Levantine / Jordanian)
Searches found no data-backed guide for Arabic hook structure; the useful points are generic: write the voice in dialect, not MSA (MSA is
fine for subtitles but stiff when spoken), define the dialect per video ([Translate AIR localisation guide](https://air.io/en/audience-growth/cultural-adaptation-in-video-localization-dos-and-donts-in-the-middle-east),
[Coinis TikTok ad guide](https://coinis.com/how-to/translate-tiktok-ad-to-arabic); its dialect-to-country labels contain an error, so only the MSA-versus-dialect
advice is used). The Arabic formats in the lexicon therefore come from translating the proven families into spoken Jordanian
(`ما حدا:`, `أنا لما:`, `استنى لآخر ثانية`, `يا زلمة شو هاد`, `اسمعني شوي`) and are marked for the owner to rate. Expect to re-weight these
from real thumbs. Limit: no native-speaker panel was available, so the Arabic win rates in the bake-off are an LLM judge's view.

### Datasets and swipe files
| Item | Licence / status | Decision |
|---|---|---|
| [HF benxh/tiktok-hooks-finetune](https://huggingface.co/datasets/benxh/tiktok-hooks-finetune) (10K to 100K rows, README says MIT) | MIT on the card; scraped TikTok text, rights of the underlying posts unclear | style reference only, no rows copied (earlier decision stands) |
| [HF Gopher-Lab TikTok samples](https://huggingface.co/datasets/Gopher-Lab/TikTok_Hottest_Video_Transcript_Example/blob/e2214781f26d2d2706e25102358be419423d5f66/README.md) | MIT, small promo samples | not used |
| 5.6 billion TikTok metadata dump ([news](https://decrypt.co/380229)) | CC BY-NC 4.0, scraped against platform terms | not used (non-commercial, terms) |
| Creator swipe files (DEV, PostIgniter, BlitzCut, Socialync, Reddit synthesis above) | blog text | read for format names and phrasing only; the lexicon examples are our own sentences |

### AI-sounding habits to avoid (rejected in code)
Words and phrases from [Grammarly](https://www.grammarly.com/blog/ai-writing/common-ai-words/), [Substack "delve"](https://ruben.substack.com/p/delve),
[Medium AI-word lists](https://medium.com/@ThirtySeven37/common-ai-words-and-phrases-that-trigger-detectors-and-how-to-fix-them-0764fd47d7d1): unlock, game-changer,
dive into, delve, leverage, tapestry, realm, journey, navigate, elevate, supercharge, "here's why", "you won't believe", "in this video",
"in today's", "it's not about X". Em and en dashes are already banned by `hook_rules`. Structural tells (rule of three, tidy short sentences) are
handled by the prompt and the rubric, not by a word list. These lists are heuristics (the sources say so themselves, there is no reliable detector). Arabic equivalents
(اكتشف، أطلق العنان، في هذا الفيديو، دعونا، إليك، سر النجاح، هل تعلم) are in the same data file.

### Trending now (dated, expires 2027-03-01)
`POV:`, `Nobody:`, `Me when:`, "tell me you ... without telling me", "not me ...", `ما حدا:`, `أنا لما:`. These are the entries with an
`expires` date in `backend/clipping/assets/hook-lexicon.json`. After that date they stop being offered until someone reviews and renews them.
Evergreen (no expiry): numbered, curiosity gap, contradiction, challenge, reaction, outcome first, wait for it, "he did NOT just", "this is your sign", "hear me out", "stop doing".

## B. Caption and edit templates

### Candidates
| Candidate | Stars / last commit / licence | What it is | Fit with our one-FFmpeg libass pipeline |
|---|---|---|---|
| [francozanardi/pycaps](https://github.com/francozanardi/pycaps) | 235 / 2026-09-30 / MIT, 0 open issues | CSS-driven animated captions; 11 presets (classic, default, explosive, fast, hype, line-focus, minimalist, model, neo-minimal, retro-gaming, vibrant, word-focus); sibling [tscaps](https://github.com/francozanardi/tscaps) in the browser | **Looks: yes. Engine: no.** It renders frames in Chromium (Playwright) or Pictex, a second render pass and a heavy dependency; our rule is one encode with libass. We take the look specs of `hype` (yellow spoken word, thick black outline), `word-focus` (capitals, spoken word on an orange box) and `vibrant` (unspoken words faint) as data. No code copied |
| [remotion-dev/template-tiktok](https://github.com/remotion-dev/template-tiktok) | 308 / 2026-11-23 / no licence file (Remotion core: [source-available licence](https://github.com/remotion-dev/remotion), 75k stars) | React/Remotion TikTok captions from whisper.cpp | Needs Node, Chromium and a React render; best-in-class animation but a second pipeline. Rejected for the pipeline, kept as a visual reference |
| [Aegisub](https://github.com/Aegisub/Aegisub) | 3.4k / 2025-01-01 / see repo | The ASS standard editor; documents the tags we use (`\bord`, `\3c`, `\1a`, `\t`) | It is the spec for our engine, not a dependency |
| [m-bain/whisperX](https://github.com/m-bain/whisperX), [whisper-timestamped](https://github.com/linto-ai/whisper-timestamped) | 25.6k BSD-2; 2.9k AGPL | word timestamps | we already have local faster-whisper word times; no gain |
| [Anil-matcha/AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator), [mutonby/openshorts](https://github.com/mutonby/openshorts) | 6.5k MIT; 9.8k MIT, both active Nov 2026 | open-source OpusClip-style clippers with captions | whole-pipeline replacements; their caption layer is the same ASS/Pillow word-highlight idea we have |
| `pulsence/caption-animator` | **not found on GitHub today (404)** | cited in an earlier code comment | the comment in `caption_render.py` is corrected: the animation ideas are common ASS practice, not that repo |
| Submagic, OpusClip, CapCut presets | commercial | Submagic: Bold, Clean, Highlight, Backdrop, Minimal, Karaoke, animated emoji ([Unite.AI](https://www.unite.ai/submagic-review), [Filmora review](https://filmora.wondershare.com/video-editor-review/submagic-review.html)); OpusClip: keyword highlight, closest to Submagic ([HeyGen comparison](https://www.heygen.com/blog/submagic-alternatives)) | the looks are the target; their engines are not reusable |

### What real users say (honest limits)
No independent retention data compares caption styles; the comparison pages are vendor-run. User reviews on
[G2](https://www.g2.com/products/Submagic/reviews), [Trustpilot](https://www.trustpilot.com/review/submagic.co?page=1) and [Product Hunt](https://www.producthunt.com/products/submagic/reviews)
praise fast, accurate captions and quick support; the one-star reviews are about billing, cancellation and credits, and a Reddit summary
mentions bugs, export problems and inconsistent clipping ([Zemith review](https://www.zemith.com/en/learn/submagic-review)). I found no Reddit thread
that calls the default presets "generic" or rates emoji captions; so "which look wins" is decided by our own side-by-side, not by reviews.

### What top editors do, and what our pipeline can do in one encode
| Element | Top editors | Ours today | Note |
|---|---|---|---|
| Spoken-word highlight colour | everywhere | yes (6 styles) | |
| Box behind the spoken word | Submagic "Backdrop", pycaps word-focus | **added** (`word_box`) | thick outline in box colour, one pass |
| Faint unspoken words / fill-in | pycaps vibrant, Submagic karaoke | **added** (`dim_ahead`) | alpha tag |
| Big capitals, 2 words, yellow, heavy outline | Hormozi/MrBeast look, pycaps hype | **added** (`hype`) | Arabic is never upper-cased |
| Pop-in | common | yes | |
| Speaker name labels | podcast clippers | yes | real names only |
| Hook title card in the first seconds | all top clippers | yes (3 to 5 s) | |
| Progress bar | common | yes (Bar style) | |
| Emoji on key words | Submagic, pycaps `emoji-for-segment` | no | libass has no colour-emoji font in our build; would need a second layer or overlay pass, so it stays out until a measured need |
| Zoom punches, B-roll cuts, SFX | Submagic, CapCut | partly (cuts and music exist; zoom punch is a reframing feature) | not caption data; not in this change |

### Ranking by pipeline fit
1. ASS looks as data in `caption_render.STYLES` (hype, word_box, dim_ahead): same encode, no new dependency. **Adopt if they win the side-by-side.**
2. pycaps presets as a visual reference for more looks later (MIT, active).
3. Remotion / pycaps as engines: rejected (second render pass, Chromium).
4. Emoji layer: deferred.
