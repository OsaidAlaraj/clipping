# Final review (2026-11-20)

Branch `final-review`, cut from `feature/phase-e-posting` tip `4f766ae` (the worktree base was older; fast-forwarded first).
Evidence: "ran" = executed here (throwaway instance, API calls, browser, tests); "read" = reasoned from code only. Sizes are relative (S, M, L), not time.
Throwaway instance: own data dir, ports 18787 (API) and 13000 (web, webpack dev), fake adapters, no `.env`, no real accounts. Stopped and deleted at the end.
Tests: web 298 passed (ran); backend: the files I touched or that cover posting, review, security and captions pass (ran); the full backend run was still going when this was written, see the last line of this file.

Status key: works / partly / missing.

## 1. Cycle status

| # | Cycle | Status | One line |
|---|---|---|---|
| 1 | Sign-in, operator isolation | works (single owner) | RLS forced on all 47 tables (ran, queried `pg_class`); passkeys untested live; no rate limit on sign-in |
| 2 | Discovery, ranking, brief, practice | partly | Ranks and explains well; ranking is flat for a new account ("cautious $0.00" everywhere); no per-clip cap or runway model |
| 3 | Source intake, transcription, corrections | partly | Upload, link, Deepgram and Whisper wired; link intake can fetch internal addresses; whole audio read into memory |
| 4 | Moment engine | works | Three-layer judge, dedupe, campaign terms, learning; Arabic cue lists are thin |
| 5 | Render and quality gate | works (not judged on real footage) | All stages exist; the fixtures are synthetic, so look and sharpness are unverified; gate misses some failures |
| 6 | Caption lab, packaging, originality | partly | Matched and curious variants, checker, per-platform norms exist; hashtags are arbitrary spoken words |
| 7 | Review feed | works | Swipe, skip order, undo, manual-post bar all ran; refusals have no one-tap fix |
| 8 | Posting | works on fakes, never live | Vault, stagger, kill switch, triggers sound; first post could beat the Undo (fixed here) |
| 9 | Analytics, earnings, learning | partly | Ledger and learning from decisions exist; views and payouts are typed by hand, nothing is pulled |
| 10 | Backup, ops, start-up | partly | Daily dump and restore work; dump stays on the same disk; worker swallows errors; dev server for daily use |
| 11 | Accounts, niches, languages | partly | Model works; Arabic UI is half-translated, and bidi breaks on handles and numbers |

## 2. Fixed here (trivial, with tests)

1. **Swipe-right could post before Undo was possible.** `publish.py` `confirm_post`: when the owner is inside the account's window the first post was planned for "now", the worker polls every 2 s, and Undo is refused once a post leaves `scheduled` (security review M4). The swipe path now delays the first post by the Undo window plus grace plus 2 s. Test: `test_20_swipe_confirm.py::test_the_first_post_waits_for_the_undo_window_so_an_undo_can_still_stop_it`.
2. **Malformed id gave HTTP 500** (`/api/clips/abc`, `/api/campaigns/zzz`; ran). `api.py` new `DataError` handler returns 400 `bad_value`. Test in `test_22_security_review.py`.
3. **Duplicate hashtags in another letter case** (`#Everyone #everyone` on the seeded clip; ran). `packaging.py` `_fit_tags` dedupes case-insensitively. Test in `test_20_caption_lab.py`.

## 3. Defects by cycle

### 1. Sign-in and isolation
- `auth.py:117-125`, `api.py` auth routes: no rate limit or lockout on setup-code and passkey attempts. The setup code is 40 bits and valid 30 minutes. Reachable only on the tailnet or localhost, so low. Fix: count failures per address, 429 after 10.
- `api.py:72-88`: local mode caches one owner for the process and trusts every caller when `CLIPPING_LOCAL_TOKEN` is unset. Writes are protected by the origin check (security review M1); reads are not. Fix: refuse local mode when the API host is not loopback.
- `rls 003_rls.sql`: an admin reads every operator's rows by design. Fine for one owner; if a second operator ever joins, the admin account is a super-reader. Note only.
- Setup codes and challenge rows (`auth_challenges`) are never purged (only `oauth_states` are, `connections.py:99`). Low. Fix: delete expired rows in housekeeping.
- Verified (ran): the foreign-origin POST is refused with 403 `bad_origin`; out-of-range setting is refused; RLS is on and forced everywhere; `app_user` has no UPDATE or DELETE on `audit_log`.
- Not run: a real passkey (needs a device).

