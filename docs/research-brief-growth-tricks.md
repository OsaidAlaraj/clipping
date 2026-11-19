# Research brief: reach and retention tricks for short clips (to run after the weekly usage reset)

Owner request (2026-11-19): read about ALL the tricks creators use, then fold the best into the system so it is really complete. Two examples the owner named:
- Publishing 4:5 (or 1:1) video centred on a 9:16 background (blurred, mirrored or coloured).
- Replacing or layering the background sound with another low-volume sound to help reach.

## Questions to answer, with evidence (not folklore)
1. **Layouts:** 4:5 or 1:1 on a 9:16 canvas (blurred or mirrored fill, colour, title bar), split screen with a second attention clip, face-cam plus gameplay stack, picture-in-picture. What do top clipping channels use per niche? Which layout do platforms favour?
2. **Audio:** a low-volume music bed under speech with ducking; in-app trending or licensed sounds (TikTok and Instagram sound libraries, YouTube Audio Library); sound effects on hooks; noise reduction; loudness targets per platform. What does evidence say about reach effect?
3. **Visual retention tricks:** punch-in zooms on emphasis, shake or speed ramps, progress bar, highlighted keywords, emoji captions, countdown, freeze frames, loop-friendly endings, pattern interrupts every N seconds.
4. **Hooks:** cold open on the strongest line, text hook, curiosity gap, first-frame rules, cover and thumbnail choice.
5. **Metadata and posting:** caption and title SEO per platform, hashtag counts, on-screen keywords, posting times by audience, series naming, pinned comment, reply-with-video, cross-post cadence.
6. **Platform signals:** what each of TikTok, Instagram Reels, YouTube Shorts and Facebook Reels measure (completion, rewatch, shares, saves, original-audio flag) and what they down-rank.
7. **Policy risk for clipping accounts (most important):** the platforms' current rules on unoriginal, reposted or "inauthentic" content, music and copyright claims, duplicate content across several accounts, and what monetisation or campaign payouts require. Which tricks are safe, which are grey, which are forbidden?

## Output
For each trick: what it is, evidence strength (study, platform doc, creator claim), expected effect, implementation cost in our pipeline (FFmpeg filter, adapter, template field), and risk (terms, copyright, campaign rules). End with a ranked shortlist to build, and a list of tricks rejected with the reason. Put it in `docs/growth-tricks.md`.

## Boundaries
- Don't build or recommend anything whose purpose is to evade copyright detection, spam or integrity systems, or to fake engagement. For audio, the safe version is a royalty-free or licensed bed with ducking, or a sound added inside the platform's own app on the manual path. Replacing a source's audio to dodge a copyright claim is out: it is unreliable, risks strikes, and is a form of evasion.
- Cite sources with links. Mark anything from memory as unverified.
- Keep it frugal: search wide, read selectively, and don't dump pages.
