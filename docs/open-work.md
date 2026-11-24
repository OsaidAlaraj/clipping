# Open work (compact handoff of the finished helper sessions, 2026-11-24)

The notes session (Haiku), the quality session (Sonnet) and the disk audit session are finished and archived. Their merged work is on `feature/phase-e-posting`. What is left is below, in one place.

## Running now (separate sessions, started 2026-11-24)
- best-shots: shot selection research, ground-truth evaluation, adoption (`docs/shot-selection-research.md`, `docs/bakeoff/shots/`)
- viral-templates: hooks and caption templates research and bake-off (`docs/viral-templates-research.md`)
- frontend-redesign: IA map, UX research, rebuild of all screens (`docs/ia-map.md`, `docs/ux-research.md`)

## Waiting for the weekly limit to reset (Friday 2026-11-27 03:59 UTC), then start
1. Campaign spec extractor: rich paste with links, typed layers, link layer, review screen, wired rules. Fixtures: `docs/briefs/real-briefs.md`; gaps: `docs/campaign-requirements-audit.md`. (An earlier subagent attempt produced no commits.)
2. Discord campaign inbox: own server + official bot, Explore "Discord" section. Never a user token (Discord bans self-bots).
3. Brand assets: logo or green-screen overlay with chroma key, required or forbidden per campaign, placement, covered-by-UI check (Clipster: not covered more than 50 %, animated logos loop at normal speed), end card, mandatory text.
4. Real-footage proof for sharpness, speaker crop, tightening, zoom (the camera hold and softer zooms were tuned without a real viewing).

## Small items
- Note 14 (future): push notifications. Options sketched in `docs/open-items.md` (web push via the PWA service worker with VAPID keys, or the planned Telegram bot).
- `transcript.test.tsx` DW-2.4 is load-sensitive (timeout raised to 60 s by the notes session).
- Unknown cause of "26 moments, 1 clip" in one real run: best-shots checks the weekly cap and portfolio share in `pipeline._schedule_candidates`.
- Speaker names: placeholder names are never drawn; smart name resolution (video title, description, introductions, owner confirm) is parked (the owner said not important now).
- Items from `docs/final-review.md` still open: pull views and payouts from the platforms, new-account ranking default, Arabic completeness and mixed-direction text, restore-live and secure `.env` copy.

## Owner actions
- Push to main (protected): `git push origin feature/phase-e-posting:main`
- Free space on drive C (5 GB free): see `docs/disk-c-audit.md`; run the cleanup script from an Admin PowerShell after closing IntelliJ, PyCharm and Visual Studio: `powershell -ExecutionPolicy Bypass -File L:\Projects\Clipping\.data\cleanup-c.ps1 -Run`. About 55 GB on C is still unexplained (needs Admin tools).
- Install the Anthropic Design plugin skills in new sessions (done on the account; older sessions did not see them).
- Accounts: 2FA and recovery email, warm-up 3 to 5 days (TikTok/Instagram), YouTube accounts (one per day), developer apps (Google/Meta/TikTok).
- When the Discord inbox is built: create the Discord server and bot, type the token into `.env` yourself.
- Logo files from Drive/Frame.io for the brand assets work (download one green-screen file and give its path).
- Copilot tasks: paste `docs/copilot-tasks.md` into Copilot, stop its old loop on drive C.
