# Discovery + Design: Phase 1 - Connected accounts and token vault

## Files Found
- backend/clipping/{auth,core,db,alerts,api,config}.py, adapters/{base,audience}.py, migrations 001-014, tests/conftest.py
- web/components/{More,Places}.tsx (Profile lives in Places.tsx), web/lib/{api,i18n}.tsx, web/tests/helpers.tsx
- `cryptography` 50.0.2 already in the venv.

## Current State
No vault, no connections, no oauth adapter. Audience tokens are env vars (stay). Row-change audit trigger stores only sha256 hashes of rows (no content), so ciphertext/tokens never reach audit rows. Registry retries 3x on non-AppError and re-raises AppError at once.

## Gaps
- Profile is in `Places.tsx` (outside file scope): mount `<Connections/>` (exported from More.tsx) with a one-line import there.
- `alerts.py`: only the adapter import line changes (registers the oauth providers); `raise_alert` is called as is.
- `conftest.py` (shared): activate the fake oauth adapter and reset its control dict.

## Code Standards
Adapters via registry with `Fake*` twin and `FAKE_*` dict; AppError plain sentences; RLS + policies in the same migration; no secrets in audit/logs; heavy imports inside functions; tests named as sentences.

## Test Infrastructure
pytest + embedded Postgres per test (`db`, `owner` fixtures), API tests via `api._owner = owner; TestClient(api.app)`; web: vitest + `mockApi`, `LangProvider initial`.

## DW Verification

| DW-ID | Done-When Item | Status | Test Cases |
|-------|---------------|--------|------------|
| DW-1.1 | Fake OAuth round trip; DB holds ciphertext; no token in audit/logs/API responses | COVERED | test_DW_1_1_round_trip_stores_only_ciphertext_and_leaks_no_token |
| DW-1.2 | Forged state, missing key, tampered ciphertext refused/fail closed with plain message | COVERED | test_DW_1_2_forged_state..., _replayed_state..., _missing_vault_key..., _tampered_ciphertext..., _wrong_key... |
| DW-1.3 | Expiring token refreshes; revoked refresh -> needs_reconnect + alert | COVERED | test_DW_1_3_expiring_token_refreshes, _revoked_refresh_marks_needs_reconnect_and_alerts |
| DW-1.4 | Disconnect deletes ciphertext; other owner cannot read/use (RLS) | COVERED | test_DW_1_4_disconnect_deletes_ciphertext, _other_owner_cannot_read_or_use |
| DW-1.5 | Profile shows state, Connect/Disconnect, EN + AR | COVERED | web/tests/connections.test.tsx |

**All items COVERED:** YES

## Design Decisions
Design-it-twice (token storage): (A) one column per token, (B) one AES-GCM blob of JSON `{access, refresh}` with AAD = connection id + key id column. Chosen B: one interface (`vault.seal/open`), ciphertext cannot be moved between rows, a single decrypt per use.
- Vault: key `TOKEN_VAULT_KEY` = base64 of 32 bytes; key id = first 8 hex of sha256(key) (stored per row). Missing key -> `VaultUnavailable` (connect refused, rows report needs_reconnect derived, no crash). Wrong key/tamper -> `VaultError`, fail closed.
- OAuth stage `oauth`, providers `platforms` (real endpoints, written from official docs, untested live), `fake`. Methods: authorize_url, exchange_code, refresh, revoke. Permanent refusals raise AppError (no retry); transient raise AdapterError (registry retry). Revoked refresh raises `TokenRevoked(AppError)`.
- `oauth_states` table: state hash (single use, 10 min), PKCE verifier sealed by vault, operator, platform, optional account. Callback authenticates by the state (bound to the operator), because the platform redirect carries no bearer token.
- Platform names follow `accounts.platform` (`youtube_shorts`, `instagram_reels`, `tiktok`).
- Cross-owner: `app.identity_taken(platform, external_id)` security-definer function + unique(platform, external_account_id).
- `access_token` raises `NeedsReconnect(AppError)` after marking and alerting inside the caller's transaction; callers must catch it inside their session so the mark commits (Phase 3 worker does).
- Derived state: needs_reconnect (stored, or key missing/changed), expiring (refresh/ access expiry within 7 days and no refresh path), connected.

## Prerequisites
- [x] cryptography available, [x] adapters/RLS patterns found

## Recommendation
BUILD
