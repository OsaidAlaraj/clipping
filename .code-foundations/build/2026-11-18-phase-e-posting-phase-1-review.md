# Review: Phase 1 - connections and token vault

## Executed Results (Step 0)
- Targeted: `pytest tests/test_16_connections.py tests/test_1_foundation.py` -> 70 passed, 0 failed (summary line suppressed by config; all dots).
- Full backend suite (`pytest -n 4`) -> 100% progress, no F/E markers, 1 skipped (pre-existing).
- Web: `npx vitest run` -> 14 files, 231 tests passed. `npx tsc --noEmit` -> clean. No linter command given.

## Requirement Fulfillment
### DW-1.1
PREMISE: fake OAuth round trip creates a connection; DB holds only ciphertext; no token in audit rows, logs, any API response.
EVIDENCE: vault.py:78-82 (seal), connections.py:155,141-174; migration audit trigger stores only sha256 hashes (002_rules.sql audit_row); test_16:52-72 (`sweep_everything` over audit_log, alerts, connection row minus secret_ct, oauth_states, caplog, API responses).
TRACE: start -> complete -> _save seals {access, refresh} with AAD -> row has secret_ct bytes, tokens absent from bytes and from every swept source.
VERDICT: PASS

### DW-1.2
PREMISE: forged state, missing vault key, tampered ciphertext each refused/failed closed with plain message.
EVIDENCE: connections.py:125-131 (state lookup), vault.py:46-56,85-101; tests test_16:88,118,134,141,152,164.
TRACE: forged state -> no row -> bad_state "Start again"; no key -> VaultUnavailable naming TOKEN_VAULT_KEY; flipped byte -> InvalidTag -> VaultError vault_tampered + needs_reconnect + alert.
VERDICT: PASS

### DW-1.3
PREMISE: expiring token refreshes on access_token(); revoked refresh marks needs_reconnect and raises alert.
EVIDENCE: vault.py:141-169; tests test_16:186, 202, 217.
TRACE: expires_at within 120s skew -> registry refresh -> new sealed blob, new expiry; TokenRevoked -> _mark_needs_reconnect + raise_alert + NeedsReconnect; second call makes no further refresh.
VERDICT: PASS

### DW-1.4
PREMISE: disconnect deletes ciphertext; another owner can never read or use the connection (RLS).
EVIDENCE: connections.py:206-224; migration policies lines 43-55; tests test_16:257, 278, 299.
TRACE: disconnect -> delete row; other operator: select 0 rows, access_token not_connected, update/delete affect 0 rows, insert for another owner raises InsufficientPrivilege.
VERDICT: PASS

### DW-1.5
PREMISE: Profile shows each connection's state, with Connect and Disconnect, in English and Arabic.
EVIDENCE: web/components/More.tsx Connections; Places.tsx Profile renders it; web/tests/connections.test.tsx (EN and AR tests).
TRACE: mocked /api/connections -> per-platform cards with state badge, Connect/Reconnect/Disconnect, Arabic labels and dir=rtl; vitest passes.
VERDICT: PASS

Edge cases: missing/wrong key (tests 118, 141, 134), forged/replayed state (88, 95, expired/cross-platform 103), account linked to another owner refused (299, 359), revoked refresh (202), tampered ciphertext (152): all handled and tested.
Contract (access_token, for_account keys, four routes): present and matching. AES-GCM, env-only key, RLS in same migration, adapter registry with fake: confirmed.

**All requirements met:** YES

## Test-DW Coverage
- [x] All DW items have tests that ran.
- [ ] Coverage matches 100% level; gaps: backend/clipping/adapters/oauth.py `PlatformOAuth.exchange_code`, `PlatformOAuth.refresh`, and the youtube/tiktok branch of `PlatformOAuth.revoke` have no unit test (grep of tests/ finds no call; only authorize_url, `_json`, `_result` and the Instagram revoke early-return are exercised). Other new routines (vault, connections, FakeOAuth, routes) are covered.

## Dead Code
None found. (`_oauth` import in alerts.py is intentional registration, noqa'd.)

## Correctness Dimensions
| Dimension | Status | Evidence |
|-----------|--------|----------|
| Concurrency | PASS | state consumed by atomic `update ... where used_at is null returning` (replay-safe); `for update` on connection rows; unique(platform, external_account_id) backs identity_taken |
| Error Handling | PASS | all failures are AppError with plain text; callback converts to redirect; no token in messages |
| Resources | PASS | sessions via context managers; http_client context manager |
| Boundaries | PASS | platform answers validated in `_save`; seconds clamped; blob length checked |
| Security | PASS | AAD binding, hashed state, PKCE verifier sealed, audit stores hashes only |

## Loaded-Skill Criteria
| Skill | Criterion | Status | Evidence |
|-------|-----------|--------|----------|
| cc-defensive-programming | external input validated at entry (callback params, platform responses, DB blobs) | PASS | connections.complete/_save, vault.open_ |
| cc-defensive-programming | no empty catch / swallowed errors | PASS | disconnect's `except AppError` is deliberate and recorded as revoked=False in audit |
| aposd-deep-modules | narrow interface hiding crypto/refresh | PASS | callers use access_token / for_account only |
| aposd-verifying-correctness | requirements mapping, error/concurrency/security dims | PASS | see above |

## Notes (non-blocking)
- `_save` UniqueViolation on platform_connections says "already connected here" even if a racing other owner caused it (rare race; message slightly off).
- Callback URL (one-time code, state) can appear in web-server access logs; codes are single-use authorization codes, not tokens.
- Real provider endpoints are untested live (stated in module docstring).
- `access_token` marking needs_reconnect relies on the caller catching NeedsReconnect inside its transaction (documented in the class docstring).

## Issues (FAIL)
1. Test coverage gap against the 100% level (rule f).
   - File: backend/clipping/adapters/oauth.py (`PlatformOAuth.exchange_code`, `PlatformOAuth.refresh`, `PlatformOAuth.revoke` youtube/tiktok path)
   - Demonstrated by: no test in backend/tests references these methods on `PlatformOAuth` (only `monkeypatch`-style httpx.MockTransport tests exist for other adapters, e.g. test_9_transcription.py:49).
   - Fix: add unit tests using `monkeypatch.setattr(oauth, "http_client", lambda t=30: httpx.Client(transport=httpx.MockTransport(...)))` covering each platform's exchange (incl. Instagram refresh_token None and TikTok handle failure), refresh (revoked 400/401 -> TokenRevoked), and revoke.

**Verdict: FAIL - blocker: 100% coverage gap, PlatformOAuth.exchange_code/refresh/revoke untested. All five DW items and listed edge cases PASS.**