### 2. Discovery, ranking, brief, practice
- `ranking.py:166,180`: the score is the 25th percentile per hour. For a new account with the default assumption (`assumed_median_views` = 2,500, migration 006:145) against 5,000-view minimums, p25 is $0 for almost every campaign, so Explore and the listing page show "cautious $0.00 per clip" on a $4.00 CPM campaign (ran). Ranking falls back to the mean times 0.01. The owner gets no usable signal in week one. Fix: until 5 own posts exist, rank on the mean and say so; show the mean beside the cautious figure. Size S.
- `ranking.py:62-83`: the 2,500 default is optimistic for a brand-new account; real first posts usually get far fewer. Fix: default 300 until data. S.
- Missing: per-clip cap and budget runway as ranking inputs beyond `max_payout` and `drain_p50` (money map section 1); campaign-type check (per-post, retainer) is only a label.
- `ranking.py:188-225`: 1,000 simulations per listing per account in Python on every list call. 103 listings took 0.24 s (ran); fine now, grows with accounts. Cache per (listing, account) for an hour. S.
- Practice campaigns work end to end (ran): clip accepted by swipe, nothing queued.
- Brief re-check (`campaigns.py:271`) is mandatory before any post and valid 6 hours; see cycle 7 for the UX hole.

### 3. Source intake, transcription, corrections
- `sources.py:94,122` **link intake can fetch internal addresses.** The check is `^https?://`, so `http://127.0.0.1:18787/api/health` was accepted and fetched by the worker (ran; it shows in Progress as a failed video). Redirects are followed. Same for any LAN or tailnet host. Needs the owner's own paste, so medium. Fix: https only, refuse loopback, private, link-local and `.local` hosts after resolving, re-check on every redirect hop. Tests that use a local HTTP server (`test_6`) need an opt-in flag. S.
- `api.py` `SourceIn.file_path`: any readable path on the laptop can be registered if `permission_ref` is given. Single owner, low. Fix: only paths under the data dir or a configured media folder. S.
- `transcription.py:140`: the whole WAV is read into memory and posted in one request; Whisper's `read_wav` also loads it whole. A 2-hour source is hundreds of MB. Fix: chunk long sources in 20-minute pieces with overlap. M.
- No retry or backoff on Deepgram 429 or 5xx inside the adapter (relies on the registry fallback to Whisper). Fix: one retry with `Retry-After`. S.
- Language check at upload and the thin-transcript retry exist (ran in earlier tests, read here).
- Correction screen works at 375 px (ran): confidence-first queue, "Looks right", audio scrub.

### 4. Moment engine
- Hard gates, soft score, pairwise Bradley-Terry, cut-off with floor, rescue of set-aside moments, learning: all present (read).
- `moment_rank.py:42-55`: Arabic cue lists are 6 to 12 words each (weak openers, hype, claims). Egyptian, Gulf and Levantine phrasing is barely covered. Fix: grow the lists from the owner's own corrected transcripts. M.
- `moments.py`: one 60 s fixture gave 1 moment for the Arabic practice source (ran); target counts for short sources are fine, but no test shows a 1-hour source finishing in a bounded time on this laptop. Fix: add a timed 1-hour synthetic test. M.
- Campaign awareness works through `brief_terms` (read).

