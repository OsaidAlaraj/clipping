# Review: Phase 7 - Social-media smoothness (commit 83e10ab)

## Executed Results (Step 0)
- Web: `npx vitest run` -> 12 files, 217 tests passed. `npx tsc --noEmit` -> exit 0.
- Backend: pytest test_15_smoothness + test_4_review_publish + test_7_discovery -> all passed (exit 0, 63 dots).
- Lint: ruff E9,F63,F7,F82 -> All checks passed.
- Full backend suite not rerun (per instructions).

## Requirement Fulfillment

### DW-7.1
PREMISE: phone size, 4G latency, every main screen shows content or a matching skeleton within 200 ms of a tap; INP < 200 ms (verify via component tests, code paths, and the measured numbers).
EVIDENCE: web/lib/data.ts:131-146 (useSyncExternalStore cache-first; hydrateCache after first render); web/components/Shell.tsx PLACE_DATA/warmProps (tab pointerenter/down/focus prefetch, idle warm of all places); Skeleton.tsx + Home.tsx, Explore.tsx, Queue.tsx, More.tsx (RowsSkeleton/DetailSkeleton), app/*/page.tsx Suspense fallbacks, app/loading.tsx; Feed.tsx:506 FeedSkeleton; smoothness.test.tsx "DW-7.1 screens answer at once" (6 tests) and cache tests.
TRACE: tap tab -> warm() fires prefetch on pointerdown -> destination renders in the first frame with cached data or its own skeleton (never blank or "Loading..." text) -> data refreshes quietly. Never-answering network test confirms a seen screen opens with data.
Measurement method (discovery file): production builds before/after, same API, 375x812, pane visible, fetch wrapper simulating 150 ms RTT + 1.6 Mbit/s. Sound for first-frame content/skeleton latency and API call counts (16 -> 10). Weaknesses (reported, not blocking): no real network or CPU throttling; media bytes unthrottled; run count/variance not stated; "no event over 16 ms" is the event-timing threshold floor, so INP is only shown to be "small" on a desktop CPU, not on a phone. The author states these limits honestly. The 200 ms claim rests mainly on structure (skeleton in first render), which is verified by the tests.
VERDICT: PASS

### DW-7.2
PREMISE: approve, reject, save and dismiss update the screen at once and roll back cleanly when the server refuses.
EVIDENCE: Feed.tsx:401-424 decide() (removeCurrent + counts mutate before reviewAction resolves; reinsert at original index, counts restored, note "The clip is back in your feed." on ApiError; NetworkError queues in outbox, no rollback); Feed.tsx:434-447 Undo; Home.tsx act() (mutate then restore snapshot + error toast); Explore.tsx dismiss/removeFromLists/restoreToLists, saveNiches rollback; tests "DW-7.2 optimistic actions" (9 tests).
TRACE: refused reject -> api 4xx -> review rejects -> reinsert(current, at) + fail(error,true) -> clip back at same index with the server's reason; passes in test "a refused reject rolls back..." and "refused save for later...".
VERDICT: PASS

### DW-7.3
PREMISE: the review feed plays the next clip with no visible load after a swipe, and layout shift stays under 0.05.
EVIDENCE: Feed.tsx:37-38,508 window of prev+current+next two (max 4 videos); ClipCard keeps the video at the same child index so React reuses the element (Feed.tsx:269-276); preload auto for current and next; useSwipe.ts writes transform per frame with no React re-render; toasts/pills are position:fixed (globals.css); skeletons keep footprints. Tests: "keeps ... mounted and buffered", "a swipe reuses the already-loaded video element", 1,000-clip test (<= 4 videos), "writes transform without re-rendering". Discovery numbers: readyState 4 and playing at 4 ms after swipe, CLS 0.
TRACE: swipe up -> index+1 -> idle card for next clip becomes active, same <video> element, effect calls play() on a buffered element.
Caveat: the 3rd buffered clip uses preload=metadata and video bytes were not throttled in the measurement, so "no visible load" on a real 4G is shown by design plus a fast-network measurement only.
VERDICT: PASS

### Full screen button
PREMISE: "Full screen" / "ملء الشاشة" button on each review clip that unmutes and calls requestFullscreen, falling back to webkitEnterFullscreen.
EVIDENCE: Feed.tsx:255-262, 296; i18n.tsx:11,23; tests "seeing the whole clip" (4 tests: unmute+request, iPhone fallback, rejected promise fallback, Arabic label).
TRACE: click -> el.muted=false, setMuted(false) -> requestFullscreen().catch(webkitEnterFullscreen) or webkitEnterFullscreen directly.
VERDICT: PASS

**All requirements met:** YES

## Test-DW Coverage
- [x] Each DW item has tests that ran (smoothness.test.tsx describe blocks named DW-7.1/7.2/7.3; test_15_smoothness.py).
- [x] Coverage level per prompt: none stated beyond per-DW; no gap named.

## Dead Code
None found that blocks. Minor: `.feed-card.settling` CSS class is defined but unused by Feed/useSwipe (note).

## Correctness Dimensions
| Dimension | Status | Evidence |
|-----------|--------|----------|
| Concurrency | PASS (note) | Shared in-flight requests; an in-flight fetch started before an optimistic action can land afterward and be stored as fresh for 8 s (data.ts:85-90). Narrow window; note only. |
| Error Handling | PASS | NetworkError retried quietly twice; ApiError surfaces with message; rollback tests pass. |
| Resources | PASS (note) | Mounted videos bounded to 4. The data.ts store has no eviction (each Explore filter/search key and listing detail stays in memory for the page life); modest, note only. |
| Boundaries | PASS | Empty feed, 1,000-clip feed, index clamp after removal, reinsert clamp (Math.min). |
| Security | FAIL | Cross-actor cache leak, see Issue 1. Backend: /api/counts runs in get_db().session(actor) with the same count queries as ops.home (RLS role app_user/app_worker) and caps alerts at 5 like home; listing categories read the `categories` table, which is in the RLS "reference" set (003_rls.sql:12), same data as /api/reference. Both PASS. |

## Edge cases
- slow network: PASS (retry test, never-answering network test).
- failed optimistic action with a clear note: PASS.
- very long feed, bounded mounted videos: PASS (<= 4 for 1,000 clips).
- Arabic RTL: PASS (inset-inline CSS, physical swipe meaning kept, Arabic label test).
- /api/counts and listing categories respect RLS and the actor on the server: PASS. Client cache of these per-actor values: FAIL (Issue 1).

## Loaded-Skill Criteria
| Skill | Criterion | Status | Evidence |
|-------|-----------|--------|----------|
| aposd-verifying-correctness | RC requirements mapped | PASS | See DW sections. |
| aposd-verifying-correctness | CS/EH/RM/BC | PASS (notes above) | |
| aposd-verifying-correctness | SE-5 authz/actor scope | FAIL | Issue 1 (client cache not actor-scoped or cleared on sign-out). |
| performance-optimization | Measure first | PASS | Before/after production-build measurements; limits stated. |
| performance-optimization | Fundamental fixes (cache, prefetch, merged calls, windowed DOM, per-frame transform) | PASS | data.ts, Shell warm, /api/counts, categories merged into listing detail. |
| web-design-guidelines | Icon buttons named, live regions, reduced motion, gestures have button alternatives, positioned toasts do not shift layout, lists bounded | PASS | Rail buttons have aria-label; toast role status/alert; reduced-motion CSS and tilt guard; Explore pages of 30. |
| web-design-guidelines | Minor | Notes | Idle skeleton animation off under reduced motion OK; `transition` lists explicit properties. Nothing demonstrated. |

## Notes (non-blocking)
- Measurement caveats listed under DW-7.1 (no phone CPU, unthrottled media, undisclosed run count).
- Home rollback restores the whole `before` snapshot, which could discard a concurrent optimistic change (edge).
- Unused `.feed-card.settling` rule.

## Issues (if FAIL)
1. Per-actor data cached in sessionStorage is never cleared on sign-out, so the next signed-in person in the same tab briefly sees the previous person's data.
   - File: web/lib/data.ts:15,24-46 (PERSISTED /api/home, /api/counts, /api/queue, /api/progress; hydrateCache); web/components/More.tsx:234-237 (signOut only calls logout and window.location.assign("/signin")); web/components/Shell.tsx hydrateCache() on every mount.
   - Demonstrated by: TRACE. Grep shows `clearCache` is referenced only inside data.ts and tests, never by sign-out or sign-in, although its own comment says "Forget everything (sign-out, a new account...)". Sign out -> full reload keeps sessionStorage -> operator B signs in -> Shell mounts -> hydrateCache loads A's /api/home (earnings, campaigns), /api/counts, /api/queue, /api/progress into the store and Home renders them in the first frames until the refresh replaces them. Also the service worker DATA list (sw.js) caches these URLs without an actor key (pre-existing for /api/home, newly extended to /api/counts).
   - Fix: call clearCache() in signOut before navigating (and on successful sign-in), and ideally key the persisted copies by actor id; clear the SW data cache on sign-out.

VERDICT: FAIL
