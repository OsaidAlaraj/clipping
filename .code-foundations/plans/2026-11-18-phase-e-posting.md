# Plan: Phase E: posting (connected accounts, one-tap posting, manual path, post records)
**Created:** 2026-11-18
**Status:** complete
**Started:** 2026-11-18 12:00
**Current Phase:** 5
**Completed:** 2026-11-19
**Complexity:** complex
**Review cadence:** 5
**Check-ins:** checkpoints
---
## Context
**Problem:** The system prepares a clip and its publish checklist, but posting is fully manual: the owner downloads the file and pastes the post link by hand. Three things are missing: posting through official APIs behind one confirm tap, an easy copy path for manual posting, and post records that phase F (money and learning) can build on.

**Success criteria:**
1. The owner connects YouTube, Instagram and TikTok accounts from Profile (OAuth) and sees each connection's state.
2. One confirm tap on an accepted clip confirms every platform the owner picked. Each platform passes its own checklist first. Posts then go out staggered (default 20 min apart), each inside its own account's posting window.
3. Instagram publishes publicly, with the paid-partnership flag when the API supports it (otherwise it stays a checklist step). TikTok lands as an inbox draft. YouTube uploads as private while the Google project is unverified. For TikTok and YouTube, the owner is reminded to finish posting in the app and paste the link.
4. Manual path: share the file from the phone, copy the caption and hashtags, then paste the post link.
5. Each post target has a record: platform, platform post id, state (one enum: `scheduled, sending, processing, published, draft, private, needs_check, failed, cancelled`), posted time, and marketplace submission time (30-minute rule). Every attempt lives in `post_targets`. A `posts` row (and its payout) exists once there is a public link. Phase F reads both tables.
6. The kill switch stops every post not yet sent.
7. All tests green. Security and money phases pass a blocking review. The Arabic guide and the handoff plan are updated.

## Constraints
- **C1:** Nothing posts without the owner's confirm tap. Practice campaigns are refused at the API and in the DB (`practice_guard`).
- **C2:** OAuth tokens are stored only encrypted (AES-GCM), with the key in `.env` (`TOKEN_VAULT_KEY`). They are never logged, never audited, never returned to the web app, and decrypted only in memory at call time.
- **C3:** Official APIs only. Every call goes through the adapter registry, and each provider has a fake. No scraping, no browser automation.
- **C4:** A publish call is never retried automatically after an ambiguous failure (timeout, 5xx after the upload started), because a retry could post twice. It goes to `needs_check` for the owner.
- **C5:** One account per platform per niche. The same clip never goes to two accounts on one platform; cross-platform only when the campaign allows that platform.
- **C6:** Hard rule 4: no coordinated multi-account blasts. One tap queues per-platform posts, staggered, each on its own account's window.
- **C7:** Migrations are additive (next is `015_*`), and RLS plus policies sit in the same file. Older code touched on the way is brought up to `docs/code-standards.md`.
- **C8:** The UI is social-media smooth, English and Arabic, and works on phone and desktop. Everything stays on drive L.
- **C9:** The owner has no developer apps yet. Everything is built and tested on fakes, and live checks wait for the owner (recorded in `docs/open-items.md`).

## Chosen Approach
**A: one publish adapter per platform, run as worker jobs.** Each platform gets an official-API adapter (`publishing` stage) behind the registry. A confirm creates one `post_targets` row per platform, and the worker sends each at its staggered time. This matches round-2 decision "posting through official APIs behind a confirm tap" and the existing worker/registry. **Fallback:** if a platform has no connection or its adapter fails, that target falls back to the manual path (copy, share, paste link).

## Rejected Approaches
- **B: a single posting gateway (Buffer or Ayrshare):** no paid-partnership flag, some cost money, and a third party holds the owner's tokens. Buffer stays as the existing optional scheduler.

---
## Implementation Phases