### 5. Render and quality gate
- `quality_gate.py:15`: `TALKING_NICHES` omits `streams` and `film-and-tv`, so those niches are never failed for thin speech or missing face. S.
- Gate checks words per second, face share, sentence end, word start, loudness, language, disclosure. It does not check: black or frozen frames, caption overflow, sharpness, first-3-seconds hook, silence at the end. Fix: add black-frame and caption-box checks (ffmpeg `blackdetect`). M.
- A gate `fail` only sinks the clip (`review.py:137`); swipe right still posts it (see cycle 7). Fix: block swipe on `no_disclosure` and `language_mismatch`. S.
- Not verified here: sharpness, speaker crop, tightening and zoom quality on real footage. The fixture is synthetic color bars (ran), so only layout, captions and the overlay were looked at. Arabic captions and the Arabic disclosure label rendered and shaped correctly in the review player (ran).

### 6. Caption lab, packaging, originality
- `caption_match.py:106`: a hashtag only has to be a word the clip says, so generic words become tags (`#ignore`, `#everyone` on the seeded clip; ran). Tags carry no reach. Fix: score candidates against the niche word list and drop stopword-like and generic verbs. M.
- Caption body "My take: founders and Everyone is the part worth thinking about here." (seeded clip; the fake model, but the commentary frame is a fixed template) reads as filler. Fix: the originality line must come from the model with the check, never a template, or be omitted. M.
- `packaging.py` norms are practitioner guidance; fine, labelled as such.

### 7. Review feed
- **Swipe refusal has no one-tap fix** (ran). With the brief check older than 6 hours, swipe right fails with "YouTube: The checklist is not complete: Brief re-checked and unchanged for this batch." and the clip returns to the feed. The owner must leave Review, open the campaign, find re-check, then return. Fix: the toast gets a "Re-check brief" button (`POST /api/campaigns/{id}/recheck`, `unchanged_confirmed` after showing the brief link), or the server re-checks automatically for listing-backed campaigns. Size S. Impact on income: high, because it blocks every first post of the day.
- The refusal names "YouTube" although the clip is an Instagram clip, because the first platform in the list fails first. S.
- `Feed.tsx:453,585`: the check button and key "A" call plain accept (no post), while swipe right posts. Two meanings of "accept" on one card, and desktop has no keyboard path to post. Fix: label the button "Accept" vs the swipe "Accept and post", or make desktop `Enter` = post. S.
- `Feed.tsx:585-592`: key handler ignores `event.repeat`; holding "A" accepts the next clip too. Fix: `if (event.repeat) return`. S.
- `review.py:177`: one hooks query per clip, `review.py:132-134` account stats per account; the feed has no page limit and signs a URL per clip. Fine at 3 clips (feed answered in 16 ms, ran), grows with 100+. Fix: batch the hooks query, cap the feed at 20 with "load more". S.
- `review.py:67-78`: priority uses the campaign's rate per 1,000 views, not expected earnings. A high-rate clip with a 30% chance of clearing the minimum ranks above a sure one. Fix: multiply `net_pay` by `quality.p_clear`. S. Impact on income: medium.
- `review.py:331-359`: Undo of a swipe goes through `publish.swipe_undo`, which cancels targets; undo of a plain accept does not touch targets but none exist. OK.
- Verified (ran): swipe right with brief fresh gives "Ready to post manually" with copy caption and download buttons for unconnected accounts, 10 s Undo; practice clip accepted and nothing queued; drag handler cost 0.1 ms per move (200 synthetic moves, dev build), no layout shift.

### 8. Posting
- Fixed: first-post timing (section 2).
- `migrations 015, 017` security review L4 and L5 remain open (kill switch does not cancel Instagram `processing`; `search_path` without `pg_temp`). Both read, not run. Fix: a new migration. S.
- `publish.py:430`: swipe checks only `compliance_status`; gate failures pass. See cycle 5.
- Swipe confirm stands in for three owner ticks and never claims the disclosure toggle (read, by design). For manual targets the owner must still set the paid-partnership toggle in the app; nothing in the "Ready to post manually" bar says so. Fix: one line in the bar. S.
- Not run live: any real platform call (known, in open-items).

