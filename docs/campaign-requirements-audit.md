# Campaign requirements audit (real briefs, 2026-11-24)

Source: four real briefs the owner pasted (Clipster: CSGOBIG [CS2], VAVADA [GENERAL], Castle [RUNESCAPE + GAMBA] 3; Reach: SocialMediaJustice).
The Topps x ClipFarm Google doc needs a sign-in and could not be read; the Clipster logo guidelines (Notion) render only in a browser, so their text is still missing.
"Supported?" is from reading the code and docs today; items marked (verify) must be confirmed against the running system before building.

| # | Requirement seen in a real brief | Seen in | Supported today? | Needed |
|---|---|---|---|---|
| 1 | ADD PROVIDED LOGO (file on Google Drive / Frame.io, sometimes video on green background) | CSGOBIG, VAVADA, Castle | No | Brand assets: upload, chroma key, placement, required flag |
| 2 | Logo placement rule ("only on the top half of the screen", "follow the logo guidelines") | VAVADA, Castle, CSGOBIG | No | Placement presets + per-campaign constraint; guidelines text must be pasted by the owner |
| 3 | Logos of ANY kind are NOT allowed | Reach SMJ | No (only "do not remove platform watermark" exists) | Rule type "logo forbidden": gate blocks a clip that has an overlay |
| 4 | Minimum clip length (8 s, 10 s) | VAVADA, CSGOBIG | Yes (verify the per-campaign value is read from the brief) | |
| 5 | Minimum engagement rate (0.3 %) | VAVADA | No | Track engagement per post, alert when under |
| 6 | Language/audience: English, "T1 audience", >= 50 % native English countries, no SEA / T3 focus | Castle, Reach, VAVADA | Partly (account audience tier exists) (verify) | Per-campaign audience rule checked against the account |
| 7 | Required hashtag in caption (#SocialMediaJustice) | Reach SMJ | Likely yes via `hashtags` in rules (verify) | |
| 8 | Account bio must carry the website link | Reach SMJ | No | Account checklist item, warn before posting |
| 9 | Branded page only ("CS2 BRANDED PAGES") | CSGOBIG | No | Account type/niche requirement checked before assigning a clip |
| 10 | Submit each clip on the marketplace within 30 minutes of posting; duplicate URLs rejected | Reach SMJ | No | Post-posting reminder with a deadline timer, then confirm submitted |
| 11 | Payout facts: CPM $0.2 to $2 per 1K, minimum views to qualify (5,000), per-platform min/max, per-creator cap ($2,500 / $15,000), campaign budget used (58 % / 93 % / 1 %) | all | Partly (rate, min views, per-clip max, budget left) (verify per-creator cap and per-platform split) | Per-creator cap tracking; effective rate after caps |
| 12 | Content topics and brand safety: viral clips of sports/movies/streamers, gambling streamers ("GAMBA"), crypto streamers, "young audience suitable", no AI-generated content, no rage-bait | VAVADA, Castle, Reach | Partly (compliance rules exist) | Gambling/crypto content flag with platform-policy warning; "no AI-generated content" check on what we generate |
| 13 | Strike policy ("one strike and you are out") | Reach SMJ | n/a | Raise the gate strictness for campaigns with a ban policy |
| 14 | Source permission for "any viral clips" (movies, sports): the brief grants it | VAVADA | Permission reference field exists | Let the campaign brief itself count as the permission reference |

Open questions for the owner: the Topps x ClipFarm brief text; the logo guidelines text and the logo files (Drive / Frame.io links need the owner's access).

## Added after reading the Topps x ClipFarm brief and the Clipster logo guidelines (opened in the owner's Chrome)

Topps x ClipFarm (Content Rewards payout: $1.00 per 1K views, $400 max per clip, $1 minimum, 40%+ US audience, YT/IG/TT):

| # | Requirement | Supported today? | Needed |
|---|---|---|---|
| 15 | NEVER put #ad or any ad-disclosure tag (the system's default is to add one) | Per-campaign switch exists (owner note 2); brief extraction must set it | Detect "no #ad" in the brief, turn the label off, and show the owner the decision (the system flags, the owner decides) |
| 16 | Source whitelist: clip ONLY from the listed content bank (named YouTube videos, Instagram pulls, Fanatics Fest); no broadcast footage (ESPN/NBA) | No (any file or link is accepted if a permission reference is typed) | Per-campaign allowed-source list; block a source outside it; block "broadcast" categories |
| 17 | Dedicated niche pages only (sports card / trading card pages) | No | Account niche match before assigning a clip |
| 18 | Hook rule: story first, the price tag is never the headline | No (the hook generator knows nothing about campaign hook rules) | Campaign hook constraints passed to the hook agent and checked after |
| 19 | Edit style asks: hook in 2 s, reaction/split screen, big captions naming the card, 9:16, no dead air, every clip different | Partly (hook, vertical, tightening exist; split screen, per-clip variation check do not) | Variation check across clips of one source; "caption names the subject" check |
| 20 | Deadline for a content type ("no more skull edits after 8/4 12:59PM EST") | No | Campaign dates with a countdown and an automatic stop |
| 21 | Priority drops ("hero" content to cut first) | No | Source priority flag per campaign |
| 22 | Rejection reasons list (broadcast footage, outside the bank, price-tag-only hook, lazy captions, repost spam, boosted views) | Partly (gate has six checks) | Turn each listed rejection reason into a gate check |

Clipster logo guidelines (apply to every Clipster logo campaign unless the brief says otherwise):

| # | Requirement |
|---|---|
| 23 | The provided logo file must NOT be altered: no stretching, distortion, recolouring or modification |
| 24 | Two logo types: static PNG and animated (green or blue screen: remove the background) |
| 25 | Static PNG: visible for the entire video. Animated: plays start to finish without interruption, LOOPS, at normal speed (no speed-up, slow-down or cuts), 7 to 25+ seconds per play |
| 26 | The logo must be clearly visible at all times and never covered more than 50 % by platform UI (captions, username, like/share buttons) or other graphics, effects or objects |
| 27 | An "ideal placement" is shown in the guidelines (an image, not text): must be read from the picture |
| 28 | Every campaign also has engagement requirements for approval |

Consequences for the design of brand assets: tightening and jump cuts must not cut the logo (the logo layer sits above the tightened timeline and is not retimed); caption and hook placement must leave the logo area free; the check for "covered more than 50 %" is a geometric test of the logo box against the caption, hook title and platform UI zones.