### Phase 1: Connected accounts and token vault
**Skills:** cc-defensive-programming, aposd-designing-deep-modules, aposd-verifying-correctness
**Model:** sonnet
**Gate:** Standard
**Depends on:** none | **Unlocks:** Phase 2
**File scope:** `backend/clipping/vault.py, backend/clipping/connections.py, backend/clipping/adapters/oauth.py, backend/clipping/alerts.py (calls existing raise_alert only), backend/migrations/015_*.sql, backend/tests/test_16_*.py, backend/clipping/api.py, backend/requirements.txt, web/components/More.tsx, web/lib/api.ts, web/tests/connections*`
**Security-sensitive:** yes

**Goal:** The owner connects and disconnects YouTube, Instagram and TikTok accounts through OAuth, and tokens live only encrypted in the database and refresh themselves.

**Scope:**
- IN: `platform_connections` table (RLS, owner-only), AES-GCM vault with key id, OAuth start/callback with `state` + PKCE, token refresh before expiry, disconnect (revoke where the platform supports it, then delete the ciphertext), connection state in Profile (connected, expiring, needs reconnect), fake OAuth provider.
- OUT: posting (Phase 2), audience tokens from phase C (env vars stay as they are).

**Edge cases:** missing or wrong `TOKEN_VAULT_KEY` (connect refused with a plain message, existing rows report "needs reconnect", nothing crashes); forged or replayed `state`; callback for an account already linked to another owner (refused, hard rule 1); refresh token revoked on the platform (state becomes needs_reconnect, alert raised); ciphertext tampered (decrypt fails closed).

**Produces:** `vault.access_token(conn, connection_id) -> str` (fresh token, in memory only); `connections.for_account(conn, account_id) -> dict | None` (`{id, platform, state, scopes, expires_at}`, never tokens); routes `GET /api/connections`, `POST /api/connections/{platform}/start`, `GET /api/connections/{platform}/callback`, `POST /api/connections/{id}/disconnect`.

**Approach notes:** Owner chose encrypted-in-DB storage, with the key in `.env` only.

**File hints:** `backend/clipping/adapters/audience.py` (env-token pattern), `backend/clipping/auth.py` (session and actor), `backend/migrations/007_discovery_fixes.sql` (RLS pattern).

**Done when:**
- [ ] DW-1.1: A fake OAuth round trip creates a connection; the DB holds only ciphertext, and no token appears in audit rows, logs or any API response (asserted by searching them all for the token).
- [ ] DW-1.2: A forged `state`, a missing vault key and a tampered ciphertext are each refused or failed closed, with a plain message.
- [ ] DW-1.3: An expiring token refreshes on `access_token()`; a revoked refresh token marks the connection needs_reconnect and raises an alert.
- [ ] DW-1.4: Disconnect deletes the ciphertext, and another owner can never read or use the connection (RLS test).
- [ ] DW-1.5 (component test): Profile shows each connection's state, with Connect and Disconnect, in English and Arabic.

**Difficulty:** HIGH
**Uncertainty:** OAuth redirect hosts: Meta and TikTok need an HTTPS redirect (Tailscale ts.net), Google allows loopback. Verified live by the owner later.

### Phase 2: Publish adapters
**Skills:** cc-defensive-programming, aposd-designing-deep-modules, aposd-verifying-correctness
**Model:** sonnet
**Gate:** Standard
**Depends on:** Phase 1 | **Unlocks:** Phase 3
**File scope:** `backend/clipping/adapters/publishing.py, backend/clipping/adapters/base.py, backend/clipping/alerts.py (calls existing raise_alert only), backend/fixtures/publishing/**, backend/migrations/016_*.sql, backend/tests/test_17_*.py`
**Security-sensitive:** yes

**Goal:** Official-API publish adapters for YouTube (resumable upload, private while unverified, paid-promotion flag), Instagram Reels (resumable upload, then publish) and TikTok (inbox draft upload), each with a fake.

