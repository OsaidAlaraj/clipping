# Owner notes 2

| # | Note | Cause | Fix | Status / commit |
|---|---|---|---|---|
| 1 | Hooks: text length does not match its on-screen duration (sometimes unreadable); add "regenerate hooks" button in the hook editor; hook quality is poor, want real trendy hooks with right size, length, words; time control as +/- step buttons | | | discussing |
| 2 | Ad flag: on/off per campaign | The ad (paid-partnership) label was always on: the caption prefix, the burned-in label and compliance all required it | Per-campaign switch `campaigns.ad_label` (migration 022), `POST /api/campaigns/{id}/ad-label`, a toggle on the campaign page. Off removes the label from caption, render and packaging; compliance passes it unless the brief requires it. A brief that requires it refuses the switch (hard rule). Tests: `backend/tests/test_21_ad_label.py` | fixed (tests green) |
| 3 | Animations: good, can be better | | | open |
| 4 | More advanced caption templates (reference: youtube.com/shorts/bxMdms08rcw), e.g. speaker name label, expert-made level | | | open |
| 5 | Review order: shuffle button (default is by priority) | Feed only had the priority order | "Shuffle" chip in the Feed header mixes the clips ahead of the current one (seen ones stay); `web/lib/shuffle.ts` + test | fixed |
| 6 | Posting queue page is unreachable / stuck on loading | Offline service worker (`web/public/sw.js`) answered a failed `/api/queue` with the app shell (HTML 200), so the screen stayed on skeleton forever | Data requests now get a JSON 503 instead of the shell; cache bumped to clipping-v2; test `web/tests/sw-fallback.test.ts` | fixed (test) |
| 7 | Settings: "never override the hard rules. Caps warn; compliance rules always block." page design is awful and too complicated | | | open |
| 8 | Home page crowded and unorganised | | | open |
| 9 | Progress bar and timing are not real, not moving, not accurate; cannot tell what is happening | | | open (next) |
| 10 | Use premade open templates for captions and hooks (not from scratch), then enhance with our layers; every part expert, smart, creative, adaptive | candidates: pycaps (Python, CSS templates), pulsence/caption-animator (ASS, MIT), Remotion captions elements; hooks: HF benxh/tiktok-hooks-finetune (MIT), quso.ai 11-archetype study, vyralcontent/content-skills (MIT). Licences to verify before use | | researching |
