# Open items

Date: 2026-11-16. The system runs locally on fakes today. Each item below says what it blocks.

## 1. Keys and accounts to supply

Put these in `.env` (copy `.env.example`). Until then the fake adapter for that stage is used.

| Item | Blocks | Spec ref |
|---|---|---|
| Telegram bot token and chat ID | Telegram alerts. In-app alerts work without it. | O11 |
| Supabase URL and keys, or a `DATABASE_URL` | Hosted database. The local database works without it. | O8 |
| Azure OpenAI endpoint, key and deployment, plus the price per 1,000 tokens in the `adapters` table | Real rule extraction, hooks and compliance checks. | D9 |
| Deepgram key | Real transcription. | D6 |
| Vizard key (or Klap key) | Real moment finding. Vizard also needs public source links, so R2 storage first. | D2 |
| Cloudflare R2 keys and `pip install boto3` | Hosted storage. | A8 |
| Buffer access token | Scheduler from week 4. | D3 |

## 2. Checks only you can make

| Item | Blocks | Spec ref |
|---|---|---|
| Read each marketplace's terms, then record the version and date | That marketplace's scraper. The database refuses to switch it on without them. Manual paste works now. | O9 |
| Buffer paid-partnership toggle | Nothing. It is treated as missing, so the toggle is a manual checklist step. | O3 |
| Elev8or platform list | X stays off for Elev8or. | O4 |
| Vyro and Clipping.net fees | Net pay for those marketplaces shows "fee unknown". | O5 |
| Content Rewards ACH withdrawal fee | Withdrawal fee is stored as unknown. | O6 |
| Moment-finding monthly budget | Nothing. The cap is $20 until you change it in Settings. | O12 |
| Arabic dialect bake-off | Nothing. Modern Standard Arabic is used until results exist. | O2 |
| Vizard rate limits | Nothing yet. | O7 |
| OpusClip Business quote | The OpusClip adapter. It cannot be switched on until a quote is recorded. | O10 |
| Confirm Agent B scope (hooks and ranking only) | Nothing. Built as D13 describes. | O1 |
| Confirm the settings bounds | Nothing. Built as proposed in spec 4.3. | 4.3 |
| Buffer, Supabase, Azure and TikTok figures marked "verify" in the spec | Nothing in the build. They are stored as configuration. | 20 |

## 3. Written but not tested against a live service

No test calls a live vendor. These adapters are untested outside the fakes.

- **Deepgram, Azure OpenAI, Telegram, R2.** Written against their documented APIs.
- **Vizard, Klap, Buffer.** Request shapes are best-effort from memory and must be checked against current vendor documentation before use. If a response lacks clip start and end times, the adapter stops and the pipeline falls back to manual selection.
- **OpusClip.** A placeholder only. There is no request code until a quote and API documentation exist.
- **Marketplace scraper.** A generic page reader with per-marketplace settings. No marketplace page layout has been checked, and it is off.
- **Supabase sign-in mode.** Written, not run against a live project.
- **Installed PWA on an iPhone.** The app was checked in a desktop browser at phone size. It has not been installed on a real device, and push notifications are not built (Telegram is the alert channel).

## 4. Not built in this run

- **Desktop campaign board with drag and drop, pipeline view and audit view** (spec 6.9). The keyboard shortcuts are built and the layout works on a desktop.
- **Scheduling screen in the app.** The scheduler adapter and its API call exist and are tested with the fake; the Queue screen has no "schedule" button yet.
- **Deleting transcripts and clips after a campaign closes.** The retention setting exists; only source-footage deletion runs.
- **Backups** (spec 15.3): daily database backup and weekly export. The monthly CSV export exists.
- **Posting the same clip to several platforms** (decision 15).
- **Arabic labels on screens other than navigation and review** (decision 30).
- **Items the spec marks [Later]:** direct-programme intake as a source, episode radar, evergreen re-clipping, hook library, own moment finding, direct API posting.

## Round 2, phase B (2026-11-17)

- **Not yet run with a real passkey.** Passkey sign-in is tested with the WebAuthn verification mocked; the browser preview cannot complete a real passkey. First real use: your own laptop and phone.
- **`python run.py phone` has not been run.** Tailscale is not installed on this laptop yet. The `--set-path /api` routing is written from Tailscale's documented options and needs one real check.
- **YouTube links need the optional `yt-dlp` package**, which is not installed. The app refuses YouTube links until it is, and always asks you to confirm the brief permits clipping that video.

## Round 2, phase C (2026-11-17)