### 9. Analytics, earnings, learning (F-core)
- Views enter only through `POST /api/posts/{id}/views` (`ledger.py:49`, windows `h48` and `d7`, typed by the owner). Ranking medians (`ranking.py:62`), account health (`alerts.py:109`) and learning all read those rows, so they stay empty until the owner types numbers. Fix: pull views by API after publish (YouTube Data, Instagram insights; TikTok after audit) into `metrics` with `source='api'`; screenshot reading for the rest. L. Impact on income: high (it feeds ranking, quality and the go/stop rule).
- Payout rows are manual (`set_payout_status`). Fix: reminder alerts at day 7 and 14 to enter the payout. S.
- `ledger.py:164`: `net_per_hour` calls `ledger(conn)` twice per request; fine at small volume.
- Learning from decisions and 48 h views is hourly and a no-op until a profile has enough labels (read).

### 10. Backup, ops, start-up
- `backup.py:63`: the dump is written to `.data/backups` on the same drive as the live database. Decision says "a second place"; it is not done. Also not backed up: `TOKEN_VAULT_KEY` (in `.env`), `media.secret`, `.env` itself. Losing `.env` makes stored tokens unreadable. Fix: copy the newest dump and an encrypted `.env` copy to a second folder or cloud drive. S.
- Restore goes only to a scratch database; the steps to put it live are not written anywhere, and `--no-privileges` means the role grants must be re-applied. Fix: `python run.py restore-live` that migrates first, then loads, with a typed confirm. M.
- `ops.py` `worker_loop` (lines near 209-216): `except Exception: worked = False` and `except Exception: pass` hide every job and housekeeping error; nothing reaches a log. Fix: log the exception type and job id, and raise an alert after 3 in a row. S.
- `ops.py:170`: a failed backup raises an `info` alert. Fix: `warn`. S.
- `run.py:181`: daily start uses `next dev` (hot reload, unminified). Smoothness is the top priority, and the dev server is the slowest way to run the app. Fix: `python run.py` builds once and runs `next start` unless `--dev`. S.
- Hard-coded ports and origin: `auth.py:52` allows only `localhost:3000` and `8787`; a different port makes every write fail with `bad_origin` until `CLIPPING_PUBLIC_ORIGIN` is set (hit this in the throwaway; ran). Fix: derive allowed origins from the configured ports. S.
- `python run.py dev` seeds the demo campaign into the live database on every first start (`__main__.py`), mixing a sample campaign and an alert ("SpinKing blocked") into the owner's real Home. Fix: seed only on a database with no campaigns and flag it as sample. S.

### 11. Accounts, niches, languages
- Arabic UI is partial and mixes directions (ran, section 5): `Home` and `Queue` are English inside an RTL frame; punctuation moves ("`.publish checklist`", "`due 1/31/2027@`", "`5h / 0.5`"). Fix: wrap handles, dates, numbers and English fragments in `<bdi>`; translate Home, Queue, Profile heading rows. M.
- `Feed.tsx:306-309,321`: "No account", "No platform", "Arabic", "English", "Why this moment:" are hard-coded English, and the progress count "1 of 11" lost its number in RTL (ran). S.
- Language set is only en and ar by design (`sources.py:91`). Accounts take any two-letter code (`api.py` `add_account`), so a French account can exist but no source can match it. Fix: restrict the account list to en and ar for now. S.

## 4. UX and design

Screens walked (ran): Home, Explore, Review, Queue, Profile, New (3 tabs), Campaign, Listing, Transcript fix, Settings, Ledger, Alerts. Mobile 375 px through the built-in browser (screenshots in `docs/final-review/m-*.jpg`), desktop 1280 px through headless Edge (`d-*.jpg`). Sign-in could not be walked (local auth mode redirects). Review Later, Add and More/alerts were reached by the same shell and were not photographed.

