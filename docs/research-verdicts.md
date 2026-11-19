# Outside research verdicts (2026-11-19)

Six research answers plus a business-width answer and a repo survey came from another AI. Checked here: repo existence, licences and last-push dates through the GitHub API. Not checked: accuracy figures, WER numbers, policy quotes (read the official pages before relying on them).

## Verified
| Claim | Result |
|---|---|
| LR-ASD, FabuLight-ASD, Light-ASD, TalkNet-ASD: MIT | True. Pushed 2025-03, 2024-12, 2025-03, 2023-10 |
| ASDnB is CC BY-NC-SA (non-commercial) | **False for the code**: the repo LICENSE is MIT (copied from Light-ASD), pushed 2026-06. Pretrained weights and dataset terms (AVA, WASD) still need checking |
| OpenShorts MIT, last push 2025-12 | MIT true, but it is **active** (pushed 2026-11-19, 9.3k stars) |
| lite.ai.toolkit Apache/MIT | **False**: GPL-3.0 (fine for private use) |
| klam MIT or Elastic | Apache-2.0 |
| clipsai "low maintenance" | Stale: last push 2024-01 |
| Clippy has no licence | True (NOASSERTION) |
| StreamCut "battle-tested", MIT | MIT true, but **created 2026-11-17, 0 stars**. Its facecam approach (OpenCV YuNet, perimeter presets, filters game characters, split layout 1080x1200 over 1080x720) is exactly our need; read `studio/face_layout.py` as a reference |
| Whop MCP by a named handle | Not found. A different repo, george11642/content-rewards-toolkit (MIT), exists; not reviewed |

## Contradictions and red flags inside the answers
- The business answer labels a "7+3 day hold" and a "0% fee on per-post campaigns of $5,000 or more" as official; the Whop-terms answer says the terms contain neither (10% seller-side fee, "unique deliverables", campaign-level Max Payout). Trust the terms answer; verify on the official page.
- The business answer recommends about 60 uploads a day across several accounts and per-device isolation; the policy answer says one source moment on several accounts is forbidden or high-risk grey. Rejected: it conflicts with the policy finding and is account-network evasion.
- Single-source stats (12% median effective CPM, 56% paid nothing) and creator earnings are unverified.
- "Chirp 3 beats Deepgram on Gulf" and the Scribe v2 code-switching figures come from single papers; we have not measured them.

## Adopt (as tests, nothing shipped yet)
1. Active-speaker bake-off on our 15 real segments, including a listener shown from behind: LR-ASD, FabuLight-ASD (body pose), ASDnB, against our YuNet baseline. Only a measurable win ships.
2. Gaming layout: borrow StreamCut's perimeter prior and split-layout maths; we already run YuNet.
3. Arabic ASR: our own small per-dialect bake-off on real samples before adding any vendor (Google Chirp 3, ElevenLabs Scribe).
4. Music: a licensed royalty-free subscription for burned-in beds; platform libraries only in-app on the manual path.
5. Official Whop facts only: 10% seller fee, unique deliverables, campaign-level Max Payout; export API has payouts and financials but no per-clip table; no scraping.
6. One source moment goes to one account. This DROPS the earlier idea of reusing one source for an English and an Arabic account.
7. klam and OpenShorts as references (Arabic caption handling; MCP orchestration); read before copying.