**Scope:**
- IN: `publishing` stage adapters plus fakes; a per-platform status check (`check_status`); a registry option so `publishing.publish_video` never auto-retries after the upload has started; quota and rate settings in `adapters.config_json` (YouTube units per upload, Instagram posts per 24 h); fixture-based fakes built from the documented response shapes; migration `016` seeds the `publishing` adapter rows (providers, `config_json` with limits, `unverified_project`, `paid_partnership_supported`; no secrets).
- OUT: deciding when to post (Phase 3); thumbnails beyond the existing cover.

**Edge cases:** platform response is untrusted input (validated shape, or `needs_check`); timeout mid-upload (ambiguous, so needs_check, no retry); a clear pre-upload refusal (4xx before any bytes, so a safe failure with the reason); quota exhausted (refused before calling, alert); Instagram container still processing (poll with a limit); caption over the platform limit (refused before upload).

**Produces:** `registry.call(conn, "publishing", "publish_video", {"connection_id", "platform", "file_path", "caption", "options": {"paid_partnership": bool, "privacy": str}}, owner_id) -> {"state": "published"|"draft"|"private"|"processing"|"needs_check"|"failed", "platform_post_id": str|None, "url": str|None, "reason": str|None}` (a subset of the one state enum in success criterion 5); `registry.call(conn, "publishing", "check_status", {"connection_id", "platform", "platform_post_id"}, owner_id) -> same shape`.

**File hints:** `backend/clipping/adapters/base.py` (registry retry/fallback, line ~153), `backend/clipping/adapters/vendors.py` (BufferScheduler request pattern).

**Done when:**
- [ ] DW-2.1: Each fake platform returns the documented states, and the contract shape holds for all three.
- [ ] DW-2.2: An ambiguous failure after upload start yields `needs_check` with exactly one call made (no retry); a pre-upload 4xx yields `failed` with the reason.
- [ ] DW-2.3: YouTube requests set privacy private while the project is marked unverified, plus the paid-promotion flag; TikTok uses the inbox (draft) endpoint; Instagram publishes publicly (state `published` with a URL) and sets the paid-partnership option only when the adapter config says it is supported.
- [ ] DW-2.4: Quota exhaustion and a malformed platform response are refused safely, with an alert.

**Difficulty:** HIGH
**Uncertainty:** exact request shapes must be checked against current official docs during this phase (see Assumptions).

### Phase 3: Post targets, one-tap confirm and post records
**Skills:** cc-defensive-programming, cc-control-flow-quality, aposd-verifying-correctness
**Model:** sonnet
**Gate:** Standard
**Depends on:** Phase 2 | **Unlocks:** Phase 4
**File scope:** `backend/clipping/publish.py, backend/clipping/pipeline.py, backend/clipping/api.py, backend/clipping/core.py, backend/migrations/017_*.sql, backend/tests/test_18_*.py, backend/tests/test_4_review_publish.py`
**Security-sensitive:** yes

**Goal:** One confirm tap turns an accepted clip into staggered per-platform post targets. The worker sends each one exactly once, and every outcome lands in a post record phase F can use.

**Scope:**
- IN: `post_targets` table (clip, platform, account, connection or manual, scheduled_at, state, idempotency key, attempts); a per-platform checklist built from the existing `checklist()`; `confirm_post(actor, clip_id, platforms, ticks)` as gate 3 (human only, kill switch off, practice refused); a stagger (`post_stagger_min`, default 20) inside each account's window; worker stage `publish` (idempotent: a target already sent is never sent again); kill switch and Cancel stop unsent targets; `posts` extended with `platform_post_id`, `state`, `privacy`, `submitted_at`, `target_id`; `post_targets` holds every state (the one enum); a `posts` row is written only once a public link exists (an API publish, or the owner pasting the link for a draft or private upload); a status check job for `processing`; post options for the screen.
- OUT: earnings and metrics (phase F); screens (Phase 4).

**Edge cases:** a double tap or replayed request (one set of targets); two accounts on the same platform (refused); a platform the campaign does not allow (refused); the brief changed since approval (blocked); the kill switch turned on between confirm and send (target cancelled); a window already closed today (next window); the worker crashing mid-send (target stays `sending` and becomes needs_check, never resent).

