<!-- base-commit: none (not a git repository) -->
<!-- generated: 2026-11-17 -->

# Code Standards

## Forbidden Patterns

**Never call an external service directly from pipeline or API code.** Go through the adapter registry, which handles cap, retry ×3, fallback, alert and audit:
```python
# BAD: no cost cap, no retry, no audit row, no fallback, impossible to fake in tests
response = httpx.post("https://api.deepgram.com/v1/listen", ...)

# GOOD (backend/clipping/pipeline.py stage_transcribe): the registry picks the active provider (fake in tests)
result, cost = registry.call(conn, "transcription", "transcribe", {"audio_path": str(audio), "language": lang}, owner_id)
```

**Never store or log secrets.** Keys live only in environment variables, read through `config.env`; adapter `config_json` holds prices, limits and endpoints only:
```python
# GOOD (backend/clipping/adapters/vendors.py): token read at call time, never written anywhere
headers={"Authorization": f"Token {config.env('DEEPGRAM_API_KEY', '')}"}
```

**Never bypass a human gate or post on the owner's behalf.** Approval, publish and rule changes are owner actions guarded by DB triggers (`migrations/002_rules.sql`). Agents only suggest.

**Never edit an applied migration.** Add the next numbered file (`backend/migrations/00N_name.sql`); `Database.migrate()` applies new files once.

**Never write full brief text or tokens to the audit log.** Audit details carry ids, hashes and counts (`test_audit_log_never_stores_brief_text`).

## Code Examples

### A pipeline stage
```python
# DO (backend/clipping/pipeline.py): one job = one stage, works on its own session, enqueues the next stage
def stage_render(conn, job: dict) -> Decimal:
    clip, source, campaign, version = _clip_ctx(conn, job["payload"]["clip_id"])
    result, cost = registry.call(conn, "render", "render", {...}, clip["owner_operator_id"])
    enqueue(conn, "captions", {"clip_id": str(clip["id"])}, clip["owner_operator_id"],
            source_id=source["id"], campaign_version_id=version["id"])
    return cost          # the worker records cost on the job
# DON'T: loop over several stages in one handler, or swallow errors; the worker retries non-AppError failures
```

### An API endpoint
```python
# DO (backend/clipping/api.py): thin handler, actor from the session, domain function does the work
@app.post("/api/listings/{listing_id}/add")
def add_listing(listing_id: str, body: AddListingIn, actor: Actor = Depends(current_actor)):
    return run(actor, discovery.add_listing, actor, listing_id, body.category, body.licence_ref)
```

## Error Handling

An expected refusal is `AppError(message, code)`; the API turns it into a 400 with `{"error", "code"}`. Messages are plain sentences the owner can act on. Vendor failures raise `AdapterError` inside adapters; the registry retries, falls back, alerts.
```python
# backend/clipping/core.py
raise AppError("Marketplaces were checked in the last 10 minutes. Try again a little later.", "too_soon")
```
- Subclass `AppError` for a refusal that callers branch on (`FeedRateLimited`, `AdapterPaused`, `AdapterUnavailable`).
- A failed declared-fallback stage raises an Alert and degrades (e.g. moments → manual selection), never silently drops work.

## Imports & Dependency Direction

`api.py` → domain modules (`campaigns`, `pipeline`, `discovery`, `ranking`, `review`, `publish`, `ledger`, `audience`) → `core`, `db`, `alerts.registry` → `adapters/*`. Adapters never import domain modules.
```python
# backend/clipping/alerts.py registers providers by importing adapter modules once
from .adapters import audience as _audience, media, vendors  # noqa: F401  (registers providers)
```
Heavy or optional imports (httpx, yt-dlp, numpy) go inside the function that needs them.

## Testing Patterns

pytest with a real embedded Postgres; each test gets a fresh database cloned from a migrated template. Every external stage has a fake activated in `tests/conftest.py`.
```python
def test_rate_limit_stops_the_pass_without_retries_or_fallback(db, owner):   # fixtures: db, owner, produced
    feed.FAKE_DISCOVERY["fail"] = "rate"                                      # module-level fake controls, reset per test
    result = refresh(db, owner)
    assert result["rate_limited"] is True
```
- Test names are full sentences about behaviour.
- Act as the right actor: `db.session(owner)`, `Actor.worker()`, `Actor.system()`. RLS is on in tests.
- API tests: `api._owner = owner; TestClient(api.app)`.
- Every phase adds a file `backend/tests/test_N_<phase>.py`; at least one error-path test per feature.
- Web: vitest + Testing Library; `mockApi({...})` from `web/tests/helpers`.

## Naming Conventions

- Pipeline handlers `stage_<name>(conn, job)`; job types are the stage names (`HANDLERS` in pipeline.py).
- Adapters: class `<Provider><Stage>` with `stage, provider = "<stage>", "<provider>"`; a `Fake<Stage>` twin with module dict `FAKE_<STAGE>`.
- Settings keys snake_case with unit suffix: `min_seconds_between_calls`, `portfolio_max_share_pct`, `audience_refresh_days`.
- User-facing copy: short plain sentences, US English in code and UI, "you" for the owner.

## File Organization

- New domain logic: `backend/clipping/<area>.py`; vendor integrations: `backend/clipping/adapters/<area>.py`.
- Schema and seed: next `backend/migrations/00N_*.sql` (tables get RLS + policies in the same file).
- Saved real vendor samples for fakes: `backend/fixtures/<vendor>/*.json`.
- Web screens: `web/components/<Area>.tsx`, routes in `web/app/<route>/page.tsx`; shared calls in `web/lib/api.ts`.
- Decisions and open items: `docs/decisions.md`, `docs/open-items.md`.

## Technology Decisions

- Python 3.14 venv in `.venv`; Postgres 16 embedded via pixeltable-pgserver (`.data/pg`); FFmpeg from imageio-ffmpeg (has libass, harfbuzz, fribidi, nvenc).
- RLS on every table; identity via `set_config('app.operator_id'/'app.role', …, true)`; long sessions use `Database.checkpoint(conn)`.
- Front end: Next.js 16, React 19, one design language for phone and desktop (light theme, tokens in `web/app/globals.css`).
- Uploads go straight to :8787 (Next proxy limit 10 MB).

## Exemplar Files

- Adapter with live + fake + manual fallback: `backend/clipping/adapters/discovery.py`, `backend/clipping/adapters/audience.py`
- Domain module with audit and RLS-aware queries: `backend/clipping/discovery.py`
- Migration adding a table with RLS: `backend/migrations/007_discovery_fixes.sql`
- Behavioural tests with fakes and API: `backend/tests/test_8_phase_c_complete.py`
- Screen with filters, phone and desktop: `web/components/Explore.tsx`
