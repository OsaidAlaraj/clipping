# Review (attempt 2): Phase 1 - connections and token vault

## Executed Results
- `pytest tests/test_16_connections.py tests/test_16b_oauth_provider.py tests/test_1_foundation.py` -> 87 passed, 0 failed.
- Full backend suite (`-n 4`) -> 100%, no FAILED/ERROR lines, 1 skipped (pre-existing).
- Web results unchanged from attempt 1 (web files not changed).

## Blocker closed
test_16b_oauth_provider.py covers PlatformOAuth.exchange_code (youtube, instagram, tiktok incl. name-lookup failure), refresh (all three; 400/401 -> TokenRevoked; 502 -> AdapterError retryable), revoke (youtube, tiktok, instagram skip), and refused/outage exchange mapping, over httpx.MockTransport. Coverage gap: closed.

## Regression check
connections.py `_save` UniqueViolation now re-asks `app.identity_taken`: other owner -> account_taken, else already_connected. Tested for the other-owner race (test_16b last test). DW-1.1 to DW-1.5 and all edge cases still pass in the re-run.

## Notes (non-blocking)
- The `already_connected` (own duplicate) branch of the new UniqueViolation handling has no test, though the test docstring mentions it; it is a branch, not a routine.

**Verdict: PASS**
