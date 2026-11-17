# Clipping Campaign System: Final Specification v1.0

Date: 2026-11-16
Owner: you (sole operator and account owner at launch)
Status: final draft for build. Replaces the spec v0.2, decisions log v0.4, adapters spec v0.1 and UX spec v0.2. Nothing is built yet.

How to read this document:
- **Section 1** is what cannot change (hard rules).
- **Section 2** is what was decided, with IDs.
- **Section 3** is what is still open, with what it blocks (nothing, in most cases).
- Sections 4 to 17 are the design.
- **[MVP]** = first build. **[Later]** = after the MVP.
- *(verify)* = not confirmed by a primary source. *(est.)* = estimate.

---

## 1. Purpose and hard rules

### 1.1 Purpose

Find paid clipping campaigns, check their rules, turn permitted footage into compliant short clips in English or Arabic, let the owner review every clip on a phone, post them to the owner's own accounts, and track what each clip earns and what it costs in time.

### 1.2 Hard rules (no setting changes these)

1. **Each account has exactly one owner**, the person paid for it. Accounts are never shared.
2. **No automated public posting through unaudited apps.** Unaudited TikTok clients post only private videos (section 10).
3. **Campaign data may be scraped, one adapter per marketplace,** but each marketplace's terms are read and recorded before its adapter goes live. Manual paste is always available.
4. **No bulk or coordinated multi-account posting.** Each account posts its own content on its own rhythm.
5. **No gambling-linked campaigns.**
6. **No finance or crypto campaigns** unless the client is licensed and supplies approved copy.
7. **No unlicensed music** and no reposting others' content without written permission. Reddit-style stories must be original or licensed.
8. **No purchased views, bots, engagement farms or anti-detect browsers.**
9. **The system never certifies legal compliance.** It flags, records, and asks the owner to decide.

---

## 2. Decisions (closed)

| ID | Decision |
|---|---|
| D1 | **Campaign sourcing:** scraping accepted, one adapter per marketplace. Manual paste is the fallback for every marketplace. |
| D2 | **Moment finding:** adapter-based. Vizard is the default. OpusClip and Klap are supported as alternative adapters. No comparison test is required before build. The OpusClip API needs its Business plan, which is quote-only, so that adapter is switched on only after a quote. |
| D3 | **Posting:** manual native posting in weeks 1 to 3. Buffer scheduler from week 4. Free plan first. |
| D4 | **Platforms:** all platforms that campaigns pay for, each one an adapter. Start with TikTok, Instagram Reels and YouTube Shorts. X and Facebook are added when a campaign you approve pays for them. |
| D5 | **Languages:** English and Arabic. All Arabic dialects are configurable per category and per operator. |
| D6 | **Transcription:** replaceable adapter. Deepgram Nova-3 is the default. A per-dialect bake-off picks the provider for each dialect. |
| D7 | **Architecture:** orchestration system. Every external service is an adapter. Plans, limits and keys are configuration. Replacing a vendor or a plan is an adapter change, not a rebuild. |
| D8 | **Start:** locally on the laptop. Accounts and sources are verified before the system scales. |
| D9 | **Cloud credit:** Azure for Students is active on the owner's account with a $100 credit. The credit is valid for 12 months and renews by re-verifying student status. It covers the LLM and a VM. |
| D10 | **Operators and accounts:** the owner only, at launch. All accounts are the owner's. The configuration supports more operators and accounts later, without code changes. |
| D11 | **Disclosure:** disclosure labels are a required overlay and caption template, configurable per language. Defaults: `#ad` in English, the Arabic equivalent in Arabic, and the platform's paid-partnership toggle. Legal review is not a build blocker. Revisit only if it changes the design. |
| D12 | **Moment-finding budget:** no fixed budget set yet. The Settings cap for A4 defaults to $20 a month (section 10.8) until the owner sets a figure (O12). |
| D13 | **Agent B scope (my interpretation of "not our own, next step"):** Agent B does hook generation and ranking. Moment finding comes from the adapter (D2). Own moment finding is a later step. Confirm this in section 3. |
| D14 | **Marketplaces:** Content Rewards, Vyro, Clipping.net, Elev8or Bounties, ClipFlip, and direct programmes. Each has its own conditions and pipeline settings (section 9). |
| D15 | **Alerts:** Telegram bot is the main channel. In-app alerts mirror it. |
| D16 | **Review flow:** dashboard is home. Four bottom tabs. Feed, priority-ranked, recalculated on every load. Accept moves a clip to the Queue. Reject needs a reason and gives a 10-second Undo. Review Later is a separate pile grouped by campaign. |
| D17 | **Session:** default 25 minutes, set in Settings. The session timer starts when a feed session opens. A session summary appears at the end. |
| D18 | **Display:** pay per clip is shown on each card or only on the summary, as a Settings option. Priority weights are editable in Settings. |
| D19 | **Campaign swipe on Home:** right approves a campaign, left parks it, tap opens its feed. |
| D20 | **Look and feel:** social-app pattern. Full-screen vertical video, right-side action rail, stories row of campaigns on Home. No likes, comments or follower counts. |

---

## 3. Open items

Only items that could affect the build are listed first. Items that only affect cost, or that can be checked later, are marked as such.

| # | Item | Blocks build? | Action | Owner |
|---|---|---|---|---|
| O1 | Confirm D13 (Agent B does hooks and re-ranking only; moment finding comes from the adapter) | Yes, for the Agent B design | Confirm or change | Owner |
| O2 | Arabic dialect bake-off results (default per dialect) | No. Build starts with the default dialect (Modern Standard Arabic) and adds others as they are tested | Run the bake-off | Owner, then build |
| O3 | Buffer paid-partnership toggle per platform | No. If missing, the toggle is a manual checklist step | Check the Buffer connection screen before week 4 | Owner |
| O4 | Elev8or platform list (X unclear) | No. X stays off until confirmed | Open a live Elev8or bounty and read its platforms | Owner |
| O5 | Vyro and Clipping.net fees (not published) | No. Records keep a "fee unknown" value | Sign in and read the fee screen | Owner |
| O6 | Content Rewards withdrawal fee for ACH ($2.50 in research, not in current creator terms) | No. Stored as unknown until checked | Check the current payout screen | Owner, then build |
| O7 | Vizard rate limits and minute caps | No. The adapter reads the limit from its response | Ask Vizard, or read its rate-limit page | Owner |
| O8 | Supabase free plan: pauses after 7 days of inactivity | Decided (2026-11-16): keep-alive ping on the free plan now, paid plan in phase 2. Check the pricing and pause rules on supabase.com before go-live | Build the keep-alive job with a failure alert | Owner, then build |
| O9 | Terms check before each scraper goes live | Yes, per marketplace | Read the marketplace's terms, record the date | Owner |
| O10 | OpusClip Business API price | No. Only needed if the OpusClip adapter is switched on | Request a quote | Owner |
| O11 | Telegram bot creation and token | Yes, for alerts | Create the bot, store the token in environment variables | Owner |
| O12 | Moment-finding monthly budget, and the default for pay-per-clip display (the UX spec gives no default) | No. Defaults in place: $20 cap; pay shown on summary only | Set in Settings | Owner |

