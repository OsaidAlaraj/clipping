# UX research and design language

Short and evidence-based. "Verified" = read in a source this session; "Practice" = widely documented industry practice I am relying on without a fresh source.

## 1. What the evidence says

**Navigation.** Bottom tab bars beat hamburger menus for a small set of equal top-level places (up to five); hamburgers hide location and are only fit for secondary items (Smashing Magazine, *Basic Patterns For Mobile Navigation*; vendor A/B tests reported by TechCrunch for zeebox and Redbooth; one Waikato thesis preferring bottom navigation). Verified, but the evidence is vendor-grade, not rigorous. Consequence: keep five tabs, no "More" menu (the orphan `/more` goes), secondary things live under *You*.

**Sheets vs pages.** Vaul (Emil Kowalski) is built on Radix Dialog, so focus trap, aria and escape handling come free; its value is drag-to-dismiss, snap points and momentum. Guidance in the shadcn world: drawer on mobile, dialog on desktop (Verified: emilkowal.ski/ui/building-a-drawer-component and shadcn docs summaries). I found no hard bug reports in this search, so I treat "dependency risk" as unknown. Decision: the app already has a working sheet (`SheetFrame` in Feed). I do **not** add Vaul or Radix: that is a new dependency tree for one component, and the existing sheet already passes the Feed's smoothness numbers. I extract it into a shared `Sheet` with drag-to-dismiss written with pointer events and `motion` (already installed). Revisit if drag physics prove poor in the measurements.

**Motion.** `motion` (Framer Motion's successor package) is already a dependency. Practice: animate transform and opacity only (compositor), 120-180 ms for feedback, 240-320 ms for sheets, ease-out for entering, ease-in for leaving, springs only for drag release; honour `prefers-reduced-motion`. TikTok, Reels and Shorts studios use the same restraint: the video is the hero and chrome fades.

**RTL.** Use the `dir` attribute on `<html>`, CSS logical properties (`inline-start/end`, `padding-inline`, `margin-block`) instead of left/right, flip only directional icons (arrows, chevrons, back), never logos or media-play symbols; use an Arabic-capable font (Mozilla RTL guidelines, Smashing "Deploying CSS Logical Properties", Untitled UI RTL docs). Verified. The app already loads IBM Plex Sans Arabic in its font stack.

**Speed and calm tools (Linear, Things, Superhuman, Cron, Arc).** Practice, consistent across their public write-ups: one obvious next action; optimistic updates with undo instead of confirmation dialogs; skeletons shaped like the final content; keyboard and gesture first; restrained palette; no card-in-card; dense but quiet lists; empty states that name the next action. ClipMind's Feed already does swipe + undo; the redesign copies that language into every screen.

**Creator tools (TikTok / Reels / Shorts studios, OpusClip, CapCut).** Practice: the create entry is one persistent button, import offers file / link / record in one sheet, and processing shows a live progress card with the thing being made, not a spinner. ClipMind's Add sheet and Home progress follow that.

**Design systems worth reusing.** shadcn/ui is a code-copy approach on Radix with CSS-variable tokens; its token naming (`--background`, `--foreground`, `--muted`, `--accent`, `--ring`, `--radius`) is a sound convention. The app already has tokens in `globals.css`; I keep its names and add the missing scale tokens instead of importing Tailwind/Radix (rewrite cost with no measured gain for a 6.6k-line private app).

## 2. Diagnosis of "AI-looking"

From the code and screenshots: every Home block is a white rounded card with a small caps heading; stacked cards of equal weight; lime pill + black pill bar used as decoration; text-only blocks where real content (clip frames, campaign logos, numbers) could be shown; three different list styles; headings at one weight. The Feed does not have these: it is full-bleed content with a few precise controls.

## 3. The design language (what the build uses)

- **Idea**: *a studio monitor*. Quiet ink and paper surfaces, real footage and numbers as the content, one lime signal.
- **Colour**: neutrals (paper `#F3F3EF`, ink `#0E0E10`, hairline `#DCDCD5`), **one accent**, lime `#CCF24A`, used only for the single primary action and "live" signals. Coral only for urgency, red/amber/green only for status text. Dark mode is designed, not inverted: `#0B0D10` ground, elevated surfaces lighter (not borders), muted text at 7:1.
- **Type**: Space Grotesk for display and numbers, Inter for text, IBM Plex Sans Arabic for Arabic. Scale 12 / 14 / 16 / 20 / 28 / 40; numbers are tabular; headings sentence case (the all-caps tracked label is retired except for one eyebrow style).
- **Space**: 8-pt grid (4, 8, 16, 24, 32, 48). Page gutter 16, section gap 24, inside a group 8.
- **Shape**: group = one surface with hairline dividers (list), not one card per row. Radius 16 for surfaces, full for pills. No shadows except the floating bar and sheets.
- **Content first**: campaign = logo/initials tile + deadline ring; clip = real thumbnail; money = large tabular number.
- **Motion**: 150 ms feedback, 280 ms sheets, `cubic-bezier(.2,.8,.2,1)` in, `cubic-bezier(.4,0,1,1)` out; list items enter with 8 px rise and 40 ms stagger on first load only; drag release uses a spring; `prefers-reduced-motion` removes movement and keeps fades.
- **Icons**: the existing 24-px, 1.9-stroke line set, one weight everywhere; directional icons flip in RTL.
- **Copy**: sentence case, plain, says the next step ("Add a video to cut"), errors say what happened and what to do.
- **Touch**: 44 px minimum targets; thumbs-zone actions at the bottom; sheets over pages for short tasks.
- **Loading**: skeletons shaped like the content; optimistic updates with undo; never a spinner alone.
