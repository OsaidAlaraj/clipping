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