---

## 4. Users and configuration

### 4.1 Operators

- At launch: one operator (the owner). The schema supports any number.
- Each operator has: login, role (operator, reviewer, admin), payout reference (not bank details), daily hours cap, languages, and accounts.
- Access is by ownership and role (row-level security on `owner_operator_id` and role), not by a fixed user count.

### 4.2 Accounts

- Any number per operator and per platform. Exactly one owner each.
- Fields: platform, handle, status (active, warned, restricted, retired), daily post cap, posting window.
- Default: one account per campaign per platform. A second account is allowed only when the campaign brief permits it in writing.

### 4.3 Settings

All tunable values sit in one `settings` table with a default, a minimum and a maximum. Every change writes an audit row. Settings never override hard rules. Bounds in the table below are proposals for the owner to confirm at build.

| Setting | Default | Bounds | Scope |
|---|---|---|---|
| Hours cap per operator per day | 5 | 1 to 8 | Operator |
| Session length | 25 min | 10 to 60 | Operator |
| Undo window after reject | 10 s | 5 to 30 | Operator |
| Pay per clip display | Summary only (proposed, O12) | Card, summary only, off | Operator |
| Posts per operator per day | 6 | 1 to 8 | Operator |
| Posts per account per day | 2 | 1 to 3 | Account |
| Posting window | 09:00 to 22:00 local | any | Account |
| Clips per campaign per week | 10 | 1 to 30 | Campaign |
| Budget alert threshold | 20% | 5% to 50% | System |
| Health alert threshold (drop vs baseline) | 60% | 30% to 80% | System |
| Source footage retention | 30 days | 7 to 90 | System |
| Transcript and clip retention after campaign close | 90 days | 30 to 365 | System |
| Priority weights (section 6.2) | defaults | 0 to 10 each | Operator |
| Adapter choice and plan per stage | defaults | supported list | System |
| Monthly cap per paid adapter | per section 10.8 | $0 to $100 | System |

Caps are soft (warn) by default. Compliance rules are always hard.

---

## 5. Functional requirements

### 5.1 Campaign intake and filtering

- **FR-01 [MVP]** Campaigns come from the marketplace adapter (scrape) or from a pasted URL and brief. Each is stored as a raw snapshot with hash and read time.
- **FR-02 [MVP]** Agent A extracts rules into the schema (section 11.2). Every rule quotes its source sentence. The output is a draft the owner confirms.
- **FR-03 [MVP]** Campaigns ranked by estimated net pay per hour, remaining budget, deadline, rule clarity and compliance status.
- **FR-04 [MVP]** Hard blocks: gambling; unlicensed finance or crypto; disclosure forbidden by the brief; rules that conflict with platform rules.
- **FR-05 [MVP]** Owner approval gate. Only approved campaigns create production jobs.
- **FR-06 [Later]** Direct programmes with signed briefs as a source.
- **FR-07 [Later]** Episode radar from public podcast RSS feeds.

### 5.2 Production pipeline

- **FR-10 [MVP]** Each approved campaign creates jobs per source file and per category template.
- **FR-11 [MVP]** Transcribe with word timestamps, cache by source hash. Language and dialect set per source.
- **FR-12 [MVP]** Moment finding through the moment-finding adapter (D2). Output: candidate start and end, score, and optional reframed file.
- **FR-13 [MVP]** Snap boundaries to sentence or silence boundaries when the adapter does not.
- **FR-14 [MVP]** Agent B generates 2 to 3 hook options per candidate, each tagged by type (question, contrarian, stakes, number, story-open).
- **FR-15 [MVP]** Render 9:16. Reframe to the active speaker when a face is detected, otherwise centre crop.
- **FR-16 [MVP]** Burn in captions in the clip's language. Normalise loudness.
- **FR-17 [MVP]** Burn in the disclosure overlay for the full clip duration (section 12).
- **FR-18 [MVP]** Package: video, hook options, caption starting with the disclosure, hashtags, required tags and mentions, compliance report.
- **FR-19 [Later]** Evergreen re-clipping of approved footage.
- **FR-20 [Later]** Hook library per niche, built from results.

### 5.3 Review

- **FR-30 [MVP]** Feed of packaged clips (section 6). Right accepts, left rejects with a reason, up and down move.
- **FR-31 [MVP]** Rejection needs a reason code (section 6.5). A 10-second Undo follows. Acceptance records the chosen hook.
- **FR-32 [MVP]** Hooks sheet: all options for the clip, one-tap choice.
- **FR-33 [MVP]** Compliance report on each card. Hard fails never reach the feed.
- **FR-34 [MVP]** Edit: trim, swap hook, edit caption. Each edit creates a new clip version.
- **FR-35 [MVP]** Desktop keyboard shortcuts: A accept, R reject, H hooks, arrows, E edit, Esc, and U undo.
- **FR-36 [MVP]** Review Later: a separate pile, grouped by campaign (section 6.4).

### 5.4 Publishing

- **FR-40 [MVP]** Publish checklist per clip: correct account and platform, disclosure toggle on, caption pasted, hashtags and tags checked, file hash matches the approved version, brief re-checked.
- **FR-41 [MVP]** Owner posts (manually in weeks 1 to 3, or through the scheduler from week 4) and records the post link. Metric tracking starts.
- **FR-42 [MVP]** Per-account daily cap and posting window. Warn before a cap is exceeded.
- **FR-43 [MVP from week 4]** Scheduler adapter (Buffer on the free plan to start).
- **FR-44 [Later]** Direct API posting (YouTube first). Requires its own review and quota planning.

### 5.5 Tracking, payouts and time

- **FR-50 [MVP]** Views logged at 48 hours and 7 days per post, by manual entry or platform export.
- **FR-51 [MVP]** Payout ledger per clip with the statuses in section 13.1.
- **FR-52 [MVP]** One-tap timer, tagged by task type (sourcing, cutting, review, posting, admin). Starts when a feed session opens.
- **FR-53 [MVP]** Net pay per hour by campaign, niche, language, account, operator and overall.
- **FR-54 [MVP]** CSV export per month.

### 5.6 Alerts

- **FR-60 [MVP]** Telegram alerts: deadline within 48 hours; budget below the threshold; rule change; payout held over 14 days; account health warning; kill switch on; adapter quota near limit.
- **FR-61 [MVP]** In-app alert inbox with acknowledge and snooze. Block-level alerts stay at the top until acknowledged.

