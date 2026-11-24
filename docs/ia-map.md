# ClipMind information-architecture map

Rule (product principle 5): one capability, one place, one component. This file is updated with every screen change.
Section A is the audit of the app **before** the redesign (branch tip 06cd35d). Section C is the state **after**.

## A. Audit before the redesign

### A1. Screens (routes in `web/app`)

| Route | Component | Notes |
|---|---|---|
| `/` | Home | primary card, "Needs you", progress, pay per hour, Today, campaign stories + cards |
| `/explore` | Explore | marketplace listings, filter sheet, table on desktop |
| `/listing?id` | ListingPage | one listing, "Add to my campaigns" |
| `/new` | NewPlace | 3 tabs: Add campaign / Add video / Practice |
| `/review`, `/feed` | Feed | the swipe review feed (the quality bar); `/feed` is an alias |
| `/profile` | Profile | numbers, 6 list links, accounts, add account |
| `/library` | Library | accepted clips scroll |
| `/queue` | Queue | posting queue + checklist |
| `/campaign?id` | CampaignDetail | payout, rules, footage, ad label, **its own add-footage form** |
| `/transcript?id` | Transcript | fix-transcript editor |
| `/alerts` and `/more/alerts` | Alerts | **two routes, same screen** |
| `/more` | MoreMenu | **orphan menu**: not linked from any tab, repeats Profile's list |
| `/more/add` | AddCampaign | **second route for adding a campaign** (also inside `/new`) |
| `/more/ledger`, `/more/review-later`, `/more/settings` | Ledger, ReviewLater, Settings | reached only through Profile list |
| `/signin` | SignIn | passkey / password |

### A2. Capabilities with more than one entry point or form

| Job | Entry points before | Problem |
|---|---|---|
| Add a campaign | `/new` tab 1, `/more/add`, Explore "Add to my campaigns", Home empty text "Tap ➕" | two routes with different chrome (`/more/add` has Back to `/`, `/new` has none); AddCampaign ends with a hard `window.location.href` (full reload, loses the data cache) |
| Add a video | `/new` tab 2 (UploadVideo), campaign page "Add permitted footage" (UploadVideo, fixed this week), Home primary "Add a video" -> `/new` landing on the *campaign* tab | owner could not find link-add in one of the two; `/new` opens on Campaign even when the user asked for a video |
| Practice campaign | `/new` tab 3 only | invisible, a third equal tab |
| See alerts | Top-bar bell, Home "Alerts" disclosure, Profile list, `/more` list, `/more/alerts` | 5 ways, two different URLs |
| Earnings | Home "Net pay per hour" card, Profile "Your numbers", Ledger, Home "Today" | numbers repeated in 3 cards with different wording |
| Review a campaign | Review tab, Home story rings, Home card (approved), campaign page button | fine, but a Home card for an *approved* campaign skips its campaign page, so rules/payout are unreachable from Home |
| Back | `Head` component hard-codes back to `/profile`; Transcript hard-codes `/` and `/campaign`; Feed finish does `window.location.href = "/"` | Back goes where the code says, not where the user came from; full reloads |

### A3. Redirects and back-navigation problems found

1. `window.location.href` / `assign` used for in-app moves in AddCampaign (-> campaign), Feed summary (-> Home). Full reload: white flash, cache cleared, outbox re-synced.
2. `Head`'s Back always goes to `/profile`, even when the page was opened from Home (Alerts, Add campaign) or Campaign.
3. Transcript "Back" goes to `/` when no campaign is known.
4. Bottom bar "Profile" lights up for `/queue`, `/library`, `/more/*`, but `/alerts` is not in the list, so on `/alerts` nothing is highlighted.
5. "New" opens a page and loses the screen the user was on (no way back to the campaign they wanted to add footage to).
6. Share-target (`/new?text=&url=`) lands on the Campaign tab: right for a brief link, wrong for a video link.

### A4. Dead ends and orphans

`/more` (menu with no inbound link), `/more/add`, `/more/alerts` duplicates, `/feed` alias, Explore has no slot for the coming Discord inbox, `/library` is only reachable from Profile.

### A5. Language

`i18n.tsx` covers navigation and the review actions only. Home, Explore, Add, Campaign, Queue, Profile, Settings, Alerts, Ledger are hard-coded English. Layout uses logical properties in most places, but there is no Arabic copy for any of those screens.

### A6. Daily jobs and taps before (from Home, phone)

| # | Job | Path | Taps |
|---|---|---|---|
| 1 | Add or find a campaign | Explore (1) -> listing (2) -> Add (3) -> category (4) -> lands on campaign page; or New (1) -> paste brief (2-3) -> Read (4) | 4 |
| 2 | Add a video | New (1) -> Add video tab (2) -> pick campaign (3) -> choose link (4) -> paste + Go (5-6) | 6 |
| 3 | Wait for clips | Home progress cards (0 taps), no push, no badge on the tab until ready | 0 (but nothing tells you where) |
| 4 | Review clips | Home primary (1) or Review tab (1) | 1 |
| 5 | Post | swipe right + confirm bar (in the feed) | 0 extra |
| 6 | See earnings | Home card (0) or Profile (1) -> Ledger (2) | 0-2 |
| 7 | Fix an alert | bell (1) -> alert (no action link) | dead end |

## B. Decisions (the new information architecture)

- **Five places, one meaning each**: Home (what needs me now), Explore (find work), **Add** (the centre button, a sheet from anywhere), Review (the feed, untouched in feel), You (accounts, money, library, queue, settings).
- **Add is one component** (`AddFlow`) in a bottom sheet (dialog on desktop). Kind switch: *Video* (default when any campaign is approved) or *Campaign*. Video = campaign picker (preselected from context) + File / Link / YouTube (with its permission step). Campaign = paste brief / link / screenshot, and "Practice" is a toggle inside it. The same component renders full page at `/new` for the phone share target. The campaign page opens the same sheet with its campaign preselected. No other form exists.
- **One primary action per screen**: a single accent (lime) button, never two.
- **Back**: `useBack(fallback)` goes to the previous in-app screen, else the logical parent. No `window.location` moves inside the app; moves are `router.push/replace`.
- **Alerts**: one route `/alerts` (the bell) with one list; `/more/*` routes removed and redirected.
- **Earnings**: one `Earnings` summary used on Home and You; Ledger is the detail.
- **Explore** gets a segmented head: *Marketplace* now, *Inbox* (Discord, coming) as a reserved, disabled-with-explanation slot.

## C. State after the redesign

(filled in at the end of the work)
