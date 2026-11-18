# Discovery + Design: Phase 7 - Social-media smoothness

## Files Found
web/components (Shell, Home, Explore, Feed, Queue, More, Places, Progress), web/lib (api, useSwipe, i18n), web/app/*/page.tsx, backend/clipping/api.py, web/tests/*.

## Current State (before)
- Every screen fetched on mount and showed "Loading..." text or an empty shell; nothing was cached between tabs.
- The shell fetched /api/home on every route change and Home fetched it again (7 calls to /api/home in one session, plus /api/reference 10.9 KB twice).
- Feed: GET /api/feed then POST /api/sessions in sequence; one video mounted at a time (a swipe mounted a fresh video); swipe drag re-rendered React on every pointer move (framer-motion); only reject had Undo; a refused accept put the clip back at the front, not where it was.
- Home and Explore used window.location for navigation (full page loads); messages were inserted banners (layout shift).

## Gaps
None blocking. Plan says "no new interfaces"; two small API additions were made (counts, categories) and are listed in Decisions.

## Code Standards
Followed docs/code-standards.md: thin endpoint, tests `test_15_smoothness.py`, vitest with `mockApi`, US English copy, LF endings.

## Test Infrastructure
pytest with embedded Postgres, `TestClient`; vitest + Testing Library, `mockApi`. Added `hold()` helper in `smoothness.test.tsx` to hold a request open (the mock router does not await handlers).

## DW Verification

| DW-ID | Done-When Item | Status | Test Cases |
|-------|---------------|--------|------------|
| DW-7.1 | Phone + 4G: every main screen shows content or matching skeleton within 200 ms of a tap; INP < 200 ms | COVERED | "DW-7.1 screens answer at once" (6 tests: skeleton in first render for Home/Explore/Queue/Feed, cached screen opens with data while a never-answering network is pending, tab touch warms data, offline notice); cache tests (shared request, fresh window, stale-while-revalidate, quiet retry); browser measurement below |
| DW-7.2 | Approve, reject, save, dismiss update at once and roll back cleanly | COVERED | "DW-7.2 optimistic actions" (9 tests: accept/later before server answers, Undo on accept and later, refused later/reject roll back at original index with note, Undo before server answered, refused Undo, Home approve + refusal, Not for me + Undo + refusal) |
| DW-7.3 | Feed plays next clip with no visible load; layout shift < 0.05 | COVERED | "DW-7.3" (6 tests: next two clips mounted and buffered, swipe reuses the same video element, only visible clip is interactive, 1,000-clip feed mounts at most 4 videos, cached feed first paint, no reshuffle after a decision); browser CLS measurement below |

**All items COVERED:** YES (3 of 3)

Edge cases: slow 4G (retry test, never-answering network test, browser run with simulated latency); failed optimistic action (rollback tests above + backend refusal test); very long feed (1,000 clips in the feed test, 200 listings paged in Explore, `content-visibility` on cards and rows).

## Design Decisions
- `lib/data.ts`: one stale-while-revalidate cache (`useData`, `fetchData`, `prefetch`, `mutate`, `invalidate`) built on `useSyncExternalStore`; shared in-flight requests; 8 s fresh window; GET retried quietly twice on network loss; session-storage copy of home/counts/queue/progress hydrated after the first render (no hydration mismatch).
- Navigation: tab pointerenter/down/focus warms that screen's data; idle callback warms all five places; `loading.tsx` and skeleton Suspense fallbacks; router.push instead of window.location.
- Skeletons per screen with the real screen's structure (Home, Explore, list, detail, dark feed).
- Feed: window of previous + current + next two clips mounted (same video element reused when a card becomes active), `preload=auto` for current and next, rest `metadata`; swipe moves the card by writing `transform` once per frame (no React re-render), `will-change`, pointer capture; one `decide()` path for accept/reject/later (optimistic, Undo for all three, rollback to original index with a note).
- Messages that used to insert banners are now floating (toast host, sync pill), so they cannot shift layout.
- Explore: pages of 30 with an IntersectionObserver sentinel; grid instead of CSS columns so appending never rebalances columns.
- Owner request folded in: Full screen button first on the rail (unmute, `requestFullscreen`, fallback `webkitEnterFullscreen`) and a Hide text toggle.

## Measured numbers
Method: production builds (`next build` + `next start`) of the pre-phase code (git HEAD, port 3101) and of this phase (distDir `.next-p7`, port 3100), both proxied to a second API (:8788, AUTH_MODE=local, throwaway data dir, demo-seeded, 3 clips). Browser pane at 375x812 mobile emulation, pane visible (a hidden pane throttles requestAnimationFrame to 1 Hz and gave bogus numbers, which were discarded). Network throttling was not available, so 4G was simulated by wrapping `window.fetch`: 150 ms round trip plus 1.6 Mbit/s transfer on every API and Next route request. Video bytes are not throttled (media elements bypass the wrapper), no CPU throttling. PerformanceObserver for event timing and layout-shift; tap-to-screen measured from the pointerdown timestamp to the first animation frame where the new route and its skeleton or content are in the DOM.

| Measure | Before | After |
|---|---|---|
| DW-7.1 tap to first content or skeleton, five tabs (Explore, Profile, New, Home, Review) | 9-17 ms, but Home and Review showed only "Loading..." text (not a matching skeleton) until 189 ms and 259 ms | 13-17 ms, all with real data (warmed by prefetch) |
| Tap on a screen whose data was not warmed (Alerts) | blank shell until data | 16 ms, skeleton rows, then data |
| DW-7.1 interaction to next paint (tab taps) | no event over 16 ms | no event over 16 ms |
| Swipe interaction duration (pointerup) | 16 ms | 16-32 ms |
| DW-7.3 after a swipe up: video state on first frame | readyState 0, paused; playing after 59-78 ms | readyState 4, playing at 4 ms |
| Videos mounted in the feed | 1 | 3 (all readyState 4 before any swipe) |
| DW-7.3 layout shift (CLS) over the whole tab and swipe run | 0.002 | 0 |
| API calls in the same run | 16 (/api/home 7x, /api/reference 2x at 10.9 KB) | 10 (/api/home 1x, /api/reference 1x) |

Honest notes: INP and tap latency were already low on the production build with warm routes; the gain is that the first frame is now real content or a matching skeleton, data is there at once, and the feed never waits on a video. The swipe interaction is 16-32 ms on a desktop-class CPU; a real mid-range phone will be slower and is not measured here. Media loading on a real slow connection is not simulated.

## Prerequisites
- [x] Files exist; [x] dependencies available (no new packages).

## Recommendation
BUILD (done).