- **ClippingAlpha rate limits are not published.** A live pass of 12+ searches plus two calls per brief got HTTP 429 after 9 briefs. The app now spaces calls 1.5 s apart (setting `min_seconds_between_calls` on the adapter), stops the pass on the first 429 without retrying or falling back, waits out `Retry-After` (at least 60 s, 10 min if not given), and refuses "Check now" more than once per 10 minutes. The right spacing still needs a few days of real passes; raise it if 429s keep showing in Alerts.
- **Briefs are read 25 per pass**, best first, and again after 3 days.
- **The "cautious per clip" figure is often $0.00.** It is the 25th-percentile outcome, so it reads $0 whenever fewer than three in four clips would clear the minimum. This is honest but blunt; revisit once real view data exists (phase F).
- **Audience country share is assumed** (30%, or 5% for Arabic accounts) until it is read from the platform or a screenshot, or typed in.
- **Feed data is ClippingAlpha's, CC BY 4.0.** Explore credits it; the marketplace page stays the source of truth and is linked from every campaign.

### Phase A–C re-check (2026-11-17)

Fixed in the re-check:
- Every feed pass wiped the briefs already read, so the same 25 were read again each time (the main cause of the 429s). Briefs are now kept and read again only after 3 days.
- The feed connector opened a new MCP session for every call (two requests each). It now keeps one session and renews it if the server forgets it. A full pass went from about 126 requests to about 63.
- "Not for me" and "Added" were shared by every owner. They are now each owner's own (migration 007, `listing_choices`).
- Ranking used every owner's accounts when an admin looked. It now uses only the viewer's own accounts.
- A campaign page failed to open while the feed was slowed down. It now opens with what is known and says the brief is not read yet.
- Words such as "famous" or "campus" counted as a US audience rule. Country words are now matched as whole words.
- A budget over 90% spent now lowers the chance the budget lasts.
- Campaigns your clips would rarely clear (under 10%) are hidden by default, with a count and "Show them".
- New Explore filters: how it pays, views needed, days until paid, budget left, and "no audience country rule" (for Arabic clips).

Closed in phase C (migration 008, tests in `test_8_phase_c_complete.py`):
- **Audience countries** are read from the official APIs (YouTube Analytics, Instagram insights; tokens from the env vars `YOUTUBE_ANALYTICS_TOKEN` and `INSTAGRAM_GRAPH_TOKEN`, never stored), once a week per account, or from a screenshot of the platform's audience page (English or Arabic). TikTok has no audience API for us until its app review, so TikTok uses the screenshot. Typed-in values are never overwritten.
- **Arabic accounts:** each account records the language it posts in. An Arabic account with no audience data assumes 5% Tier-1 viewers (setting `assumed_tier1_pct_arabic`), so campaigns with no country rule rank above Tier-1-only ones, with the reason "fits Arabic clips". A brief that asks for another language does not count that account.
- **Fixed-pay campaigns first while accounts are new** (fewer than `new_account_posts`, 10, tracked posts). The feed has none today; the rule is ready for when it does.
- **Payout record:** your own settled payouts on a marketplace (5 or more) set the approval odds; before that, the feed's approval figure for the campaign; before that, 85%.
- **Spreading risk:** while you have two or more approved campaigns, one campaign takes at most 40% of the week's clips (`portfolio_max_share_pct`). Extra moments are kept and can be cut by hand.
- **Fresh budgets first:** the worker takes jobs for the campaign whose budget runs out soonest first.
- **Short transactions:** a discovery pass now saves after every query and every brief. Live: 103 campaigns visible after 15 seconds, longest transaction 5 seconds (was one of about 3 minutes).
- **Unread briefs** no longer rank as if every clip gets paid: until the brief is read, the typical minimum payout of the briefs already read is assumed, and the reason says so.

Still needs you or a later phase:
- **Tokens:** YouTube and Instagram audience reading needs the two tokens above; until then use screenshots. Not tested against the live APIs.
- **The 1.5-second spacing** between feed calls held for a full live pass (no 429). Watch Alerts for a few days.

## Phase D, step 6: quality gate, brand checks, end to end (2026-11-18)