What is good: no horizontal overflow on any screen at 375 px (measured `scrollWidth` = 375); one h1 per page; every control had an accessible name except the form inputs listed below; the bottom nav, large pill buttons and cards are consistent; the transcript fixer and the swipe card are phone-first; reduced-motion respected in CSS; the swipe handler writes transforms straight to the DOM, so a drag does not re-render React.

Findings:
1. **Dev-only badge covers the nav's left icon** (Next "N" button) on every screen. Only in dev; fixed by running a production build (cycle 10).
2. **Review card text is dense and truncates under the action rail.** The "Swipe right posts to ..." line is cut off by the six round buttons (`m-review.jpg`), and the last info lines sit close to the nav pill. Fix: move the targets line above the caption and `padding-inline-end` the info block by the rail width; fold "Why this moment" behind the text toggle. S.
3. **Swipe refusal toast** (`m-swipe-refused-toast.jpg`) is a dead end, see cycle 7.
4. **Explore first screen is the niche picker**, and "Save my niches" stays disabled-looking until a pick. After first use the card should collapse. S. The filter chips run off the right edge (scrollable but "Highest pay" is cut).
5. **Desktop width is under-used** on Profile, Queue, New and Settings: content sits in a ~450 px column with ~600 px empty at the right (`d-profile.jpg`, `d-queue.jpg`). Home has a right rail and is fine. Fix: two-column layout (list left, detail right) on Queue and Profile. M.
6. **Form inputs have no programmatic label** on New and Campaign (`noname` on `input.input`, `textarea.input`, a sr-only file input): the visible label is a sibling, not `for`/`id`. Fix: `htmlFor`. S.
7. **Tap targets**: the header logo link is 137x40 and "All alerts" is 52x20 (Home). Raise to 44 px. S.
8. **Empty and error states.** Empty review ("Nothing to review", with an End session button) is clear. With the API stopped (ran) the Queue showed the cached list with no "offline" notice, so stale data looked live, and Settings silently dropped the Kill switch card. Fix: an offline banner whenever a data fetch fell back to cache or failed; never hide the kill switch, show it disabled with the error. S. Impact on safety: medium.
9. **Missing-id routes** (`/campaign`, `/transcript` without `id`) show a bare muted sentence with no back link. S.
10. **Wording**: "1 clip(s) are ready to post" (Home alert), "Cautious $/clip" and "Reach minimum" in Explore are jargon for a first-time reader. S.
11. **Contrast**: body text on cards and the dark nav measured fine by eye; the muted grey (`.muted`) on the paper background and the lime button label on white-ish Save are the lowest; run an automated contrast pass (not done). S.
12. **Arabic/RTL**: layout mirrors correctly (nav order, header, cards); the problems are mixed-direction strings (cycle 11) and Latin digits. Swipe meanings stay physical (right = accept), as designed.
13. **Smoothness** (measured): drag handler 0.1 ms per move (p95 0.1 ms, max 0.2 ms) over 200 synthetic moves; no layout shift on the review screen (CLS 0); first load of `/review` DOMContentLoaded 97 ms (dev, warm). Frame pacing and INP on a real touch screen were not measurable: the built-in pane was hidden, so `requestAnimationFrame` did not fire. Measure on the phone with the production build before calling it smooth.

## 5. Prioritized improvements

Impact on income first, then safety, then UX. Size is relative.

