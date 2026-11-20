# Security and bug review: posting and token vault (2026-11-20)

Scope: `vault.py`, `connections.py`, `adapters/oauth.py`, `adapters/publishing.py`, `publish.py`, the connection and post endpoints in `api.py`, migrations 015 to 018, and the gate triggers in `migrations/002_rules.sql` (as replaced by 010b and 017).
Method: read every line in scope, then confirmed each suspicion with a probe or a test before calling it a finding. "Verified" means a test or probe ran; "read" means only reasoned from the code.
Result: **no high finding.** The core design holds: AES-GCM with the row id as associated data, single-use hashed OAuth state with PKCE for YouTube, `sending` committed before any byte leaves, no auto-retry after an unclear publish, and three layers (app code, DB triggers, RLS) on every posting gate. Four medium and seven low findings, listed below. The Copilot review of the same scope ("no findings", `docs/copilot/`) missed all of them.

## High

None found.

## Medium

| # | Where | Failure scenario | Fix | Status |
|---|---|---|---|---|
| M1 | `api.py` (all POST routes), local-auth mode | With `AUTH_MODE=local` and no `CLIPPING_LOCAL_TOKEN` the API trusts every caller. A web page the owner visits can send a body-less POST such as `/api/targets/{id}/cancel` or `/api/connections/{id}/disconnect` (a "simple" cross-site request, no preflight). DNS rebinding makes the same page a same-origin caller of the whole API. | Middleware `refuse_foreign_origins`: a write with an `Origin` outside the app's own origins gets 403 `bad_origin`. Requests with no `Origin` (tests, curl, worker) pass; passkey cookies are already `SameSite=Lax`. | **Fixed.** Verified by test (`test_22_security_review.py`). Reads from a rebound page remain possible in local mode; run passkey mode or set `CLIPPING_LOCAL_TOKEN` when the port is reachable. |
| M2 | `config.py:47` `SECRET_NAMES` | `redact()` is the only scrubber for alerts, job errors, adapter errors and audit text, but it did not know `TOKEN_VAULT_KEY`, `GOOGLE_CLIENT_SECRET`, `INSTAGRAM_APP_SECRET`, `TIKTOK_CLIENT_SECRET`. The Instagram exchange sends the app secret in a query string; any exception text carrying that URL would be stored in clear. | Added the four names. | **Fixed**, verified by test. |
| M3 | `adapters/publishing.py` `_tt_publish` (was `url.split("/")[2].endswith(".tiktokapis.com")`) | A crafted TikTok answer with `upload_url` `https://evil.example/?x.tiktokapis.com` (or `#`, or `\`) passed the check, and the owner's video bytes were PUT to that host. | New `_tiktok_upload_ok`: parsed host must fully match `*.tiktokapis.com`, https, no user info, valid port. | **Fixed**, verified by test (11 hostile and 2 good addresses). Needs a hostile or compromised TikTok response to exploit, hence medium, not high. |
| M4 | `publish.py` `plan_times` / `next_slot` (for the decision 31 session) | `plan_times` starts the first post at `now` when the account is inside its window. A swipe-right bar promising "in 20 minutes" with a 10 s undo is only true if the first target is delayed. Otherwise the worker can send it before the owner can undo. Undo also needs to cancel the targets: `review.undo` only restores clips whose status is `accepted`, and a confirmed clip is `ready_to_post`. | Not mine to edit. Delay the first target by at least the undo window plus a margin, cancel scheduled targets inside the undo transaction, and block undo once any target is `sending` or later. | **Reported only.** Read, not run. |

## Low

| # | Where | Failure scenario | Fix | Status |
|---|---|---|---|---|
| L1 | `publish.py` `_clean_outcome` | A platform answer could set `url` to any http(s) site; it would be stored as the post link (`_link_ok` guards only the pasted path). | `_on_platform_host`: a link off the platform's own hosts is dropped, and a published target without a link becomes `needs_check`. | **Fixed**, verified. |
| L2 | `publish.py` `checklist`, `schedule`, `mark_posted` (and `approve_publish` through `checklist`) | A malformed clip id returns HTTP 500 from a database cast error instead of a plain 400. | `_id()` validation first. | **Fixed**, verified (`test_a_malformed_clip_id...`). |
| L3 | `publish.py` `mark_posted` (legacy manual path) | Accepts any http(s) link, unlike `paste_link`. Owner-only action, so a self-inflicted wrong link. | Apply `_link_ok` when the clip has a platform. Not done: `test_10b_pilot` records `https://example.com/p` and the legacy path is being replaced by targets. | Open. Read. |
| L4 | `migrations/017` `kill_switch_stops_targets`; `stage_post_status` | The kill switch cancels only `scheduled` targets. An Instagram target in `processing` (container uploaded, not yet public) is published by `finish_publish` once the switch is turned off again. Queued jobs are cancelled, but the sweep re-queues the check. | Cancel (or hold) `processing` Instagram targets when the switch goes on, or refuse `finish_publish` unless the owner re-confirms. | Open. Read, plus the worker code that cancels queued jobs. Needs a new migration (next number is coordinated with other sessions). |
| L5 | `migrations/015` `app.identity_taken`, `017` `app.kill_switch_stops_targets` | `SECURITY DEFINER` with `search_path = public` still searches `pg_temp` first for tables; a caller able to create a temp table named `platform_connections` could fake "not taken". Only reachable with SQL access, which the app role does not expose. | `set search_path = public, pg_temp`. | Open. Read. |
| L6 | `publish.py` `stage_publish` | It trusts the job's `run_after` and never re-checks `target.scheduled_at`. `claim_job` honours `run_after` today, so stagger holds, but nothing else would catch a job created or retried early. | Return without sending when `scheduled_at` is still in the future. | Open. Read, and confirmed `claim_job` filters on `run_after`. |
| L7 | `api.py` callback, `adapters/oauth.py` | The OAuth `code` and `state` sit in the callback URL (server access logs, browser history); Instagram refresh puts the long-lived token in a query string. Platform-imposed. Codes are single use and the state is consumed first. TikTok and Instagram run without PKCE (documented in the code). | Keep access logs off or local; turn PKCE on for TikTok after the live check. | Accepted. |

## Verified safe (checked, nothing to fix)

- **State and PKCE** (`connections.start/complete`): 32-byte random state, stored as SHA-256, consumed by one atomic `UPDATE ... WHERE used_at IS NULL AND expires_at > now()` before the code exchange, so a failed attempt cannot be replayed; platform mismatch, expiry, forged and replayed state each have tests (`test_16_connections`, `test_20_failure_paths`). The verifier is sealed with the state row id as AAD.
- **Vault**: nonce per seal, row-bound AAD, key fingerprint check, every failure closes; a transient refresh outage is never marked revoked; tokens appear in no audit row (audit triggers store hashes only), error text or API view. `last_error` and `key_id` are not returned by `_view`.
- **Redirects**: the callback redirects only to relative `/profile?...`; `redirect_uri` comes from env, not from the request. httpx does not follow redirects, so a 3xx cannot carry a bearer token elsewhere. YouTube's session URL must start with Google's upload prefix.
- **No post without the owner's confirm**: `post_targets_guard` (human, switch off, non-practice, own active account, platform allowed, complete checklist) fires on insert; the worker cannot insert (`is_human` false); `post_gate` for a target row needs a published, confirmed target; `practice_guard` (010b) still blocks `posts`, `payouts` and clip status moves on practice campaigns; `stage_publish` re-checks switch, practice, account, brief version and file hash right before sending. Probed via `test_20_failure_paths` and `test_18_post_targets`.
- **Queue races**: duplicate taps hit `on conflict do nothing` on `idempotency_key` and the one-live-target-per-platform index and roll back as a whole; `cancel_target` takes a row lock against the worker's claim; a dead worker leaves `sending`, which the sweep turns into `needs_check`, never a resend.
- **Injection**: all SQL is parameterised; the one dynamic `UPDATE` in `connections._save` interpolates column names from a fixed dict. Pasted links are parsed by hostname, not substring.
- **RLS**: `platform_connections`, `oauth_states`, `publish_quota_log` are owner-only (even admins do not read another owner's connection); `post_targets` admin can read all, which is the existing pattern.

## Not covered

- Real platform endpoints (never run live; written from the docs).
- The review feed and accept code (another session is changing it; only M4 reported).
- Web app XSS and the passkey flow beyond the cookie flags.
- No load or concurrency test beyond the existing suite; races were reasoned from the locks and unique indexes.
