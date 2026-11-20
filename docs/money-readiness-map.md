# Money readiness map (2026-11-19): every factor that decides whether ClipMind earns

Status: DONE (built and reviewed) / PARTLY / MISSING (not built) / OWNER (only the owner can do it) / OPEN (decision or research).

## 1. Income model
- Campaign choice by real yield, not headline CPM: PARTLY (Monte Carlo ranking exists; effective-yield and budget-runway inputs MISSING; no scraping that terms forbid)
- Volume x hit rate: most clips earn little; plan for many tested clips, not one viral clip: OPEN
- Per-clip caps and budget exhaustion tracking: MISSING
- Fees and cash cycle in net-per-hour: PARTLY (Whop 10% seller-side fee is in the terms; payout timing is not)
- Campaign type: per-post or retainer versus CPM, check fees on the official page: OPEN
- Direct deals with creators and agencies once the first numbers exist: OPEN
- Second marketplaces to avoid single-point dependence: OPEN

## 2. Accounts and identity
- 3 niches x 2 languages x 3 platforms, phased: start small, expand after proof: OWNER
- Business mailboxes and aliases on the own domain: DONE; 2FA and recovery set: OWNER
- Account creation blocked by Google and TikTok: retry from phone apps, one a day: OWNER
- Creator versus business account type per platform (music and eligibility trade-offs): OPEN
- Normal-use warm-up, bios, links, consistent niche; tracking sheet: OWNER
- Campaign eligibility thresholds (followers, account age, region): OPEN
- Developer apps for Google, Meta, TikTok (posting by API): OWNER (session ready); manual posting works meanwhile

## 3. Rights, policy and compliance
- Written permission per source, kept with the clip: PARTLY (rules and audit log exist; permission letter template MISSING)
- Originality: permission is not enough; add real value (commentary, structure, framing): MISSING (originality layer)
- One source moment to one account, never the same moment on several: RULE ADDED
- Disclosure (#ad or paid-partnership label) top of caption and in the clip: DONE
- Music licensing: royalty-free burned-in with a per-clip log, platform libraries in-app only: DONE with 37 free CC0 beds (no credit lines), per-clip licence log; thin spots listed in docs/bakeoff/final/music
- Captions that match the video (no misleading descriptions): RULE ADDED, caption checker DONE (caption lab, branch caption-lab)
- Brand and adult safety flags: PARTLY
- Privacy and terms pages: DONE (clipmind.app); kill switch and human gates: DONE
- Policy changes (YouTube Oct 2026, Meta Mar 2026): VERIFIED by reports, read official pages: OPEN

## 4. Product quality
- Language check at upload and thin-transcript retry: DONE (branch merged)
- Real face detection with smoothing (YuNet): DONE; active-speaker choice (LR-ASD, FabuLight, ASDnB): OPEN bake-off
- Gaming stacked layout (face-cam on top, gameplay below): MISSING
- Sentence-end snap: DONE; cold open, dead-air and filler tightening, punch-in zooms: MISSING
- Animated word-by-word captions, Arabic RTL tested: PARTLY
- Sharpness of 720p sources (upscale or sharpen), clean audio, loudness target: MISSING
- Best cover frame: MISSING; per-platform packaging (title, caption, hashtags): DONE (branch caption-lab)
- Pre-review quality gate with measured standards: MISSING
- Contact sheet and fixed test videos for before/after checks: PARTLY
- Arabic dialect hooks with native check, dialect ASR bake-off: OPEN

## 5. Distribution and growth
- Posting cadence and timing per audience; hook, caption keyword and cover discipline: OPEN
- Caption lab: matched (green) and honest curiosity (amber) variants, A/B on different moments: MISSING
- Comment replies, series and clip families, search keywords: MISSING
- Per-platform priority given originality rules (TikTok and Instagram first?): OPEN

## 6. Measurement and learning
- Earnings ledger, net per hour, per-campaign ROI: PARTLY (ledger exists; Phase F core not built)
- Platform analytics (retention curves, completion, shares, saves) as the learning target: MISSING
- Market benchmark (crowd replay heatmaps, Twitch clips) for tuning: PLAN written, not run
- Learning from owner decisions and 48 h views: DONE (hourly)

## 7. Operations and cost
- Review time per clip and daily routine, phone-first review: DONE; throughput dashboard MISSING
- Token and API cost: Deepgram credit, Azure student credit; per-source cost caps MISSING
- Backups of the local database (drive L and a free cloud copy): MISSING
- Reliability: retries, alerts, queue health: PARTLY
- Move to a server only after first real campaigns: OPEN
- Repo: Phases A to E merged on the working branch; protect `main` after first stable release: OPEN

## 8. Owner business admin (not advice)
- Payout method and currency set up with the marketplace; local tax and registration rules checked with a professional: OWNER
- Keep records of permissions, payouts and music licences: OWNER

## 9. Risk
- Account strike or ban: keep accounts separate by niche and language, normal behaviour, no evasion: RULE
- Marketplace rejection (brief rules, duplicates, disclosure): checklist: MISSING in app
- Platform rule changes: re-verify monthly: OPEN
- Single-platform or single-marketplace dependence: OPEN

## Shortest path to a first dollar
1. One account on the platform with the best fit (TikTok or Instagram first), set up by the owner from the phone.
2. One real campaign whose brief allows the clip style, chosen by effective yield.
3. 5 to 10 clips from one source with a quality gate, a matched caption and the disclosure, posted manually with one confirm tap each.
4. Measure retention and payout, then tune.