### 5.7 Governance

- **FR-70 [MVP]** Three human gates: campaign approval, clip acceptance, publish approval. No state passes a gate without a recorded human action.
- **FR-71 [MVP]** Kill switch: stops new jobs and hides all "ready to post" packages.
- **FR-72 [MVP]** Audit log for every state change, including settings and adapter changes.
- **FR-73 [MVP]** Brief re-check before each posting batch. A changed brief blocks the batch until re-approved.

---

## 6. User experience

### 6.1 Navigation

Four bottom tabs: **Home, Feed, Queue, More**. Under More: Review Later, Ledger, Alerts, Settings.

### 6.2 Home (dashboard)

- **Stories row:** one round avatar per active campaign. A ring shows budget left, or time to deadline. Tap opens its feed.
- **Today:** hours logged against the cap, clips accepted, posts done, posts due.
- **Net pay per hour:** this week and month to date. Confidence label: low until 30 days of data.
- **Attention:** alerts. Block-level alerts first.
- **Campaign cards:** ranked by estimated net pay per hour. Each shows rate, platforms, budget left, deadline and compliance badge. Pay per clip appears only if the setting is on.
  - Swipe right approves the campaign. Swipe left parks it. Tap opens its feed.

### 6.3 Feed

- Full-screen vertical video, one clip at a time, with a thin progress bar at the top.
- Header: campaign name. Footer: account, platform, language, duration, compliance badge, one-line priority reason.
- **Action rail on the right:** accept (✓), reject (✗), hooks, edit, review later. Each has a VoiceOver label.
- **Swipe right:** accept. The clip moves to the Queue.
- **Swipe left:** reject. A reason is required, then a 10-second Undo toast.
- **Swipe up or down:** next or previous.
- **Review later:** moves the clip to Review Later. It counts as neither accepted nor rejected.
- **Muted start:** clips start muted. Tap to turn sound on.
- **Bounded feed:** when the session length ends, the feed stops and the summary appears (clips reviewed, accepted, rejected, posts due, hours logged).
- Hard-blocked clips never appear in the feed. They appear in Alerts with the reason.

### 6.4 Queue and Review Later

- **Queue:** accepted clips ordered by due time. Scheduler time shown from week 4. Each item opens the publish checklist. "Mark posted" asks for the link. A clip can be moved back to Review Later.
- **Review Later:** grouped by campaign, like Instagram's Saved collections. Each campaign is a folder with clip count, deadline and a thumbnail strip. Clips show their parking reason. Folders for closed campaigns sit at the bottom with their counts.

### 6.5 Reject reason codes

Weak hook; cut mid-thought; wrong context; caption error (name, term or language); audio problem; framing problem; copyright or music risk; disclosure problem; outside campaign rules; duplicate; other (note required).

### 6.6 Priority algorithm

- Recalculated on every feed load. The order can change between visits.
- Inputs, each weighted in Settings:

| Input | Effect |
|---|---|
| Campaign estimated net pay per hour | Higher first |
| Deadline proximity | Sooner first |
| Clip hook score (from Agent B) | Higher first |
| Remaining campaign budget | Low budget raises priority |
| Account cap status | Near-cap accounts drop down |
| Compliance status | Warn-level drops down; block-level removed |

- Hard blocks are removed before ranking. Ranking never overrides them.
- Ties break by creation time, oldest first.
- Each card shows its top reason in one line.
- Review Later clips are not in the ranked feed. They appear only in Review Later.

### 6.7 Session

- Default 25 minutes, set in Settings.
- Timer starts when a feed session opens and stops when it closes. One-tap correction.
- "Stop here" saves position (campaign, clip, filters) and ends the session with the summary.

### 6.8 Design principles

- **Thumb reach:** accept and reject sit in the lower-right third, at least 44 pt.
- **Few choices:** four tabs; one primary action per screen.
- **Familiar patterns:** vertical video and side rail, so gestures need no teaching.
- **Visible status:** progress bars, tab counts and checklist steps.
- **Speed:** accept updates at once; the server confirms in the background.
- **Reversibility:** reject has Undo. Posting needs an explicit confirm. Nothing posts automatically.
- **Recognition over recall:** campaign, platform, deadline and account on every card.
- **Closure without pressure:** counts and rings show unfinished work. No streaks or reward animations.
- **Clear endings:** the session summary ends a session.
- **Transparent ranking:** every card states why it is where it is.

### 6.9 Desktop

- Campaign board with drag and drop, pipeline view, ledger and audit view. Keyboard shortcuts as in FR-35.

---

## 7. iOS web app (PWA)

- **Install:** Safari, Share, Add to Home Screen. Runs full-screen.
- **Push:** works only for installed web apps on iOS 16.4 and later, after a tap-triggered permission request. Telegram is the main alert channel, so the app works without push.
- **Gesture rule:** swipes start at least 20 px from the left edge, so they do not clash with Safari's back gesture.
- **Autoplay:** iOS blocks sound autoplay. Clips start muted; sound turns on after a tap.
- **Haptics:** not reliable on iOS web. Visual feedback is the main signal.
- **Video:** streamed from storage through short-lived signed links. Previews at 720p.
- **Motion:** swipes handled with Motion. Animations respect reduce-motion.
- **Offline:** cached Home and Queue. Accept, reject and park actions are saved on the device and sent when online. Posting needs a connection and says so.
- **Storage:** Safari can clear storage for apps not used for a while. Nothing critical lives only on the device.

---

## 8. Language and layout

- Interface: English first. Arabic labels later.
- Arabic: the layout mirrors right-to-left, including the action rail and progress direction. Swipe meanings stay the same (right accepts, left rejects), so the rail moves to the left.
- Arabic clip cards: caption and hook text right-to-left, with an Arabic-capable font.
- Dark theme by default. Text meets WCAG AA contrast. Every icon has a VoiceOver label. Motion respects reduce-motion.
- One-handed layout on large phones; primary actions in the lower half.

---

## 9. Marketplaces

Each marketplace has a record with: platforms, pay model, fee rules, withdrawal methods, hold timing, source adapter, and terms version. Values marked (verify) need checking before that marketplace's adapter goes live.

### 9.1 Content Rewards (Whop)

