# Discovery + Design: Phase 2 - Publish adapters

## Files Found
- backend/clipping/adapters/base.py (registry: 3 attempts, fallback), oauth.py (provider_name, fake pattern), vault.py (access_token), alerts.py (registers adapters), tests/test_16*.py (fake connect helper, MockTransport pattern), migrations 015 (RLS + adapters seed).

## Gaps
- Adapter methods receive only the request kwargs: no conn, no owner, no alert hook. Fix in base.py: `Registry.call` binds `adapter.conn / owner_id / alert()`; new `Adapter.no_retry` tuple makes one attempt, no fallback.
- Contract call has no `provider`; follow decision #7: rows stay inactive, callers pass `provider=publishing.provider_name()` (fake while CLIPPING_USE_FAKES, else "platforms").
- Quota windows are per day / per account; `adapters.quota_used` is monthly. New table `publish_quota_log` (migration 016).
- Instagram resumable needs a second method to publish a container that was still processing: added `finish_publish` (no-retry), not in the plan's Produces (additive).

## Assumption verification (read-only doc fetches, 2026-11-18)
- YouTube resumable protocol (developers.google.com/youtube/v3/guides/using_resumable_upload_protocol): CONFIRMED. POST upload URL ?uploadType=resumable&part=..., Location = session URI, PUT bytes -> 201 + video resource; 5xx/dropped = unknown, 4xx permanent.
- YouTube unverified project locked to private (HIGH): NOT CONFIRMED by the fetched pages (videos.insert page says nothing; summarizer read it as "not restricted"). Kept as config `unverified_project: true` (safe direction: forces private). Flip to false after the audit. Result also reports "kept private" if the platform answers private to a public request.
- YouTube paid placement (MED): `paidProductPlacementDetails.hasPaidProductPlacement` exists in the video resource (docs/videos) but writability is "not stated". Sent when config `paid_partnership_supported.youtube_shorts` is true (default true; a 4xx is a safe pre-upload failure, owner flips config to false and uses the Studio checklist).
- Instagram (developers.facebook.com/docs/instagram-platform/content-publishing and the Instagram-Login variant): REELS container -> rupload.facebook.com upload (headers Authorization: OAuth, offset, file_size) -> status_code (EXPIRED/ERROR/FINISHED/IN_PROGRESS/PUBLISHED) -> media_publish. Limit 100 API posts / 24 h. CAVEAT (MED->LOW): `upload_type=resumable` is documented "only for Facebook Login for Business" apps; phase 1 signs in with Instagram Login. Untested live; fallback (signed URL via storage, or manual path) stays open. Permalink retrieval not documented on those pages (GET media?fields=permalink used, best effort, url None if absent).
- Instagram `is_paid_partnership` (LOW): documented "Facebook Login only" -> seeded `paid_partnership_supported.instagram_reels: false`; sent only when config says true.
- TikTok inbox upload (HIGH): POST /v2/post/publish/inbox/video/init/ (scope video.upload), FILE_UPLOAD with video_size/chunk_size/total_chunk_count, PUT chunks with Content-Range to a 1 h upload_url; status via POST /v2/post/publish/status/fetch/ (SEND_TO_USER_INBOX, PUBLISH_COMPLETE, FAILED+fail_reason). Pending-share cap 5 / 24 h. The audit is not mentioned as required for inbox upload.

## Design (design it twice)
A. One class per platform + registry provider per platform. Contract call has no provider, so callers must route: leaks platform knowledge upward.
B. One `platforms` provider, platform in the request, per-platform private methods. Fake = same class with a fixture-driven `httpx.MockTransport` (one code path, so the fake proves real validation/ambiguity logic).
C. Generic step-table interpreter. Over-abstract for 3 platforms.
Choice: B. Interface: `publish_video`, `finish_publish`, `check_status` (3 methods hide quota, caption limits, token handling, chunking, polling, ambiguity rules).
Rule: `_started` flips when the first byte is sent. Before it, any failure = `failed` with reason; after it, any non-success or unreadable answer = `needs_check` (post id when known). `publish_video` never retried (registry `no_retry`).

## DW Verification
| DW-ID | Status | Tests |
|---|---|---|
| DW-2.1 | COVERED | fake states + shape for 3 platforms (published/private/draft/processing), check_status shape |
| DW-2.2 | COVERED | timeout mid-upload -> needs_check, 1 call (real + fake); pre-upload 4xx -> failed with reason |
| DW-2.3 | COVERED | request bodies via MockTransport: YT private + paid flag, TikTok inbox endpoint, IG public + url, paid option only when config supports |
| DW-2.4 | COVERED | quota refused before any call + alert; malformed responses -> safe outcome + alert |
**All items COVERED:** YES

## Recommendation
BUILD
