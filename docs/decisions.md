## Decision 32 (2026-11-20): platforms, music, accounts, Copilot
- **Platforms:** TikTok and Instagram together from the start; YouTube and Facebook follow. Every platform in the code must be supported (all accounts, niches and languages), no hard-coding to one.
- **Music:** free sources only, but with paid-library quality and chosen smartly: tracks matched to niche, mood, tempo and the clip's energy; loudness and ducking tuned per platform; a per-clip licence log (source, licence, track, date). Free burned-in sources with clear commercial licences only (see `docs/research-verdicts.md`); platform libraries stay in-app on the manual path.
- **Copilot:** the owner allows using it for well-specified mechanical work (review, tests, docs, audits) on its own branches; its output is reviewed before merge.
- **main** is merged (8d7127d) and protected against force-push and deletion.

## Decision 32 follow-up (2026-11-20): music bed stage built, CC0 only
- **Owner rule:** CC0 / no-attribution tracks only (no credit lines in captions), and music that fits short-form feeds. Catalog: 37 instrumental tracks by HoliznaCC0 on Free Music Archive, licence and Instrumental tag checked per track page; files under `.data/music` (not in git); fit criteria and a `fit_why` per track are in `backend/clipping/assets/music-catalog.json`.
- **Selector and mix:** niche, mood, tempo vs the clip's energy and cut pace, no vocals, start past the intro on a bar line, no repeat on consecutive clips; speech-heavy podcasts get no bed. Sidechain ducking, -14 LUFS (TikTok, Instagram), peak limiter, inside the clip's one encode. Settings chosen by measurement (docs/bakeoff/final/music).
- **Thin spots (owner may buy a library later):** one artist, no current trending pop/EDM or corporate beds, nothing regional, few high-energy business beds.

## Decision 33 (2026-11-20): active-speaker crop (docs/bakeoff/speaker)
Strict bake-off on 16 real 30 s clips with labelled speakers (11 with people, 5 gaming face-cam), rubric fixed first (`docs/bakeoff/speaker/RUBRIC.md`), labels written before scoring, contact sheets inspected (`sheets/`).
- **Adopted: FabuLight-ASD, face-only weights**, exported to ONNX (4 MB, `assets/fabulight_asd_face.onnx`, onnxruntime on the CPU, no torch in the app). Speaker in the crop: 87 % of seconds on people clips vs 65 % for the old YuNet crop (+22 points, bootstrap 95 % CI +4.5 to +41); multi-person and listener-from-behind clips 94 % vs 72 %. Crop jitter 3.9 px vs 32 px, crop moves 15 vs 61 (15 true speaker changes). About 19 s per 30 s clip on the CPU, 1.0 M parameters. Code behind `faces.track` (falls back to the old path on any failure, clips over 90 s, no audio); `speaker.py` holds the pure choice logic, `ScriptedScorer` is the test fake.
- **Tied, not adopted: LR-ASD** (MIT, weights in repo, AVA-trained) and **ASDnB** (MIT, weights on Google Drive). With each model's own speaking threshold the three are within noise of each other on accuracy (88 %, 87 %, 89 %). FabuLight won the tie on speed (31 s of GPU for all 16 clips vs 87 s and 69 s), VRAM (164 vs 201 and 212 MB), no body-box detector needed (ASDnB needs a person detector; I used a crude face-derived body box, so ASDnB's number is a lower bound), and an exported ONNX that needs no torch. LR-ASD is the ready swap if the FabuLight weight terms are a problem.
- **Rejected: transcript-diarization heuristic** (Deepgram speaker labels mapped to face tracks by mouth-motion): +4 points, CI -8 to +18, i.e. no clear gain; it fails when the mouth proxy binds the wrong face (multi-person 24 % vs baseline 43 %). Binding labels to faces with the ASD scores instead (`diar+lr`) gave the same 24 % there, so the labels themselves add nothing over the ASD scores. Diarization stays useful for captions, not for the crop.
- **Weight and data terms:** all three code bases are MIT. FabuLight's weights were trained on WASD (terms not verified); LR-ASD and ASDnB on AVA-ActiveSpeaker (labels CC BY 4.0, the film footage's own rights unclear). Private local use only; do not redistribute the weights. Owner to confirm.
- **Method limits:** 11 people clips (30 s each), one interview and one podcast show, so the CI is wide. Speaker identity per second comes from Deepgram labels checked against frames, which favours the diarization candidate, not the winner. Gaming clips are not a fair test (production uses the face-cam stack); the speaker model with the natural threshold scored 54 % there vs 59 % for the old crop (not significant on 5 clips), and gaming style clips do not use it unless the face-cam layout is missing. Body-pose variants of FabuLight (higher published accuracy) not run: they need a pose model.