- **Platforms:** TikTok, Instagram, YouTube (per campaign).
- **Pay models:** CPM, pool, per-post, retainer.
- **Creator fee:** 10% on current CPM campaigns. On per-post and retainer campaigns: 10% when the budget is under $5,000, 0% at $5,000 or more. Older campaigns can still show 7%. [contentrewards.com/articles/how-to-choose-a-clipping-platform](https://contentrewards.com/articles/how-to-choose-a-clipping-platform)
- **Withdrawals:** from $1. Standard payout up to 5 business days. Instant payout carries a fee. (verify the ACH fee: the research figure of $2.50 is not in current terms, O6)
- **Timing:** 7-day earning window after approval, then a 3-day hold on CPM payouts (earlier research; verify).
- **Source adapter:** scraper of the public Discover page, after terms check (O9). Manual paste fallback.

### 9.2 Vyro

- **Platforms:** TikTok, Instagram Reels, YouTube Shorts, X. [clipaffiliates.com](https://www.clipaffiliates.com/blog/clipaffiliates-vs-vyro)
- **Pay model:** per 1,000 eligible views. Typical listed rate $1 to $2 per 1,000 (research cluster).
- **Withdrawals:** Stripe or PayPal by location. $10 minimum, once a week. Earnings refresh hourly and stay pending until the campaign ends and a final verification runs.
- **Fee:** not published (O5).
- **Crypto:** third-party coverage lists it, but Vyro's own site names only Stripe and PayPal.
- **Source adapter:** scraper, after terms check (O9). Manual paste fallback.

### 9.3 Clipping.net

- **Platforms:** TikTok, Instagram Reels, YouTube Shorts, X. [findclout.com](https://findclout.com/blog/vyro-vs-clipping-net)
- **Pay model:** per 1,000 views, with per-campaign minimum view thresholds, often 10,000 to 100,000 views. [clipaffiliates.com](https://www.clipaffiliates.com/blog/clipping-cpm-rates-2026)
- **Listed rate:** $0.40 to $3 per 1,000 on the homepage. Bloomberg reported typical $0.30 to $1.50.
- **Withdrawals:** PayPal, USDC, USDT on Ethereum. Paid automatically when a cycle closes.
- **Fee:** not published (O5). The site labels the PayPal option "no fees" (verify).
- **Source adapter:** scraper, after terms check (O9). Manual paste fallback.

### 9.4 Elev8or Bounties

- **Platforms:** conflicting. The bounty page lists TikTok, Instagram, YouTube and X ([elev8or.io/bounty](https://www.elev8or.io/bounty)). Its blog lists Threads and LinkedIn as well ([elev8or.io/blog/whop-clipping](https://www.elev8or.io/blog/whop-clipping)). Another page says X is not tracked ([elev8or.io/blog/content-rewards-alternatives](https://www.elev8or.io/blog/content-rewards-alternatives)). Resolve with O4.
- **Pay model:** per 1,000 views. Rate, platforms and remaining budget shown on each bounty.
- **Withdrawals:** wallet with a low minimum. One source says $5 (verify).
- **Fee:** not found (verify).
- **Source adapter:** scraper, after terms check (O9). Manual paste fallback. X stays off until O4 is answered.

### 9.5 ClipFlip

- **Platforms:** Instagram, TikTok, YouTube. Snapchat, Facebook and X are listed as coming soon. [clipflip.io](https://clipflip.io/resources/creating-a-campaign)
- **Pay model, fees, withdrawals:** not verified.
- **Source adapter:** manual paste only until verified.

### 9.6 Direct programmes

- Podcasters, streamers, Discord programmes. Pay, fee and withdrawal per agreement.
- Source adapter: none. Manual intake with a signed brief.

### 9.7 Per-marketplace pipeline settings

Each marketplace record drives the pipeline:
- Pay model and fee rule, so net pay is calculated correctly.
- Hold timing, so the ledger status is right (Content Rewards: 7 days plus 3 days; Vyro: pending until the campaign ends).
- Minimum view thresholds, so clips below the threshold are flagged as not paying.
- Platforms, so only allowed platforms are offered in the Queue.

---

## 10. Adapters

### 10.1 Principles

- One interface per service. The business layer never calls a vendor directly.
- Plans, limits, prices and keys are configuration.
- Every adapter reports quota used, quota left, and cost per call. Alerts fire before a limit blocks work.
- Each adapter declares a fallback. After three failures the fallback runs and an alert fires.
- Every stage can be done by hand. Automation is optional.
- Each source adapter stores the marketplace's terms version and the date read.

### 10.2 Adapter list

| # | Adapter | Default | Alternatives | Fallback |
|---|---|---|---|---|
| A1 | Campaign source | Scraper per marketplace (after terms check) | Official API if one exists | Paste + extraction |
| A2 | Rules extraction (LLM) | Mid-tier LLM with JSON output, Azure OpenAI on credit | Other LLMs | Human entry |
| A3 | Transcription | Deepgram Nova-3 | AssemblyAI, ElevenLabs Scribe, local Whisper | Local Whisper |
| A4 | Moment finding | Vizard | OpusClip (Business quote), Klap | Manual selection in the review tool |
| A5 | Rendering | FFmpeg (own code) | Vendor render from A4 | Centre crop |
| A6 | Captions and overlay | FFmpeg | Vendor captions from A4 | Plain text overlay |
| A7 | Scheduler | Buffer free (from week 4) | Buffer paid, Metricool, Later | Manual posting |
| A8 | Storage | Cloudflare R2 free tier | Backblaze B2, Supabase Storage, Azure Blob | Local disk |
| A9 | Database and auth | Supabase | Neon with separate auth | Local Postgres |
| A10 | Alerts | Telegram Bot API | Email digest | In-app only |
| A11 | Metrics | Manual entry, then platform export | Platform API where allowed | Screenshot entry |
| A12 | Payout records | Manual reconciliation per marketplace | Marketplace export if provided | Manual ledger |

### 10.3 Interfaces

- **Common:** `health()`, `quota()`, `cost(request)`, `config()`. Every call writes an audit row.
- **A1:** `list_campaigns(marketplace)`, `read_brief(ref)`, `check_changes(ref, last_hash)`.
- **A2:** `extract_rules(brief_text, category_schema)`: rules JSON, every rule quoted.
- **A3:** `transcribe(audio_path, language, dialect_hint)`: words with timestamps; `language` is `en` or `ar`.
- **A4:** `submit(source, options)`, `status(job_id)`, `clips(job_id)`.
- **A5:** `render(candidate, format)`. **A6:** `burn_captions(video, words, style)`, `burn_overlay(video, label, timing)`.
- **A7:** `schedule(post)`, `cancel(id)`, `status(id)`. Reports channels used, queued posts per channel, posts per day.
- **A8:** `put`, `get_signed_url(key, expiry)`, `delete`, `usage`.
- **A10:** `send(alert, severity, channel)`. Block alerts are sent twice on Telegram.
- **A11/A12:** `record_views(...)`, `record_payout(...)`. Entries, not API calls.

### 10.4 Moment finding (A4): the options

| Tool | API | Price (vendor data, Oct and Nov 2026) | Notes |
|---|---|---|---|
| Vizard | Included in its Creator and Business plans, with no separate API subscription ([docs.vizard.ai](https://docs.vizard.ai/docs/pricing)) | Creator about $14.50 a month on yearly billing, one credit per minute ([aitoolsbakery.com](https://aitoolsbakery.com/?p=18889)) | Max video 600 minutes, 10 GB, up to 4K. API jobs use the upload-minute pool. Rate limits: O7 |
| OpusClip | API on the Business plan per most sources. One source says limited API on Pro Beta and Max ([diyai.io](https://diyai.io/ai-tools/code-generation/opus-clip-pricing/)) | Starter $15, Pro $29; Business is quote-only ([eesel.ai](https://eesel.ai/blog/opusclip-pricing)) | Adapter ready, switched on after a Business quote (O10) |
| Klap | Self-serve API | $0.44 per input video, $0.32 per short, $0.48 per export; credit packs from $10 ([itechguides.com](https://www.itechguides.com/best-ai-video-clipping-api-klap-pricing-and-code/)) | Pay per clip. Costs add up at volume |
| Submagic | Business + API plan only | Custom pricing ([costbench.com](https://www.costbench.com/software/ai-video-editing-saas/submagic/)) | Not planned |

Default: Vizard. Each adapter is switched on in Settings. No pre-build test is required (owner decision).

### 10.5 Transcription and dialects (A3)

- Languages: English and Arabic.
- Dialects: all Arabic dialects are configurable per category and operator. Initial list: Modern Standard Arabic, Gulf, Egyptian, Levantine, Maghrebi. Codes can be added without code changes.
- Deepgram supports Arabic with 16 dialect codes, added January 27, 2026 ([developers.deepgram.com](https://developers.deepgram.com/changelog/2026/1/27)).
- Whisper has much higher error rates on dialects than on Modern Standard Arabic (research finding). Test each dialect.
- Bake-off per dialect: 10 minutes per dialect and language from the owner's real material. Score word error rate on a hand-checked transcript and caption-fix minutes per clip. Providers: Deepgram Nova-3, AssemblyAI (Universal-3.5 Pro for Arabic), ElevenLabs Scribe, local Whisper. The winner per dialect becomes its default in configuration.

### 10.6 Scheduler (A7): Buffer

- **Free plan:** 3 channels, 10 queued posts per channel, 1 user. A Start Page counts as one of the channels (earlier research; verify on buffer.com).
- **Paid plans:** Essentials $5 per channel a year on annual billing ($6 monthly). Team $10 per channel a year ($12 monthly). Verify before go-live.
- **Alert:** when channels used reach 2 of 3 on the free plan, and when the plan limit is reached.
- **Disclosure toggle (O3):** the research did not confirm that Buffer exposes the paid-partnership toggle. Assume it does not until checked. If not, the toggle is set by hand in the native app as a checklist step.

### 10.7 Storage, database, alerts

- **Storage (A8):** Cloudflare R2 free tier. Signed URLs with short expiry. Source deletion after the retention setting.
- **Database (A9):** Supabase. Free plan includes two projects, 500 MB database, 1 GB file storage, 5 GB egress and 50,000 monthly active users. Free projects pause after 7 days without database activity; paid plans are not paused (earlier research; verify on supabase.com). Decision (O8): keep-alive ping on the free plan until phase 2, then a paid plan.
- **Alerts (A10):** Telegram bot. Token in environment variables.

### 10.8 Cost caps per adapter

| Adapter | Plan | Basis | Default monthly cap | Alert at |
|---|---|---|---|---|
| A2 LLM | Azure credit ($100, 12 months) | per token | $10 | 80% |
| A3 Transcription | Deepgram pay-as-you-go | per minute | $10 | 80% |
| A4 Moment finding | Vizard Creator (or Klap, per clip) | per minute or per clip | $20 | 80% |
| A7 Scheduler | Buffer free, then paid | per channel | per plan | 2 of 3 channels |
| A8 Storage | R2 free tier | per GB | free tier | 80% |
| A9 Database | Supabase free, or Pro if O8 says upgrade | per plan | $25 | 80% |

When a cap is reached, the adapter pauses and a block alert fires.

---

## 11. Data model

Database: Postgres on Supabase. Every table has `id` (uuid), `created_at`, `updated_at`, `created_by`.

### 11.1 Tables

| Table | Key fields | Notes |
|---|---|---|
| `operators` | name, role, status, payout_ref, daily_hours_cap, languages[] | Owner at launch |
| `marketplaces` | name, base_url, pay_model, fee_rules, withdrawal_methods, hold_rules, platforms, source_adapter, terms_version, terms_read_at | Section 9 |
| `categories` | name (podcast, gaming, lifestyle, reddit_story), default_platforms, caption_language, disclosure_template_id, music_policy | |
| `campaigns` | marketplace_id, external_ref, category_id, status, deadline, owner_operator_id | Status: draft, reading, pending_approval, approved, paused, closed, rejected |
| `campaign_versions` | campaign_id, version_no, snapshot_hash, raw_text, read_at, rules_json, rate_per_1k, fee_pct, max_payout_per_clip, budget_total, budget_remaining, min_views_to_qualify, payout_model | New row per re-read; never overwritten |
| `campaign_rules` | version_id, rule_text_verbatim, category, requirement, source_quote | Each rule quotes its source |
| `source_assets` | campaign_id, origin_url, file_hash, duration_s, language, dialect, licence_ref, permission_ref, stored_path, delete_after | Permitted footage only |
| `transcripts` | source_id, language, dialect, provider, model, word_json_path, wer_estimate | Cached |
| `jobs` | type, source_id, campaign_version_id, status, attempts, cost_usd, error | Idempotent key: type + input hash |
| `candidates` | source_id, provider, start_s, end_s, score, reason, status | From A4 |
| `clips` | candidate_id, version_no, file_path, duration_s, caption_text, hashtags, language, compliance_status, status | Status: rendering, ready_for_review, accepted, queued, review_later, rejected, ready_to_post, posted, archived |
| `hooks` | clip_id, text, hook_type, chosen, rank | Agent B output |
| `reviews` | clip_id, reviewer_id, decision (accept, reject, review_later, undo), reason_code, notes, created_at | Every decision logged; undo references the original decision |
| `accounts` | platform, handle, owner_operator_id, verified, daily_cap, posting_window, status | One owner each |
| `posts` | clip_id, account_id, platform, posted_at, post_url, disclosure_toggle_on, checklist_json, scheduler_ref | Written by the owner |
| `metrics` | post_id, checked_at, window (h48, d7), views, watch_time_s, source | Snapshots, never edited |
| `payouts` | clip_id, campaign_version_id, status, gross_usd, fee_usd, withdrawal_fee_usd, net_usd, held_since, paid_at | Section 13 |
| `time_entries` | operator_id, task_type, started_at, ended_at, minutes, campaign_id, clip_id | Basis for net pay per hour |
| `sessions` | operator_id, started_at, ended_at, length_setting, clips_reviewed, accepted, rejected | Session summary source |
| `alerts` | type, severity, entity_type, entity_id, message, acknowledged_at, snoozed_until | Feeds Telegram and in-app |
| `settings` | key, value, default, min, max, scope, updated_by | Section 4.3 |
| `adapters` | stage, provider, plan, config_json, quota_used, quota_limit, active | Section 10 |
| `kill_switch` | active, set_by, set_at, reason | Single row |
| `audit_log` | actor_id, action, entity_type, entity_id, rule_version_id, before_hash, after_hash, at | Append-only |

### 11.2 Rules schema (`campaign_versions.rules_json`)

```json
{
  "campaign_ref": "string",
  "marketplace": "string",
  "version": 1,
  "read_at": "ISO-8601",
  "snapshot_hash": "sha256",
  "payout": {
    "model": "cpm | pool | per_post | retainer",
    "rate_per_1k": 1.0,
    "currency": "USD",
    "fee_pct": 10,
    "min_views_to_qualify": 0,
    "max_payout_per_clip": 200,
    "budget_total": 1000,
    "budget_remaining": 800
  },
  "platforms_allowed": ["tiktok", "instagram_reels", "youtube_shorts"],
  "languages_allowed": ["en", "ar"],
  "accounts": {"max_per_platform": 1, "second_account_allowed": false, "quote": "string"},
  "disclosure": {"required_by_brief": true, "forbidden_by_brief": false, "quote": "string"},
  "content_rules": [
    {"text": "verbatim", "category": "hashtag", "requirement": "required", "quote": "string"}
  ],
  "music_policy": "campaign_audio_only | licensed_only | none | unclear",
  "risk_flags": [],
  "hard_blocks": []
}
```

- Every rule carries a quote. A rule without one is stored as `unclear`.
- `unclear` in any required field makes the campaign `review`, never `pass`.

### 11.3 Audit log

- Append-only, enforced by database permissions (no UPDATE or DELETE).
- One row per state change, including settings and adapter changes.
- Every clip links to the campaign version it was approved against.

---

## 12. Production pipeline and compliance

### 12.1 Stages

| Stage | Input | Output | Tool | On failure |
|---|---|---|---|---|
| S1 Intake | URL or scraped brief | Draft campaign version | A1 + Agent A | Re-run; owner edits |
| S2 Approval | Draft version | Approved version | Owner gate | Blocked until approved |
| S3 Source | Permitted file | `source_assets`, hash | Manual upload | Hash mismatch stops the job |
| S4 Transcribe | Audio, language, dialect | Word JSON, SRT | A3 | Retry 3 times, then fallback, then alert |
| S5 Moments | Source, options | `candidates` | A4 | Retry, then manual selection |
| S6 Hooks | Candidate | `hooks` | Agent B | Empty set re-prompted |
| S7 Render | Candidate, hook | 9:16 video | A5 | Falls back to centre crop |
| S8 Captions and overlay | Transcript, template | Burned video | A6 | Blocks if disclosure text missing |
| S9 Compliance | Clip, campaign version | Compliance report | Agent C + checks | Hard fail blocks packaging |
| S10 Package | Clip, hooks, caption, checklist | Review card | Backend | None |

Rules:
- Deterministic checks run before any LLM check: duration, aspect 9:16, caption starts with disclosure, overlay present, banned words.
- The LLM never overrides a deterministic failure.
- Every job records `cost_usd`.

### 12.2 Agents

Agents propose. They never approve, publish or change rules.

- **Agent A (rules):** input is campaign text and category schema; output is rules JSON with quotes. May flag `unclear` and hard blocks. May not approve or infer rules. Test: 10 real briefs.
- **Agent B (hooks and ranking, D12 and D13):** input is transcript segments, adapter candidates and category signals; output is hook options with types and a ranking of candidates. May rank and tag. May not choose the final hook or invent quotes. Test: 20 episodes against the owner's picks.
- **Agent C (compliance):** checks clip text and overlay against campaign rules; outputs pass, review or fail with evidence. May not certify legal compliance, judge copyright or verify licences. Test: 50 labelled clips; any rule above 10% disagreement is rewritten.

### 12.3 Disclosure

- **Caption:** the disclosure is the first text, in the clip's language.
  - English: `#ad`, or the wording the brief accepts.
  - Arabic: the Arabic equivalent, first in the caption.
- **On-screen label:** visible for the full clip duration, in the clip's language.
- **Platform toggle:** the paid-partnership or branded-content toggle is switched on at posting. TikTok's rules say hashtags alone are not enough, so the toggle is required (earlier research; verify on TikTok's help centre).
- **Templates:** configurable per category and language (D11). The owner decides the wording with their own advice; the system enforces that the template is present.
- **Brief forbids disclosure:** the campaign is rejected.

### 12.4 Hard blocks

- Gambling or casino campaigns.
- Finance or crypto without a licensed client reference.
- Copyright or music content without a licence on file.
- Reddit-style stories without original or licensed proof.
- Briefs requiring more accounts than the setting allows.

### 12.5 Rule-change detection

- Snapshot each brief at read time. Before each posting batch, re-check and diff. Any change blocks the batch until re-approved.

---

## 13. Posting, payouts and alerts

### 13.1 Payout statuses

`submitted → tracking → approved → held → paid`, with terminal `rejected`, `capped`, `budget_exhausted`.

- `tracking`: views still accruing.
- `held`: marketplace hold (Content Rewards: 3 days after the earning period).
- `capped`: per-clip maximum reached.

### 13.2 Fees and net

- Net = gross − creator fee − withdrawal fee. Fee rate stored per campaign version.
- Content Rewards creator fee: 10% on CPM and pool; 10% on per-post and retainer under $5,000; 0% at $5,000 or more; legacy 7% possible (section 9.1).
- Withdrawal fees are stored per method. ACH fee unknown until O6 is answered.

### 13.3 Routine

- Weekly: reconcile views and status against each marketplace's own screens.
- Monthly: batch withdrawals; export CSV.
- Screenshots at 48 hours and 7 days for any held or disputed clip.

### 13.4 Alert rules

| Alert | Trigger | Severity |
|---|---|---|
| Deadline | Within 48 hours | warn |
| Budget low | Below threshold | warn |
| Budget gone | Zero remaining | block |
| Rule change | Snapshot diff | block (batch) |
| Payout held | Over 14 days | warn |
| Payout disputed | Owner marks dispute | warn |
| Account health | Rolling 48-hour median below threshold vs 4-week baseline | block (pauses account) |
| Adapter quota | Within 20% of limit | warn |
| Kill switch | Activated | info |

Channels: Telegram (block alerts sent twice), and the in-app inbox.

### 13.5 Account health

- Weekly manual check of each account's analytics for eligibility or restriction notices.
- Notices recorded in `accounts.status`. A restricted account is excluded from production.

---

## 14. Analytics and go / no-go

### 14.1 Metrics

- **Net pay per hour** = net earned (paid, and pending shown separately) ÷ logged hours. By campaign, niche, language and week.
- **Clips clearing threshold** = clips at or above `min_views_to_qualify` ÷ posted clips.
- **Median views** per clip by niche, language and platform.
- **Hook win rate** by hook type: median 48-hour views relative to niche median.
- **Rejection rate** and reasons.
- **Compliance disagreement** per rule.

### 14.2 Go / iterate / stop (weeks 5 to 8)

| Outcome | Median net pay per hour | Action |
|---|---|---|
| Go | At or above about $4.60 and rising | Scale: more campaigns, accounts or operators |
| Iterate | $2 to $4.60 | Change campaigns, niches, languages or hooks; four more weeks |
| Stop | Under $2, or over 30% of clips rejected or below minimums | Stop or pivot |

These thresholds come from the $600 target and a 130-hour month. They are a framework, not market data.

### 14.3 Tuning levers (in order)

1. Hit rate (clips clearing threshold).
2. Campaign selection by net pay per hour.
3. Per-clip cap and remaining budget.
4. Language and platform mix within campaign rules.
5. Volume (last, within caps).

### 14.4 Forecasting

- Published rate ranges until 30 days of the owner's data exist.
- Confidence label: low until 30 days, medium until 90, high after.
- Always show a range.

---

## 15. Infrastructure, security and testing

### 15.1 Phase 1 (MVP)

- Laptop runs the Python worker, FFmpeg and the orchestrator.
- Supabase for Postgres, auth and small storage (see O8 for pausing).
- Cloudflare R2 free tier for previews.
- Next.js PWA on a free host (verify limits).
- Telegram bot for alerts.
- Deepgram for transcription.
- Azure credit for LLM calls (D9).

### 15.2 Phase 2

- Always-on small VPS for the worker (about $6 to $12 a month, est.).
- Scheduled jobs and backups.

### 15.3 Backups

- Daily Postgres backup (Supabase point-in-time on paid plans; daily export on free).
- Weekly CSV export of audit log and ledger, kept in two places.
- Clips rebuild from source and rules; source kept only for the retention setting.

### 15.4 Security and privacy

- Row-level security on every table, by owner and role.
- No platform passwords stored. The owner logs into each platform.
- Secrets in environment variables, never in the repository.
- Signed, short-lived URLs for all media.
- Source deleted after retention. Audit log append-only.
- Store names, handles and payout reference IDs only. Never bank details.

### 15.5 Testing

- **Transcription bake-off** per dialect (section 10.5).
- **Golden sets:** 10 briefs (Agent A), 20 episodes (Agent B), 50 clips (Agent C).
- **Deterministic:** duration, aspect, caption starts with disclosure, overlay present, banned words.
- **Rules engine:** blocked categories fail; `unclear` never passes.
- **Ledger:** state changes only in allowed order.
- **Settings:** values stay within bounds; each change writes an audit row.
- **Kill switch:** no new jobs and no ready packages while active.
- **UI:** accept and reject gestures; reason required on reject; Undo restores the clip within 10 seconds; session ends at its length and shows the summary.

### 15.6 MVP acceptance

- A campaign goes from intake to approval, produces a packaged clip, is accepted in the feed, is posted through the checklist, and has views and payout logged.
- The audit log records each step.
- The kill switch stops all new jobs within one minute.
- Median net pay per hour is calculated from the ledger.

---

## 16. Cost model (USD per month)

| Item | MVP (1 owner) | Full (1 owner) |
|---|---|---|
| Hosting | 0 (laptop) | 6 to 12 (est.) |
| Database and storage (Supabase free, R2 free) | 0 | 0 to 25 (25 if O8 says upgrade) |
| Transcription (about 1,200 min a month at $0.0043 to $0.0052/min) | 5 to 7 | 5 to 15 |
| LLM (est.) | Azure credit (covered for about 12 months) | 5 to 20 (after credit) |
| Moment finding (Vizard Creator) | about 14.50 | about 14.50 |
| Scheduler (Buffer free, then 3 to 4 channels at $5 annual) | 0 | 15 to 20 |
| Alerts (Telegram) | 0 | 0 |
| Withdrawal fees (example: $2.50 per payout, unverified) | 2.50 to 5 | 5 |
| **Total (estimate)** | **about 22 to 27** | **about 50 to 100** |

One-time: about 60 to 120 hours of build time for the MVP (est.).

---

## 17. Delivery plan

**Weeks 1 to 2**
- Intake (paste and scrape adapters for the first marketplace), Agent A extraction, rules schema, campaign approval gate, hard blocks.
- Done when: five real briefs are entered, extracted, reviewed, and approved or rejected with reasons.

**Weeks 2 to 4**
- Transcription bake-off per dialect. Choose defaults.
- Moment-finding adapter (Vizard) and render, caption and disclosure stages, for one niche and one language.
- Done when: 10 clips are produced with correct disclosure and caption language.

**Weeks 3 to 5**
- Mobile web app: Home, Feed, Queue, More (Review Later, Ledger, Alerts, Settings).
- Session timer, Undo, reason codes, hooks sheet, publish checklist.
- Done when: 40 clips are reviewed in one session with reasons logged.

**Week 4 onward**
- Buffer scheduler adapter (free plan).
- Posting checklist, post log, 48-hour and 7-day metrics.
- Ledger, net-per-hour report, Telegram alerts, kill switch, audit log.

**Weeks 5 to 8**
- Run the operation, measure, decide go, iterate or stop (section 14.2).
- Add more operators, accounts or adapters only through settings.

**Later**
- Episode radar, evergreen re-clipping, hook library, own moment finding, YouTube API posting, extra marketplaces and niches, always-on server.

---

## 18. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Account restriction for unoriginal content | Medium to high | High | Real edits per clip; no third-party watermarks; caps; health alert |
| Scraping blocked or terms change | Medium | High | Adapter per marketplace; paste fallback; terms recorded before go-live |
| Payout holds or rejections | High | High | Ledger statuses; screenshots; clear campaigns |
| Budget closes mid-flight | High | Medium | Re-check before each batch; budget alert |
| Rule changes after approval | Medium | Medium | Snapshot diff; batch blocked until re-approved |
| Copyright or music claim | Medium | High | Campaign audio only; Reddit stories only with proof |
| Disclosure template wrong for an audience | Medium | High | Configurable templates; checklist; owner review |
| Weak dialect transcription | Medium to high | Medium | Per-dialect bake-off; caption-fix metric; adapter |
| Hook quality does not transfer | High | Medium | Hook-type scoreboard; median decisions |
| Supabase pauses the project | Medium | High | Keep-alive job with a failure alert (O8); paid plan in phase 2 |
| Vizard or Buffer quota or plan changes | Medium | Medium | Quota alerts; adapter swap |
| Overbuilding before earning | High | High | MVP scope; 8-week go/no-go |
| Marketplace counterparty risk | Medium | High | Two or more marketplaces; published terms preferred |
| Tool lock-in | Low to medium | Medium | Postgres ownership; open formats; adapters |

---

## 19. Glossary

- **Campaign:** a paid clipping programme funded by a brand, creator or platform.
- **Brief:** a campaign's written rules.
- **CPM:** payout per 1,000 verified views.
- **Earning period and hold:** time views count, and the wait before payout clears.
- **Unaudited API client:** an app not yet reviewed by a platform; restricted (for example, private-only TikTok posts).
- **Disclosure:** the label marking paid content.
- **Net pay per hour:** net earnings ÷ logged hours.
- **Adapter:** the module that connects one service to the system.

---

## 20. Sources

Several are vendor or affiliate pages. Verify key figures before relying on them.

- TikTok Content Sharing Guidelines and Content Posting API.
- Instagram Graph API content publishing.
- YouTube Data API revision history.
- Content Rewards creator terms and FAQ: [contentrewards.com](https://contentrewards.com/articles/how-to-choose-a-clipping-platform).
- Vyro: [clipaffiliates.com](https://www.clipaffiliates.com/blog/clipaffiliates-vs-vyro).
- Clipping.net: [findclout.com](https://findclout.com/blog/vyro-vs-clipping-net), [clipaffiliates.com](https://www.clipaffiliates.com/blog/clipping-cpm-rates-2026).
- Elev8or: [elev8or.io/bounty](https://www.elev8or.io/bounty), [elev8or.io/blog/content-rewards-alternatives](https://www.elev8or.io/blog/content-rewards-alternatives).
- ClipFlip: [clipflip.io](https://clipflip.io/resources/creating-a-campaign).
- Vizard API: [docs.vizard.ai](https://docs.vizard.ai/docs/pricing); price [aitoolsbakery.com](https://aitoolsbakery.com/?p=18889).
- OpusClip: [eesel.ai](https://eesel.ai/blog/opusclip-pricing), [diyai.io](https://diyai.io/ai-tools/code-generation/opus-clip-pricing/), [ssemble.com](https://ssemble.com/blog/opus-clip-review-2026).
- Klap: [itechguides.com](https://www.itechguides.com/best-ai-video-clipping-api-klap-pricing-and-code/).
- Submagic: [costbench.com](https://www.costbench.com/software/ai-video-editing-saas/submagic/).
- Deepgram Arabic dialects: [developers.deepgram.com](https://developers.deepgram.com/changelog/2026/1/27).
- Buffer: earlier research on Free, Essentials and Team prices. Verify on buffer.com before go-live.
- Supabase: earlier research on free-plan limits and pausing. Verify on supabase.com.
- Azure for Students: earlier research and the owner's verified account. Verify the credit terms and renewal rule.
- TikTok branded content: earlier research. Verify on TikTok's help centre.
- Research papers: Whisper (arXiv:2306.02902), Arabic dialect study (arXiv:2511.18774).

---

## 21. Audit log: what changed from earlier versions

This section records every fix made in this consolidation, so the reasons are traceable.

**Spec v0.2 corrections**
- Section 1.3 said "no scraping". Changed to D1: scraping allowed per marketplace after a terms check.
- Section 3 said campaigns are entered by paste only. Changed to adapter-based intake with paste fallback.
- Section 10.1 said manual posting only. Changed to D3: manual weeks 1 to 3, scheduler from week 4.
- Section 2 and the cost table used the $2.50 ACH fee as fact. Marked unknown until O6.
- Section 8.1 had an open lawyer question. Removed as a build item per D11; templates stay configurable.
- Section 14 said Supabase free was fine. Added the 7-day pause (O8).
- Section 14 said Azure was "if eligible". Confirmed by the owner's verified account (D9).
- Section 16.1 said Arabic in "target dialect(s)". Changed to all configurable dialects with a per-dialect bake-off.
- Section 20 open decisions were outdated. Replaced by sections 2 and 3.
- Data model: added statuses `queued` and `review_later`, the `sessions` table, dialect fields, scheduler reference, and the `reviews` undo.

**Decisions log v0.4 corrections**
- D2 said the Vizard test was pending. Owner decided no test is needed (both adapters supported). Replaced by the default-and-switch rule in D2.
- Section 3 showed Vyro platforms as "mainly TikTok, Instagram, YouTube (verify)". Verified as four platforms including X.
- Section 3 showed the ACH $2.50 as current. Corrected to "unknown" (O6).
- Section 4 open items were partly closed. Re-stated in section 3.
- UX decisions (D16 to D19) and the scraping decision (D1) were not in the log. Added.

**Adapters spec v0.1 corrections**
- A4 said "Vizard API (test pending)". Changed to "Vizard default, both supported" (D2).
- Section 5 test plan removed as a build step (owner decision). Kept as an optional check.
- Section 7 Buffer: added the Start Page channel rule and the verified paid prices.
- Section 8 Supabase: replaced "limits to verify" with verified limits and the pause rule.
- Section 9 Azure: "verify expiry" replaced with the verified 12-month renewable terms.
- Section 11 open items: Buffer price, Azure and Supabase are now answered; Vizard rate limits and the Elev8or X question remain.

**UX spec v0.2 corrections**
- Section 3.2 pointed to section 8 for returning Review Later clips. Section 8 is now closed, so the reference is removed.
- Session length, Undo, pay display and priority-weight settings are in both the decision list and Settings.

**Other items to confirm (owner)**
- D13: Agent B does hooks and re-ranking; moment finding comes from the adapter. This is my reading of "not our own, next step". Confirm or change (O1).
- D2 default: Vizard. Change in Settings if you prefer another.

**Second pass (same day)**
- Owner name removed from the header; the document says "you".
- D12 restored to the moment-finding budget, matching the decisions log. The Agent B scope point is now D13.
- O12 added for the moment-finding budget and the pay-display default.
- Links that were not traced to earlier research were removed. Facts from earlier research are kept and marked "verify".
- The unsupported Deepgram credit was removed from the cost table, and the totals were recalculated.
- Settings bounds are marked as proposals.

---

## 22. Change log

- v1.0 (2026-11-16): consolidated from spec v0.2, decisions v0.4, adapters v0.1 and UX v0.2, with the audit fixes in section 21.
- v1.0 second pass (2026-11-16): citation and consistency fixes, see section 21.