**Produces:** `GET /api/clips/{id}/post-options`, returning `{platforms: [{platform, account, connection_state, allowed, manual_only, checklist: {server: {...}, owner_ticks: [...]}, warnings}]}`; `POST /api/clips/{id}/post` with body `{platforms: [..], ticks: {platform: {...}}, request_id}`, returning `{targets: [...]}`; `GET /api/clips/{id}/post-status`, returning `{targets: [{platform, state, scheduled_at, url, reason}]}`; `POST /api/targets/{id}/cancel`; `POST /api/targets/{id}/link` (paste the public link); `posts` columns as above.

**Approach notes:** The owner chose one tap for all platforms. Staggering per account window keeps it within hard rule 4. This replaces decision 15 (one platform per clip).

**Done when:**
- [ ] DW-3.1: One confirm for three platforms creates three targets staggered at least `post_stagger_min` apart, each inside its account's window; a replayed `request_id` creates none.
- [ ] DW-3.2: Practice campaigns, a worker or system actor, an incomplete per-platform checklist, the kill switch, a platform the campaign does not allow, and a second account on the same platform are each refused, both at the API and in the DB (trigger test with a direct insert).
- [ ] DW-3.3: A target is sent at most once even when the job runs twice or the worker dies mid-send (it becomes needs_check); the kill switch turned on between confirm and send cancels every unsent target.
- [ ] DW-3.4: Every outcome updates its `post_targets` row (state, platform post id, reason); a public link writes the `posts` row with platform, platform post id, posted_at and submitted_at, and opens the payout row; drafts and private uploads write no `posts` row until the link is pasted.
- [ ] DW-3.5: The existing manual `mark_posted` path still works and writes the same record shape.

**Difficulty:** HIGH
**Uncertainty:** none.

### Phase 4: Posting screen and manual path
**Skills:** impeccable:impeccable, web-design-guidelines, code-clarity-and-docs
**Model:** sonnet
**Gate:** Standard
**Depends on:** Phase 3 | **Unlocks:** Phase 5
**File scope:** `web/components/Queue.tsx, web/components/Post*.tsx, web/lib/api.ts, web/app/**, web/tests/**, backend/clipping/alerts.py (reminder + morning summary), backend/clipping/publish.py, backend/tests/test_19_*.py`

**Goal:** From the Queue, the owner picks platforms, sees one combined checklist, confirms with one tap and watches each platform's status live. Or they post by hand with share, copy and paste link.

**Scope:**
- IN: a platform picker (connected, allowed, or manual); a combined checklist with server items locked; one confirm button; live per-target status with Cancel before send; manual path (Web Share API with the file on the phone, download on desktop, copy caption, copy hashtags, paste link); a "finish in the app" reminder for drafts and private uploads (in-app alert plus the morning summary); a 30-minute marketplace submit countdown after a public link; English and Arabic (RTL); phone and desktop; optimistic and smooth (existing patterns from commit 83e10ab).
- OUT: the weekly posting plan (phase F).

**Edge cases:** Web Share with files unsupported (falls back to download); clipboard blocked (select-text fallback); offline (actions queued with an id, as decision 29); a target turning `needs_check` (clear message on what to verify).

**Produces:** the posting screens, plus the backend reminder (`alerts.py`: a "finish in the app" alert for draft and private targets, and a line in the morning summary) and the countdown field (`submit_by` on the post-status response, from `publish.py`). Phase 5 documents them.

**Done when:**
- [ ] DW-4.1: A component test runs pick platforms → checklist → one tap → per-platform states; Cancel stops an unsent target.
- [ ] DW-4.2: The manual path shares or downloads the file, copies caption and hashtags, and records a pasted link; an invalid link is refused with a message.
- [ ] DW-4.3: Drafts and private uploads raise a "finish in the app" reminder; a public link starts the 30-minute countdown.
- [ ] DW-4.4: Arabic labels and RTL layout on the posting screens; the stylesheet audit (no overflow at 375 px, tap targets ≥ 44 px) passes.

