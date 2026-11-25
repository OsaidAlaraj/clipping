# Hook quality: diagnosis, bake-off 2, what was adopted (2026-11-25)

## Why the hooks read badly (84 real hooks from the app database, read next to their clips)
1. **Empty teasers.** The lexicon formats force formulas whose words come from nowhere in the clip: "Wait for the frog part", "Bet you miss the direwolf detail", "Wait for him to say Racai", "Hear me out kids built TikTok" (broken). Measured on 59 held-out moments: **32 % of production hooks are empty teasers (40 % in English)**; the new chain: 0 %.
2. **Topic labels dressed as questions.** "How do lawyers choose cases", "Can courts stop runaway innovation", "Why Universal wanted Google money". Nothing surprising, nothing from the clip's best line (the clip said "the only two industries that use the word users are drug dealers and software").
3. **The model grades itself.** The writer's own rubric picks the winner, so formula lines that score "curiosity gap" win.
4. **Arabic:** the Deepgram text is often garbled and the writer invented from garbage ("ليش الفايبريتر على بيضاته"), and mixed dialects (تليفون, Egyptian) into "Jordanian".

## Research (read-only)
Top Shorts of 20 channels (1,200 titles, `.data/market/shorts_all.jsonl`, outlier = views / channel median) and guides: winners are plain claims, names, numbers, the speaker's own strong line; trend tags (POV, Nobody) are 0 % of winners; on-screen text should add context, not repeat audio ([playbook](https://playbooks.com/skills/cdeistopened/opened-vault/video-caption-creation), [Soundstripe](https://www.soundstripe.com/blogs/how-to-clip-a-podcast)). Clipster's domain gave a certificate error, Content Rewards and TikTok Creative Center were not reachable in the time; not read.

## Can a scorer stand in for market taste? No (validated, failed)
240 held-out pairs (top vs bottom quarter of the same channel, both A/B orders), `scorer_validation.md`:
all scorers sit at chance: feature model 53 %, LLM "which got more views" 53 %, the bake-off 1 "stop scrolling" judge 49 %, LLM with 16 market examples 53 % (±6). Titles carry topic and timing more than wording, so a wording scorer is not validated by this data. **Therefore no judge here is a proven market judge**, including bake-off 1's. Per owner rule, none was adopted as the arbiter.

## Bake-off 2 (fresh transcripts, none from bake-off 1; prompts tuned on 22 dev moments, then 60 untouched test moments; old = production)
Win rate vs production (both orders; an arm that writes nothing loses):

| Arm (test, n=57) | J1 stop-scrolling judge | J2 truth and natural-language judge |
|---|---|---|
| spec (producer, writer, editor) | 37 % (en 42, ar 24) | **67 %** (en 66, ar 68) |
| angle (owner's angle finder, per-angle writer) | 41 % | 40 % |
| quote (dev only) | 21 % | 43 % |

Failures (no valid hook): old 1, spec 1, angle 3. Angle invented claims ("Netflix owns your childhood") and was weaker; not adopted.
Independent blind read by me of 39 test pairs (A/B shuffled, unblinded after): spec chosen **33 of 39** (one reader, taste for specificity, not market proof).
Objective, no taste: hooks with a concrete anchor from the clip 100 % (spec) vs 88 % (old); empty teasers 0 % vs 32 %.

## Decision
No arm is a "clear winner" under the pre-set rule (>= 60 % on both judges): spec fails J1 (which rewards the curiosity formulas that diagnosis item 1 calls the defect, and which scored chance against the market). Adopted anyway as the default generator because it removes a measured defect, wins J2 and my blind read, and keeps the old format generator as the third-try fallback. **This is a judgement call the owner can reverse**: `agents/hooks.py::run` (attempts 0 and 1 use the chain; change `attempt < 2` to `attempt < 0` to restore the old behaviour). Not proven by market data; the thumbs and `hook_pattern_stats` remain the real test.

## Not done / next
- Arabic: judged only by LLMs; needs the owner's native read of `test.json` Arabic rows. The product prompt now asks for Jordanian/Levantine (changed after the bake-off, so untested).
- Angle finder with sampled frames: not built (no frames in the test inputs); angle arm used transcript plus the next 40 s only.
- First-3-to-5-second on-screen text, cut rate and Clipster/Content Rewards earning clips: not collected (access); needed to validate a wording scorer.
- Per-clip owner-typed hooks: out of scope (clip-editor session).

Files: `build_moments.py`, `arms.py`, `prompts.py`, `run.py`, `collect_titles.py`, `validate_scorer.py`, `specificity.py`, `dev.*`, `test.*`, `blind_key.json`.