## Decision 34 (2026-11-20): clip polish, measured on real segments (docs/bakeoff/final/polish/)
Data: a 38 s English podcast segment (1080p, Cordkillers ep. 377 at 10:10) and a 37 s Arabic lecture segment (720p, KAU physiology at 15:05, Arabic with English terms); word times from local Whisper large-v3-turbo. Scripts and raw numbers sit next to the proof clips (`measure_*.py`, `*.jsonl`). Everything is inside the one encode; the quality gate runs as before. Switches in the template: `tighten`, `karaoke`, `zooms` (all on by default).

**34a. Dead-air and filler tightening: adopted** (`clipping/polish.py`, `tighten_graphs` in media.py). A gap between words over 0.4 s keeps 0.12 s on each side (0.20 s after a sentence end); a hesitation sound (um, uh, er, hmm; Arabic stretched ااه, امم, ههم; يعني only next to a pause or said twice) is cut with its word. Edges slide to the quietest 10 ms within 40 ms and snap to the frame grid; a cut whose edge is still loud is dropped; the first and last 0.3 s are never touched; at most 30 % of a clip goes. Video is trim and concat, audio is trim plus a 20 ms triangular crossfade at each join, all in the same filter graph; zooms and the crop path run before the cut so they stay on the original timeline, captions, title card and progress bar use the tightened timeline.
Numbers: English 37.89 s to 37.26 s (1.7 %, one 0.63 s pause cut: this speaker leaves almost no air, 0 gaps over 0.4 s in the word times, 4.4 words per second); Arabic 36.77 s to 33.37 s (9.3 %, three cuts of 1.07, 0.94 and 1.40 s, words per second 2.20 to 2.43). Cut edges sit at -74 dB (English) and at most -43 dB (Arabic, noisy room) against speech medians of -25 and -32 dB; no cut touches a kept word's core, except one English cut in the standard transcript, where Whisper stretched a word over digital silence (the audio there is silent, the word time is wrong). A cut whose edge is within 3 dB of the speech level is dropped (that rule removed a fourth Arabic cut, 0.13 s). Joins: largest sample jump within 5 ms of a join is 0.0003 (English) and 0.0195 (Arabic) against 0.35 to 0.43 for the clip's own 99.9th percentile, i.e. no click. Captions in sync: the finished clips were transcribed again and aligned with the caption words: median offset 4 ms (English, 159 of 168 words matched, 90th percentile of the absolute offset 85 ms; the untouched clip gives 10 ms and 160 ms) and -1 ms (Arabic, 46 of 81 words matched, 90th percentile 290 ms against 172 ms for the untouched clip: Whisper's timing noise on code-switched speech, but also a sign the Arabic spread is no better than before). A/V drift after three cuts on a synthetic flash-and-beep source is under 45 ms (test). Tightening never takes a clip below the campaign minimum length (`min_s` in the render request). Fillers are rare in transcripts: Whisper drops them unless prompted; with a filler prompt and no VAD the English segment gave one "uh" and the Arabic one three يعني (kept: fluent). The filler path is covered by unit tests, not by a real clip; evidence here is thin.
Rejected: a hard audio cut without crossfade (clicks in the synthetic test), cutting from word gaps alone (misses silence Whisper folds into a word: English 0.9 s pause at 18.9 s).

**34b. Punch-in zooms: adopted the eased curve and stress-based picks; the old triangle is kept only as fallback** (`punch_graph`, `polish.stress_moments`). Zoom factor 1.05 to 1.10 by word stress (peak loudness of the word over the clip's speech level, bonus for a sentence start, question or exclamation), smootherstep ease in 0.40 s, hold 0.80 s, ease out 0.70 s, at least 5 s apart, at most 5, never in the first second or the last 1.5 s, never on `facecam_over_game` / stacked layouts (the gameplay half never zooms), text never zooms (zoom runs before the subtitles). Old: 1.07 triangular bump on loud seconds. Numbers (English / Arabic): zooms 1 / 4 to 5 / 5; share of time above 1.02: 1.3 % / 5.1 % to 20 % / 21 %; peak 1.068 / 1.067 to 1.10; frame-to-frame width change at 1080 px: max step 8 to 16 / 8 to 18 px, max acceleration per unit of depth: 114 / 143 px per frame squared (old) to 60 / 60 (new), i.e. about half the lurch for each percent of zoom, and no kink at the apex; stress at the zoom peak (z over speech level) 2.26 / 2.09 (old) against 2.07 / 2.25 (new): no measurable gain in how loud the picked moments are, so the claim is smoothness and consistency, not better targeting. The gap in smoothness is analytic (the same expression evaluated per frame, sizes rounded to even pixels as the filter does); no pixel-level jitter test was run. 1.12 and 0.3 s ease-in were tried and rejected: steps of 28 px per frame, a lurch.

**34c. Word-by-word captions: adopted, karaoke state per word** (`karaoke_events`). Lines of up to 3 words (1.6 s), one subtitle event per spoken word holding the whole line, the spoken word blends from white to the accent in 70 ms; the line never reflows and an Arabic line keeps its right-to-left order and joined letters (colour tags only sit between whole words; checked on a real Arabic frame). Size 5.0 % of the frame height (96 px at 1920, was 4.2 %), shrunk per line to fit two lines in 78 % of the width. Position: block centred at 70 % of the height (Arabic 67 %), the market-style defaults, was bottom-anchored at 16 % margin. Measured box of the widest real line: old y 76.4 to 83.2 % (English), 80.6 to 83.4 % (Arabic), new y 65.8 to 74.9 % and 64.6 to 69.3 %; x 22 to 78 % and 28 to 72 %, clear of the right-hand buttons; the old position ran into the TikTok and Reels bottom interface zone (below about 80 %). Not checked: the real in-app overlay, only the commonly quoted zones. `\k` karaoke tags were rejected: they sweep per letter and run the wrong way in right-to-left lines.

**34d. Image sharpness: lanczos scaling adopted, sharpening rejected, x264 fallback raised, NVENC unchanged.** (A) Scaling, truth = native 9:16 crop of the source, input = crop scaled down 2x, 8 s each: VMAF-NEG (which cannot be gamed by sharpening) bicubic (old) 83.4 / 88.1, lanczos 84.5 / 89.7 (1080p studio / 720p slides); PSNR 34.96 to 35.59 and 32.39 to 32.89; sharpness (Tenengrad) against truth 0.975 / 0.977 to 1.002 / 1.014. Plain VMAF liked sharpening (unsharp 0.5: 91.6 / 99.0) but VMAF-NEG, PSNR and SSIM fell (83.3 / 86.6; 34.86 / 32.07): the filter adds edges that were not there, so unsharp and cas were rejected; bilinear was worst (71.7 / 69.1). Final crop scale, blur-pad foreground and zoom scale now use lanczos. (B) Encoder, truth = the 1080x1920 frame written lossless, same 8 s: NVENC p5 cq24 (current) NEG 82.7 / 95.5 at 28.5 / 19.2 MB per minute; NVENC cq20 +0.7 / +0.8 for +64 % / +72 % size, p7 hq cq19 aq +0.8 / +0.7 for +142 % / +144 %: below what a viewer sees after the platform re-encodes, not adopted. The x264 fallback was far behind (veryfast crf23: NEG 78.9 / 90.5): x264 medium at crf 20 gives 82.3 / 94.8 at 23.1 / 19.0 MB per minute (smaller than NVENC, same quality), so the x264 path now uses preset medium and crf minus 3 (about 3 times slower per clip on CPU, only used when NVENC is busy or missing). Slow and crf 18 were not worth it (slow +0.05, crf18 +0.5 for +24 % size). Method limits: two 8 s clips, VMAF against a lossless re-encode of the same graph.
---

# Decisions made during the build

Each line is a gap in the spec, the safer default chosen, and why. Date: 2026-11-16.

## Infrastructure

1. **Local Postgres is embedded.** With no `DATABASE_URL`, the system starts a Postgres 16 from the `pixeltable-pgserver` pip package in `.data/pg`. This machine had no Postgres or Docker, and it keeps the run to one command. Supabase is used by setting `DATABASE_URL`.
2. **FFmpeg comes from the `imageio-ffmpeg` pip package** when none is on the PATH. It has no `ffprobe`, so media details are read from FFmpeg's own stream report.
3. **A Python API sits between the app and the database.** The app never talks to Postgres directly. Row-level security still applies: every request runs as the `app_user` role with the operator's identity set for that transaction. The worker runs as `app_worker`.
4. **Policies use the backend's session identity, not Supabase `auth.uid()`.** The backend is the only database client, so this works the same locally and on Supabase.
5. **The owner is created with the `admin` role.** Only an admin can change system settings, adapters and the kill switch.
6. **Auth has two modes.** `local` signs in the single owner (optional shared token). `supabase` verifies a Supabase JWT and maps it to an operator. Supabase mode is written but not tested against a live project.

## Campaigns and rules

7. **A hard-blocked campaign is set to `rejected` on intake**, with the reason and the quoted sentence. The spec says hard blocks never proceed; rejecting at once keeps them out of the approval list.
8. **Approving a campaign also confirms its extracted rules.** One recorded human action covers both. The database refuses approval if the rules were never confirmed.
9. **A finance or crypto brief passes the block only when the owner enters a licensed-client reference at intake.** The system records the reference; it does not verify the licence.
10. **Keyword blocks are deliberately broad.** A false block is safer than a missed one. A wrongly blocked brief can be re-entered after the owner checks it.
11. **Added setting: accounts per campaign per platform** (default 1, range 1 to 2). Spec 12.4 refers to "the setting" but 4.3 does not list it.
12. **Brief re-check lasts 6 hours.** The spec says "before each posting batch" without defining a batch. A pasted brief is re-checked by pasting its current text, or by the owner confirming they re-read it.
13. **Only platforms the marketplace tracks are kept** from a brief's platform list (spec 9.7).

## Pipeline

14. **The top-ranked hook is burned in as a title for the first 3 seconds.** Picking another hook in the hooks sheet re-renders the clip as a new version.
15. **Each clip targets one account and one platform**, rotated across the owner's active accounts on allowed platforms. Posting the same clip to several platforms would need one checklist per platform and is left for later.
16. **Captions and the disclosure label are burned in with one encode** (ASS subtitles). The label runs from 0 to the end of the clip.
17. **Loudness is normalised in the render stage** (`loudnorm`, -14 LUFS).
18. **Speaker reframing needs OpenCV, which is optional.** Without it every clip is centre-cropped, which is the declared fallback.
19. **Added settings: clip minimum and maximum length** (5 and 90 seconds). The deterministic duration check needs limits and the spec gives none.
20. **Source footage is registered by file path on the laptop** rather than uploaded through the browser. The worker runs on the same machine in phase 1.
21. **No separate 720p preview is made.** The clip itself is streamed through a signed link that lasts 15 minutes.
22. **Music and copyright rules always come back as `review`.** Agent C may not judge copyright or verify licences.
23. **Weekly clip cap is soft.** Extra moments are kept as candidates and a warning is raised.

## Review and publishing

24. **Clip statuses.** `accepted` is in the Queue with the checklist open. `ready_to_post` has passed publish approval. `queued` has been handed to the scheduler. All three appear in the Queue.
25. **Feed ranking uses the campaign's net rate per 1,000 views** as the "net pay per hour" input until the owner has their own data. The system has no view forecast and does not invent one.
26. **Default priority weights:** net pay 5, deadline 4, hook 3, compliance 3, budget 2, account cap 2. The spec says "defaults" without values. All are editable in Settings.
27. **The server allows Undo for the window plus 5 seconds**, to cover network delay.
28. **A refused edit is rolled back.** For example, a caption edit that drops the disclosure leaves the original clip untouched.
29. **Offline actions are kept in the browser's local storage** and carry an ID, so the server applies each one once.
30. **Arabic interface labels cover navigation and review actions.** The layout mirrors right-to-left. Other screens stay in English for now (spec: English first).
31. **Default Arabic disclosure is `#إعلان`.** The owner decides the wording (D11).

## Money and alerts

32. **An unknown creator fee leaves fee and net empty.** Reports then use gross and say the fee is unknown.
33. **Per-language and per-account net pay per hour split a campaign's hours evenly across its posted clips.** Time entries are logged per campaign, not per clip.
34. **Go, iterate or stop uses the median of weekly net pay per hour, including pending payouts.** The "and rising" condition is left to the owner to judge.
35. **Account health compares 48-hour views of posts from the last four days against the earlier four weeks.** It needs at least 2 recent and 4 earlier posts. A drop past the threshold sets the account to `warned` and stops new clips being assigned to it.
36. **Alerts are deduplicated while open.** The same alert is not sent again until it is acknowledged.
37. **Paid adapters with no configured price stay paused.** The monthly cap cannot be enforced without a price, and prices are never guessed.

## Not built in this run

See `docs/open-items.md` for the list.

---

# Round 2 decisions (owner review, 2026-11-17)

Decided with the owner item by item. These replace earlier lines where they conflict.

## Campaigns
- All researched marketplaces are recorded: Content Rewards, Vyro, Clipping.net, Elev8or, ClipFlip, Shoot, ClipAffiliates, Clipping.io. Kick clipping (mostly gambling) and Apify aggregators are excluded.
- Automatic reading only for Content Rewards (public Discover page; terms read 2026-11-17, no scraping ban). Vyro, Elev8or and Shoot forbid scraping. Clipping.net and ClipFlip are unclear, so they stay manual.
- Manual intake made fast: a browser "save campaign" button, the phone share sheet, and screenshot reading.
- The campaign explorer filters on marketplace, social platform, pay model, rate per platform, fee known, payout method and minimum, hold, views needed to get paid, budget left, burn rate, days left, required audience and "new account required".
- New checklist rules: Content Rewards needs the post submitted within 30 minutes; a rejected post can't be resubmitted; Clipping.net posts must pass 1,000 views and stay public; ClipFlip grants brands a lasting licence (shown as a warning).
- Unknown fees stay unknown. The real withdrawal fee is worked out from the first withdrawal's received amount.

## Design
- Social-app experience everywhere, light by default, dark for the video review and as an option. Five places on phone and desktop: Home, Explore, ➕, Review, Profile; the bell opens alerts.
- Every screen works on both devices. Desktop gets a side menu and wider layouts, not different features.
- No likes, comments, follower counts, streaks or reward animations.

## Alerts
- Telegram in Arabic, English or both (default both), adjustable quiet hours (default 23:00 to 08:00, block alerts always), morning summary, a button that opens the screen.

## Models and media
- One model per task: a strong model for rules extraction, hooks and moment finding; a small one for compliance.
- Deepgram Nova-3 by default (regional Arabic codes, speaker labels, utterances, word confidence), local Whisper on the laptop GPU as the real fallback, a transcript correction screen, and per-campaign key terms.
- Dialect comparison happens automatically from the owner's corrections instead of a separate bake-off.
- Moment engine owned by us (D13 changed): audio, video and transcript signals, whole-episode context, pairwise ranking, cut variants, campaign awareness, de-duplication, optional vendor second opinion, learning from the owner's decisions and view results.
- One combined render (captions and overlay in the same encode). Arabic shaping verified with the bundled FFmpeg; the Arabic #ad label moves to the top right.

## Accounts and posting
- One account per platform per niche, starting with one niche. Never the same clip on two accounts of the same platform; cross-platform is fine when the campaign allows it.
- Paid-partnership toggle has three states: on, not available for this account (with reason; caption and overlay disclosure still required), not done (blocks).
- Posting through official APIs behind a confirm tap: Instagram (paid-partnership flag at publish), YouTube upload, TikTok after its audit (private-only before). Posts go out only inside the owner's available hours, because of the 30-minute rule.
- Originality layer per clip and per-account templates, because Instagram stops recommending aggregators.

## Platform
- Phone access through Tailscale (private, HTTPS on ts.net) plus passkey sign-in.
- Footage from the phone, from Google Drive, Dropbox or direct links; YouTube only when the brief explicitly permits clipping that content, with a warning that YouTube's terms forbid downloads.
- Views read through official APIs where possible (YouTube, Instagram, TikTok after review), screenshot reading otherwise.
- The laptop is kept awake while jobs run; a small always-on server is phase 2.
- Daily database backup to a second place, weekly CSV of the audit log and ledger, retention deletion after campaigns close.

---

# Round 3: lessons from practitioners (2026-11-17)

From clippers' published experience (guides, complaint write-ups, marketplace help centres; Reddit was not reachable).
Each line is a lesson and what the system does with it, with the build phase.

## Choosing campaigns (phase C)
- Views outside the US, UK, Canada and Australia often pay less or nothing → each account's audience countries are read from the platform APIs; campaign geography rules are extracted; ranking uses expected *paid* views, not total views.
- A clip under the minimum earns nothing → ranking uses each account's real median views to estimate the chance of clearing the minimum; campaigns out of reach are hidden by default. Beginners start with 10K–25K minimums.
- Budgets run out before late views verify → new campaigns get a boost, campaigns over 90% spent drop, stalled budgets are penalised, and approved campaigns are worked first while budget is fresh.
- Risk-adjusted ranking: approval odds, budget runout and payout record are simulated and campaigns rank on the cautious (lower-quartile) outcome.
- Milestone and hybrid campaigns pay without a viral clip → shown first while accounts are new.
- Spread risk → no more than a set share of the week's clips in one campaign, and at least two marketplaces once possible.
- Arabic clips mostly reach non-Tier-1 audiences → Arabic work is matched to campaigns with no country rule, or to direct deals with Arabic creators.

## Making clips (phase D)
- Fewer, stronger clips beat many weak ones → a quality gate keeps clips with a low chance of clearing the minimum out of the top of the feed.
- Unclear briefs cause rejections → a brief-quality grade (A to F) and a checklist of every written rule; a rejection reason from the marketplace becomes an extra check for that brand's future clips.

## Posting (phase E)
- Tier-1 reach for non-US clippers is often easier on Instagram Reels and YouTube Shorts than TikTok → the platform for each clip is chosen from the account's audience and the campaign's country rule.
- Consistency matters (3+ clips a week, daily-ish for 8 weeks) → a weekly posting plan and reminders in the morning summary.
- New accounts: a warm-up period with low volume in one niche before campaign volume (common practitioner advice, not platform documentation). No engagement tricks of any kind.
- Never the same clip on two accounts of one platform; perceptual fingerprint check against our own past posts.

## Money, proof and learning (phase F)
- Disputes are won only with dated evidence → an evidence pack per clip: post link, submission time, analytics snapshots at 24 h, 72 h and 7 d (API where available, screenshot request otherwise), kept off-platform.
- Rejections must rest on written rules; one appeal → an appeal helper drafts the text from the brief snapshot and the evidence, with the deadline.
- Posts must stay public, keep likes visible and not be deleted for 30 days; low engagement for the view count can be flagged as bots → a post-health monitor alerts on any of these.
- Balances stuck as pending → payout ageing alerts and a reminder to withdraw once above the minimum.
- Track and adjust → a weekly "what worked" report: hook type, clip length, opening style, platform, posting hour, with three concrete suggestions.
- Most people quit between day 10 and day 30, earning under $10/hour in month one → a ramp plan with weekly targets, trends for time per clip and views per clip, and the go/iterate/stop decision only after week 8.
- Pick a niche you enjoy and go deep before widening → onboarding asks for your niches; Explore defaults to them.

---

# Round E decisions (2026-11-19)

Decided during phase E. These replace earlier lines where they conflict.

- **One tap for all platforms, staggered.** One confirm queues one post per platform. Posts are spaced `post_stagger_min` apart (default 20 minutes, minimum 10). Each post stays inside its own account's posting window. This replaces decision 15, which allowed one platform per clip.
- **Encrypted tokens in the database.** OAuth tokens are stored as AES-GCM ciphertext. The key is `TOKEN_VAULT_KEY` in `.env`. Tokens are never logged, audited or returned to the web app.
- **TikTok as inbox draft.** The post lands in the app's drafts. The caption is not sent, so the owner adds it in TikTok, posts, and pastes the link.
- **YouTube private until audit.** Uploads are private while the Google project is unverified. The owner makes the video public in YouTube Studio and pastes the link.
- **Instagram publishes publicly.** The paid-partnership flag is not exposed by the API for Instagram Login, so it stays a checklist step.
- **No auto-retry after an unclear publish.** If the upload started and the outcome is unknown, the post becomes "needs check". The owner checks the platform. A retry could post twice.
- **Manual target only for unconnected platforms.** A connected platform cannot be posted by hand yet.
- **Pasted links on the platform's own host.** A pasted link must be on that platform's own site. Other links are refused.
- **Morning summary at 08:00.** Drafts, private uploads and other "finish in the app" items appear in one daily summary from 08:00.

---

# Benchmark downloads (2026-11-19)

Decided by the owner, with these limits (relayed in the session notes; the owner's words are the source):
- **Internal measurement only.** Videos from any platform may be downloaded to train and test the moment and crop models. Nothing is posted, shared or re-uploaded. Videos, frames and audio are deleted after the features and metrics are extracted; only numbers, labels and small feature files stay.
- **Location.** `.data/benchmark/` on drive L only, with `.data/benchmark/manifest.json` listing source URL, platform, date, size and deletion status.
- **Anonymous access only.** No cookies, logins or tokens from the owner's real accounts. If a platform refuses anonymous access, report it; no workaround. Twitch metadata only through the official Helix API with an app token.
- **Gentle pace.** About 2 downloads a minute at most, a small total, and the first batch limited to 10 videos. The bulk runs after the owner confirms their social accounts exist, so downloading does not hurt the IP reputation those accounts start with.
- **Platforms in scope.** YouTube, Twitch, TikTok, Instagram, Facebook. YouTube and Twitch give usable crowd labels; the others give one number per finished short or nothing.
- **Plan.** `docs/quality-diagnosis.md`, sections 7 to 9.

## 2026-11-19: reframe bake-off (docs/bakeoff/reframe/index.html)

- **Adopted: OpenCV YuNet** (MIT, `face_detection_yunet_2023mar.onnx`, 0.3 MB, CPU). Interview: crop holds the speaker 89 % of sampled frames vs 10 % for the skin baseline. Gaming: finds the face-cam in 100 % of frames (skin 0 %), but a plain face-follow crop chases game characters, so for gaming it is used as a face-cam locator only.
- **Rejected as the main detector: MediaPipe BlazeFace** (Apache-2.0). Misses small faces (face-cam 12 %), 66 % in crop on the interview, jumpier.
- **Kept as tie-break only: MediaPipe FaceLandmarker mouth activity** (Apache-2.0). 76 % in crop vs YuNet 89 % on single-face shots; its value is on wide two-shots, which the sample did not contain. Not adopted.
- **Not run:** OpenShorts, wjs-reframing-video, clipsai resize, AutoFlip, TalkNet, Light-ASD (see docs/survey-table.md). No candidate was rejected on evidence there.
- **Method limits:** the reference face is a detector consensus (YuNet-favoured); video C is slides with no face, so it measures nothing. Frame strips are included for checking by eye.
- **Automatic backups.** One pg_dump (custom format) per 24 hours from housekeeping into `.data/backups`, newest 14 kept; `python run.py backup` for a dump now, `python -m clipping restore <file>` restores into a new scratch database, never the live one.

## Decision 31 (2026-11-19): swipe right is the owner's confirm to post
In the review feed a swipe right accepts the clip AND is the owner's deliberate confirm to post. It shows a bar ("Posts to TikTok and Instagram in 20 minutes", staggered as in Round E) with a 10-second undo. If the account is not connected, it shows "Ready to post manually" with the caption ready to copy. Unchanged: no posting on practice campaigns, every post is recorded, the kill switch stops the queue, the human-gate triggers stay. Reason: the owner wants swipe-right to mean publish, and a visible bar with undo keeps it a conscious owner action.

---

## Decision 35 (2026-11-20): caption lab, packaging and originality (branch caption-lab)

Decided without the owner present, on the evidence in `docs/growth-tricks.md`, `docs/research-verdicts.md` and `docs/money-readiness-map.md`. Code: `caption_lab.py`, `caption_match.py`, `packaging.py`, `originality.py`, `caption_text.py`; runs inside the existing `package` stage. Tests: `backend/tests/test_20_caption_lab.py`.

## A. Caption lab and match checker
- **Two variants per clip.** `matched` (green, the default) says only what the clip says. `curiosity` (amber) is an honest open question the clip answers. The owner may switch to curiosity per clip (`POST /api/clips/{id}/caption-variant`); the default is never curiosity.
- **A caption may only say what the transcript says.** The checker rejects: a number not spoken (digits and spelled-out numbers are compared), a quotation not in the transcript, clickbait words ("you won't believe", "shocking", "secret", Arabic equivalents) unless the speaker said them, absolute words ("best", "never", "everyone") unless said, and a caption whose meaningful words are mostly absent from the transcript (70% must be present for matched, 60% for curiosity). A curiosity variant must also be a question.
- **The model can only add rejections.** A separate `caption_check` model call splits the caption into claims and quotes the supporting words; a claim counts only if the quote is really in the transcript. Same rule as Agents B and C.
- **A clip always gets a matching caption.** If the model's matched variant fails, a caption cut from the clip's own best sentence replaces it. A rejected curiosity variant is kept with its reasons but cannot be chosen.
- **Arabic is written for Jordan and the Gulf together:** plain Arabic with light Jordanian or Gulf colloquial words (ليش, شو, وش, شوفوا) and nothing from a far dialect. The clip's dialect label picks a Jordanian, Gulf or neutral frame. Arabic text is compared after folding letter variants, diacritics, Arabic-Indic digits and the common prefixes.
- **Disclosure first, always.** The existing disclosure template stays the first words of every caption (the compliance check already requires it). After packaging, compliance runs again; if the new caption fails it, the render's caption is put back and the reason is stored in `render_meta.caption_lab_error`. A model failure never blocks a clip.
- **The caption no longer embeds the hook line.** Before, the caption was the disclosure plus the hook text. The hook is a pattern line that can promise more than the clip says, so it stays on screen only; the caption comes from the lab. One existing test (`test_each_edit_creates_a_new_clip_version`) was changed to match.
- **Not done:** a "payoff" check that the second half of the clip delivers what a curiosity question promised (only word coverage is checked), and the A/B tracking of variants on different moments (money map section 5). Fake-model tests prove the plumbing and the rules, not the quality of a real model's captions: a real run needs a look by the owner.

## B. Per-platform packaging
- One clip gets a title, caption, hashtags and cover text for each platform the campaign allows (TikTok, Instagram Reels, YouTube Shorts), stored in `clips.render_meta.packaging`. The clip's own platform fills `caption_text`, `hashtags` and `mentions`, so the review feed, the swipe "Ready to post manually" copy and the posting worker read the same text. The worker and the manual card use the platform's own package (`packaging.caption_for`), except when the owner edited the caption by hand: the edit wins everywhere.
- **Norms (practitioner guidance, not platform guarantees):** TikTok body about 150 characters before the fold, Instagram about 125, YouTube about 100 with a title of at most 70; 3 to 5 hashtags on TikTok and Instagram, 2 to 3 on YouTube; cover text at most 5 words. Required campaign hashtags and mentions are never cut and never counted against the maximum. A hashtag is added only if it is a word the clip says. No "fyp"-style tags.
- **Not done:** the cover text is stored but the cover image does not use it (render path is another branch's); no per-platform length in seconds.

## C. Originality layer (minimal)
- **Why:** YouTube (1 Oct 2026) and Meta (March 2026) reduce reach for reposts that add nothing, and template-based bulk changes do not count. The layer adds a real, owner-visible element and records it. It does not alter picture or audio to hide the source: no evasion of duplicate or copyright detection, and one source moment still goes to one account.
- **Per clip:** one commentary line (a take or a framing question, at most 120 characters, not a copy of a transcript sentence, no new numbers or quotes, no clickbait). It goes into the post text after the caption body when the platform limit allows.
- **Per account:** `accounts.commentary_template` (migration 021): an intro line per language with `{topic}`, plus a style note (`PUT /api/accounts/{id}/commentary-template`). It is appended after the commentary. It gives the account a recognisable family but is recorded as NOT added value.
- **Record in `render_meta.originality`:** status, each element (kind, text, placement, `counts_as_added_value`, `owner_confirmed`, `burned_into_video`), and `added_value` (true only with a commentary line). Today every text element is `burned_into_video: false` and `owner_confirmed: false`: the owner reads it in the caption before swiping right, and the render does not yet show it on screen.
- **Briefs that want raw clips** (words such as raw, unedited, "do not add text or commentary") switch the layer off for that campaign and say why in the record.
- **Not done:** on-screen intro or outro text and a voice-over (needs the render path), a measured commentary share, an owner-confirm step before the text is used.