**Difficulty:** MEDIUM
**Uncertainty:** none.

### Phase 5: Docs: Arabic guide, handoff plan, setup steps
**Skills:** none -- docs-only update in the owner's existing Arabic style
**Model:** haiku
**Gate:** Minimal
**Depends on:** Phase 4
**File scope:** `docs/**`

**Goal:** The Arabic user guide, the handoff plan, decisions and open items describe posting and how to set up the Google, Meta and TikTok developer apps.

**Scope:**
- IN: `docs/دليل-الاستخدام.md` posting section; `docs/خطة-المتابعة.md` status and next steps (phase F); `docs/decisions.md` round-E lines (replacing decision 15); `docs/open-items.md` live checks pending, app reviews and audits, `TOKEN_VAULT_KEY` setup.
- OUT: code.

**Done when:**
- [ ] DW-5.1: The guide explains connect, one-tap posting, drafts and private uploads, the manual path, and the 30-minute rule, in Arabic.
- [ ] DW-5.2: The handoff plan marks phase E done and lists the owner's setup steps per platform; open items list every live check not yet run.

**Difficulty:** LOW
**Uncertainty:** none.

---
## Test Coverage
**Level:** 100%

## Test Plan
- [ ] Unit: vault encrypt/decrypt round trip (DW-1.1)
- [ ] Dirty: wrong key, tampered ciphertext and missing key fail closed (DW-1.2)
- [ ] Dirty: OAuth `state` forged or replayed is refused; account owned by another operator is refused (DW-1.2)
- [ ] Component: Profile connection states, Connect and Disconnect, in English and Arabic (DW-1.5)
- [ ] Unit: refresh on expiry; revoked refresh token → needs_reconnect + alert (DW-1.3)
- [ ] Integration: RLS, so owner B cannot see or use owner A's connection; disconnect removes the ciphertext (DW-1.4)
- [ ] Integration: a secret sweep finds the token in no audit row, log capture or API response (DW-1.1)
- [ ] Unit: each fake platform returns every documented state; contract shape holds (DW-2.1)
- [ ] Dirty: timeout after upload start → needs_check, exactly one call; pre-upload 4xx → failed (DW-2.2)
- [ ] Unit: YouTube private + paid flag; TikTok inbox; Instagram paid flag only when supported (DW-2.3)
- [ ] Dirty: quota exhausted; malformed response; caption over the limit (DW-2.4)
- [ ] Integration: one confirm → 3 staggered targets in windows; replayed request_id → none; window closed → next window (DW-3.1)
- [ ] Dirty: practice, worker actor, incomplete ticks, kill switch, second account same platform, direct DB insert (DW-3.2)
- [ ] Dirty: job run twice; worker killed mid-send → needs_check, no resend; kill switch between confirm and send (DW-3.3)
- [ ] Integration: post records carry the phase-F fields; the payout opens only with a public link (DW-3.4)
- [ ] Regression: `mark_posted` manual path unchanged (DW-3.5)
- [ ] Component: posting flow, Cancel, manual share/copy/paste with fallbacks, reminders, countdown, Arabic RTL, 375 px audit (DW-4.1 to 4.4)
- [ ] Manual (owner, later): one real connect and one real post per platform once the developer apps exist

## Assumptions
| Assumption | Confidence | Verify Before Phase | Fallback If Wrong |
|---|---|---|---|
| Unverified Google API projects lock `videos.insert` uploads to private until audit | HIGH | 2 | If not locked, upload public behind the same confirm |
| YouTube exposes a paid product placement flag on upload | MED | 2 | Checklist step in YouTube Studio |
| Instagram Reels support resumable upload from a local file (no public URL needed) | MED | 2 | Short-lived signed URL via R2, or manual path |
| The Instagram API exposes a paid-partnership option at publish | LOW | 2 | Stays a checklist step (three-state toggle) |
| TikTok inbox upload (`video.upload` scope) works before the audit | HIGH | 2 | Manual path |
| `cryptography` (AES-GCM) is available in the venv | HIGH | 1 | Add to requirements (already importable) |