| # | Area | Problem | Fix idea | Size |
|---|---|---|---|---|
| 1 | Income | Swipe right is refused when the brief check is over 6 h old, with no way to fix it from the card (cycle 7) | Toast button "Re-check brief", or auto re-check for listing campaigns | S |
| 2 | Income | Views and payouts are typed by hand, so ranking, quality and learning stay on assumptions (cycle 9) | Pull views by API after publish into `metrics`; payout reminder alerts | L |
| 3 | Income | New-account ranking shows $0.00 everywhere and assumes 2,500 median views (cycle 2) | Rank on the mean until 5 own posts; default 300; show both figures | S |
| 4 | Income | Feed priority ignores the chance of clearing the minimum (cycle 7) | `net_pay x p_clear` | S |
| 5 | Income | Hashtags are arbitrary spoken words; commentary line is a template (cycle 6) | Niche-scored hashtags; model-written, checked commentary or none | M |
| 6 | Income | No real-footage proof of sharpness, speaker crop, tightening, zoom (cycle 5) | One review pass on 3 real clips per niche with the gate numbers | M |
| 7 | Safety | Link intake fetches loopback and private hosts (cycle 3) | https only, block private ranges per hop | S |
| 8 | Safety | Gate failures (`no_disclosure`, `language_mismatch`) do not stop a swipe-post (cycles 5, 7) | Refuse swipe on those two codes | S |
| 9 | Safety | Backup is on the same disk; `.env` and the vault key are not backed up (cycle 10) | Copy dump and encrypted `.env` to a second place | S |
| 10 | Safety | Worker hides all job and housekeeping errors (cycle 10) | Log and alert after 3 repeats | S |
| 11 | Safety | Offline shows stale data as live; the kill switch card vanishes on a failed load (UX 8) | Offline banner; never hide the kill switch | S |
| 12 | Safety | Kill switch does not cancel Instagram `processing` (L4); `pg_temp` in search path (L5) | One migration | S |
| 13 | Safety | No rate limit on sign-in and setup codes (cycle 1) | 10 failures then 429 | S |
| 14 | Safety | Paid-partnership toggle for manual targets is not mentioned in the manual bar (cycle 8) | One line in the bar | S |
| 15 | Ops | Daily start runs the dev server (cycle 10) | Build once, run `next start` | S |
| 16 | Ops | Fixed ports and origins break writes on a different port; demo seed lands in the live DB (cycle 10) | Derive origins; seed only an empty DB | S |
| 17 | Ops | Long audio read whole into memory; no Deepgram retry (cycle 3) | 20-minute chunks; one retry | M |
| 18 | Ops | Restore has no live path (cycle 10) | `restore-live` with migrate and typed confirm | M |
| 19 | UX | Arabic half-translated, bidi breaks handles, dates, numbers (cycle 11) | `<bdi>`, translate Home, Queue, Profile, Feed strings | M |
| 20 | UX | Review card text collides with the action rail (UX 2); two meanings of accept (cycle 7); key repeat (cycle 7) | Layout padding; label; `event.repeat` guard | S |
| 21 | UX | Desktop leaves half the width empty on Profile, Queue, New, Settings (UX 5) | Two-column layouts | M |
| 22 | UX | Unlabelled inputs, 20 px and 40 px tap targets, jargon, missing-id pages (UX 6, 7, 9, 10) | `htmlFor`, 44 px, plain words, back links | S |
| 23 | UX | Explore niche card dominates first screen after first use (UX 4) | Collapse after save | S |
| 24 | Engine | Arabic cue lists thin; gate niche list misses streams and film (cycles 4, 5) | Grow lists from corrections; extend `TALKING_NICHES` | M |

## 6. Top 10

1. Re-check-brief button on the swipe refusal (blocks every first post of the day).
2. Pull views and payout status from the platforms (ranking and learning currently run on assumptions).
3. New-account ranking: show the mean, lower the default median, stop showing $0.00 on every campaign.
4. Rank the feed by expected earnings (rate x chance to clear), not rate alone.
5. Block swipe-post on gate failures for disclosure and language.
6. Close the link-intake host hole (https only, no private addresses).
7. Back up to a second place and include `.env` and the vault key; log and alert worker errors.
8. Run the daily app from a production build and measure swipe smoothness on the phone.
9. Finish Arabic: translate the remaining screens and fix mixed-direction text.
10. Review real-footage clips per niche (sharpness, speaker crop, tightening) before spending posting accounts on them.

Already fixed in this branch (section 2): first post waits for Undo, malformed id returns 400, no case-duplicate hashtags.

Full backend suite result: see the commit message of the last commit on this branch.