Built (migration 014, tests in `test_14_quality.py` and `web/tests/quality.test.tsx`):
- **Chance of clearing the minimum.** Every clip in Review carries `quality` (the chance of reaching the campaign's minimum views, a sentence saying why, and whether the numbers are "your posts" or "assumed"). Clips under `quality_min_p_clear` (default 25%) go to the bottom with a "Weak" label; nothing is hidden. The moment's quality moves the account's typical views by 35% of the account's spread (`MOMENT_SKILL` in `review.py`): a guess that real results will tune in phase F.
- **Brand checks.** A marketplace's rejection reason (Queue ← a clip ← "The marketplace rejected this clip") is read by plain rules, with no AI. A banned phrase blocks the brand's next clips that use it; a missing tag or phrase sends them to you as "review"; a vague reason is kept as a reminder and blocks nothing. Checks can be switched off (`POST /api/brand-checks/{id}/active`).
- **Correction queue** puts the words inside likely clip moments first, strongest moment first.
- **Signals** now follow transcript corrections whenever they are loaded (not only when recomputed), and a reaction phrase said over silence no longer counts.
- **Demo.** `python run.py demo` always uses the fakes (before, it used whatever `.env` held, which meant live services). Each stage prints the provider that really ran. `python run.py demo --no-keys` runs with fakes off and every key blanked.

Still open:
- **Live check at 375 px and desktop** of Review and the correction screen is done by the owner's session (the app needs a passkey). The tests cover the components and audit the stylesheet for overflow and tap sizes.
- **The no-keys demo runs on the seeded stand-ins.** With fakes off and no keys, the adapters that need keys simply stay on their fakes, so `demo --no-keys` completes but does not run local Whisper. Local Whisper (Phase 1) and signals-only ranking (Phase 4, and a rolled-back check in the demo itself) are proven by their own tests. A real Whisper run in the demo would download the 1.6 GB model into every throwaway data directory.
- **A brand is the listing's brand, else the campaign's name.** Two campaigns of one brand that were added by hand share nothing until they come from the same listing.
- **Brand rules read English and Arabic cue words only.** A reason in another language becomes a reminder.

- Under `pytest -n auto` with the app's own worker running, 4 load-sensitive tests (3 render template tests in test_13, the 2-hour signals speed test in test_11) can fail; they pass alone. Make them GPU/time tolerant or mark them serial.
- DW-6.4 live check of the review and correction screens on a real phone (375 px) is left to the owner's pilot use; component tests and the stylesheet audit passed.
- Facebook Reels (and later X) as extra posting platforms: not in Phase E's three. Facebook posts through the same Meta app as Instagram, but needs a personal profile plus a Page (extra verification). Add after the first three platforms earn.

---

## Phase E (2026-11-19)

Phase E is built and pushed on `feature/phase-e-posting`. Everything was tested on fakes. Nothing real has been posted yet.

### Live checks not run yet
- **OAuth redirects on Meta and TikTok.** Both need an HTTPS redirect (Tailscale `ts.net`). Blocks connecting Instagram and TikTok on a real account.
- **Instagram resumable upload and permalink.** Blocks a real Reels post and reading its link.
- **YouTube private lock and paid-promotion flag.** Blocks confirming that uploads land private and that the flag is set.
- **TikTok inbox upload and its caption limit.** Blocks a real TikTok draft and the caption check.
- **PKCE for TikTok.** Only Google uses PKCE for now. Blocks TikTok sign-in until checked.
- **First real test.** One real campaign, one post per platform, each result checked by hand. Blocks any claim that posting works.

### Audits and reviews
- **Google audit.** Until it passes, YouTube uploads stay private.
- **TikTok audit.** Until it passes, TikTok posts stay drafts.
- **Meta App Review.** Needed before accounts other than the owner's test accounts can connect.
- **Combined E+F review.** Phase 4 (the posting screen) has its review deferred. It runs together with phase و.

### Known gaps
- **Quota ledger can undercount.** If the process dies in the middle of a call, the count can miss that call.
- **No manual path for connected platforms.** The owner cannot post by hand to a platform that is connected.
- **Server error messages stay in English in Arabic mode.** The screens are bilingual, but some server messages are not translated yet.

### Owner setup (not a code task)
- **Developer apps.** Create the Google, Meta and TikTok apps, and add each redirect URL. Steps are in `docs/خطة-المتابعة.md`.
- **`.env` values.** Add the client ids, secrets, `OAUTH_REDIRECT_BASE` and a new `TOKEN_VAULT_KEY`. Never paste them in chat.

## Owner notes (2026-11-19)

Done in small commits (see `docs/owner-notes.md`): Home layout, Review wording and chips, Review Later, Profile tidy-up.
- **Review order** ("scroll without deciding; decided clips never return, skipped ones come back after a few"). Waiting for the owner to confirm the rule. Priority: high. Size: 1–2 h, touches the review queue logic.
- **Profile full redesign**, if the tidy-up is not enough. Priority: low. Size: 2 h.

## Per-niche clip length from market study 2 (2026-11-20)
The study (docs/market-style/render-profile-defaults.md) suggests podcast 15 to 45 s EN and 15 to 60 s AR, gaming 12 to 30 s EN and 25 to 55 s AR, business 30 to 70 s EN and 45 to 90 s AR. The code's only knob is the profile's `length_min_s` / `length_max_s` (`content_profiles.config`, seeded in migration 012: podcast 30 to 60, stream 20 to 45), which has no language dimension, so nothing was changed. Options: a language-aware length in `Profile.length_for`, or a data migration of the seeds after the owner picks a range. The v2 numbers sit in `backend/clipping/assets/render-profile-defaults.json` (`clip_length_s`) as guidance only. Priority: low. Size: 1 h.