## Decision Log
| Decision | Alternatives Considered | Rationale | Phase |
|---|---|---|---|
| Tokens encrypted in DB, key in `.env` | Windows DPAPI file; `.env` only | Owner choice; refresh tokens rotate, so env-only breaks | 1 |
| TikTok as inbox draft before audit | Private direct post | Owner choice; drafts can go public, private posts earn nothing | 2 |
| One tap for all platforms, staggered per account window | One tap per platform | Owner chose one tap; stagger keeps hard rule 4 | 3 |
| No auto-retry for publish after upload start | Registry retry ×3 | A retry could post twice | 2, 3 |
| Official per-platform adapters | Buffer or Ayrshare gateway | Paid-partnership flag, no third party holding tokens | all |

---
## Notes
- Code standards (`docs/code-standards.md`, 2026-11-17) were not regenerated; they are current for this code.
- Review policy (owner): phases 1 to 3 are security- and money-sensitive and each gets a blocking review before commit. Phase 4 is covered by the combined E+F batch review, which runs in the phase F plan (cadence 5 keeps it from firing here). Phase 5 is docs-only.
- The owner tests on `.scratch/stable` (:3000/:8787). Never build there; move it only after the review passes.
- Skills were not loaded during planning, to save tokens; the build agents load them per phase.
---
## Decided During Build
| # | Phase | Decision or question | Source | Status |
|---|---|---|---|---|
| 1 | 1 | Platform names follow accounts.platform: youtube_shorts, instagram_reels, tiktok | build agent | assumed |
| 2 | 1 | NeedsReconnect and tamper errors are AppError; callers (Phase 3 worker) must catch them inside their own session so the needs_reconnect mark and alert commit | build agent | assumed |
| 3 | 1 | Missing or changed vault key never writes to the DB; rows only show needs_reconnect | build agent | assumed |
| 4 | 1 | PKCE only for Google until TikTok's variant is checked live; Instagram has no PKCE and no revoke | build agent | assumed |
| 5 | 1 | OAuth state identifies the owner at the callback (hashed, single-use, 10 min) | build agent | assumed |
| 6 | 1 | Connecting with no matching account creates the accounts row from the platform handle | build agent | assumed |
| 7 | 1 | OAuth provider chosen by CLIPPING_USE_FAKES (fake) else platforms; adapter rows stay inactive | build agent | assumed |
| 8 | 2 | Callers pass provider=publishing.provider_name() to registry.call (adapter rows stay inactive, per #7) | build agent | assumed |
| 9 | 2 | Added finish_publish(connection_id, platform, platform_post_id) to publish an Instagram container still processing; check_status stays read-only | build agent | assumed |
| 10 | 2 | Once the first byte is sent, any non-success or unreadable answer is needs_check (never retried); before it, failures are failed with a reason | build agent | assumed |
| 11 | 2 | Refusals (quota, caption, missing file, wrong-platform connection) return failed; only not_connected and NeedsReconnect raise AppError | build agent | assumed |
| 12 | 2 | Quota: rolling 24 h, per account for Instagram and TikTok, project-wide for YouTube, counted at upload start, advisory lock | build agent | assumed |
| 13 | 2 | Missing privacy defaults to private; YouTube forced private while unverified_project; TikTok caption not sent (owner adds it in TikTok) | build agent | assumed |
| 14 | 2 | Alerts raised inside a session that then raises are rolled back with it; Phase 3 must raise alerts in a committed session | build agent | assumed |
| 15 | 3 | Unconnected or needs-reconnect accounts get a manual target, never enqueued, completed by pasting the link | build agent | assumed |
| 16 | 3 | Stagger follows request order: previous + post_stagger_min (operator setting, floor 10), then moved into each account's window | build agent | assumed |
| 17 | 3 | Worker commits `sending` before any byte; AppError->failed, unknown->needs_check, send-time blocker->cancelled; stuck sending -> needs_check after 10 min via sweep | build agent | assumed |
| 18 | 3 | post_gate lets the worker record an API publish that already happened even if the kill switch went on meanwhile (fact, not a new post) | build agent | assumed |
| 19 | 3 | Pasted target links must be on the platform's own host; confirm_post and paste_link are owner-only | build agent | assumed |
| 20 | 4 | Old one-platform approve and mark-posted block shows only when post options fail to load | build agent | assumed |
| 21 | 4 | Share sheet only on touch devices; desktop downloads | build agent | assumed |
| 22 | 4 | Morning summary built from alerts: one info alert per local day from 08:00 (MORNING_HOUR in alerts.py) | build agent | assumed |
| 23 | 4 | A manual target exists only for unconnected platforms; no way yet to post by hand on a connected one | build agent | open question for owner |
| 24 | 4 | test_18 sweep tests use the real clock (a pinned 10:00 made them fail when run before ~07:00) | orchestrator | assumed |
---
## Execution Log
### Phase 1: Connected accounts and token vault (Gate: Standard, security-sensitive)
- [x] BUILD: Discovery + design + implementation complete
- [x] REVIEW: Verification passed (fail->pass, 2 attempts: coverage of the real OAuth provider added)
- [x] Committed
Commit: 64bf5ff
Summary: Owners connect YouTube, Instagram and TikTok via OAuth (state + PKCE); tokens live AES-GCM-encrypted in platform_connections (migration 015) and `vault.access_token()` refreshes them or marks needs_reconnect with an alert; Profile shows connection state in English and Arabic.

### Phase 2: Publish adapters (Gate: Standard, security-sensitive)
- [x] BUILD: Discovery + design + implementation complete
- [x] REVIEW: Verification passed
- [x] Committed
Commit: 524e73c
Summary: `publishing` stage adapters for YouTube (resumable, private while unverified, paid flag), Instagram Reels (resumable, publish, finish_publish) and TikTok (inbox draft) plus fixture-driven fakes; no retry once bytes are sent (needs_check), quota ledger with RLS (migration 016); call via registry.call(..., provider=publishing.provider_name()).

### Phase 3: Post targets, one-tap confirm and post records (Gate: Standard, security-sensitive)
- [x] BUILD: Discovery + design + implementation complete (paused once for laptop shutdown)
- [x] REVIEW: Verification passed
- [x] Committed
Commit: 2a5746b
Summary: One owner tap (`POST /api/clips/{id}/post`) creates staggered per-platform `post_targets` (migration 017, DB guards for human/kill switch/practice/allowed platform/one account per platform); worker stage `publish` sends each once (sending committed first, crash -> needs_check via sweep), `post_status` polls processing, `posts` rows only with a public link; post-options, post-status, cancel and link routes exist.

### Phase 4: Posting screen and manual path (Gate: Standard)
- [x] BUILD: Discovery + design + implementation complete; also cleared the four Phase 3 review notes (TargetLinkIn rename, unused IN_FLIGHT, housekeeping-sweep test, Instagram finish claim)
- [x] REVIEW: DEFERRED — batch pending (full backend suite green, 270 web tests, tsc clean at commit)
- [x] Committed
Commit: 80738c8
Summary: Queue sheet now shows PostPanel (platform picker, one combined checklist, one confirm, live per-platform status with Cancel, offline queue), PostManual (share or download file, copy caption and hashtags, paste link), 30-minute `submit_by` countdown, "finish in the app" alert and a daily morning summary; English and Arabic, RTL.

### Phase 5: Docs: Arabic guide, handoff plan, setup steps (Gate: Minimal)
- [x] BUILD: Docs updated (guide section "النشر", handoff plan with per-platform setup steps, decisions round E, open items phase E)
- [x] REVIEW: DEFERRED — batch pending (docs only)
- [x] Committed
Commit: e658b19
Summary: The Arabic guide and handoff plan describe posting and the Google, Meta and TikTok app setup; decisions and open items record round E and every live check still owed.

### Pending after this plan
- Batch review of Phase 4 and Phase 5 runs together with phase F (owner's rule: one combined E+F review).
