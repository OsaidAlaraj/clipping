# Clipping Campaign System

Finds paid clipping campaigns, checks their rules, turns permitted footage into short English or Arabic clips, lets the owner review every clip on a phone, and tracks what each clip earns per hour. Built from `docs/spec.md` (v1.0).

Nothing here posts on its own. The owner posts from their own accounts and records the link.

## Run it

Needs Python 3.11 or later and Node 20 or later (with pnpm or npm). Postgres and FFmpeg are installed into `.venv` by pip, so there is nothing else to install.

```bash
python run.py
```

This creates `.venv`, installs packages, applies the database migrations, seeds a sample campaign from synthetic footage, and starts:

- the API and worker on `http://127.0.0.1:8787`
- the review app on `http://localhost:3000`

The first start takes about a minute while the sample clips render. Ctrl+C stops everything, including the local database. If the process is killed instead, the local Postgres can keep running in the background; it is reused on the next start, or stopped with `python run.py stop`. To use the app on a phone on the same Wi-Fi, open the "Network" address that the web server prints. Installing it to the iPhone home screen works from Safari (Share, Add to Home Screen); offline support needs the app served over HTTPS, which a local address is not.

## Test it

```bash
python run.py test
```

Runs the backend tests (pytest), the web tests (vitest) and the TypeScript check. Tests start their own throwaway database, use fake adapters for every vendor, and use real FFmpeg. No test calls a live service.

```bash
python run.py demo
```

Runs the sample campaign end to end on a fresh throwaway database and prints the run log (also saved in `docs/run-log.md`).

## Configure it

Copy `.env.example` to `.env` and fill in what you have. Every value is optional for a local run.

| Variable | What it does |
|---|---|
| `CLIPPING_USE_FAKES` | `1` (default) uses fake vendors. Set `0` to use real ones wherever their keys are set. |
| `DATABASE_URL` | A Postgres URL (for example Supabase). Empty uses the embedded local Postgres in `.data/pg`. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram alerts. |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` | The LLM for the three agents. Also set its price in the `adapters` table, or it stays paused. |
| `DEEPGRAM_API_KEY` | Transcription. |
| `VIZARD_API_KEY`, `KLAP_API_KEY` | Moment finding. |
| `BUFFER_ACCESS_TOKEN` | Scheduler (from week 4). |
| `R2_*` | Cloudflare R2 storage (needs `pip install boto3`). |
| `AUTH_MODE`, `SUPABASE_JWT_SECRET` | `local` (default, single owner) or `supabase`. |
| `CLIPPING_LOCAL_TOKEN` | Optional shared token for local mode when the app is reachable from other devices. |

Secrets live only in environment variables. They are never stored in the database or written to logs.

## Where things are

| Path | Contents |
|---|---|
| `docs/spec.md` | The specification. The source of truth. |
| `docs/decisions.md` | Choices made where the spec left a gap, one line each. |
| `docs/open-items.md` | What you need to supply or check, and what is not built. |
| `docs/run-log.md` | Test results and the sample-campaign run. |
| `backend/migrations/` | Schema, database guards, row-level security, seed data. |
| `backend/clipping/` | Adapters, agents, pipeline, review, publishing, ledger, alerts, API. |
| `backend/tests/` | One file per build step. |
| `backend/fixtures/briefs/` | Six sample briefs. |
| `web/` | The review app (Next.js PWA). |

## How the rules are enforced

The hard rules are enforced in the database as well as in code, so no code path can skip them:

- **Three human gates.** Campaign approval, clip acceptance and publish approval are refused by a trigger unless an identified operator is acting. The worker and the agents cannot pass a gate.
- **Audit log.** Every row change writes an audit row from a trigger. The table has no UPDATE or DELETE grant, and a second trigger refuses changes even from an admin connection.
- **Kill switch.** While it is on, inserting a job fails in the database, queued jobs are cancelled, and ready-to-post clips are hidden.
- **Hard blocks.** Gambling, unlicensed finance or crypto, a brief that forbids disclosure, and a brief that needs more accounts than allowed are found by fixed checks. The LLM cannot remove a block, and a blocked campaign cannot be approved.
- **Accounts.** An account has one owner (`not null`, one row per platform handle), and a post can only be recorded by that owner.
- **Settings.** Bounds are checked by a trigger. Unknown setting keys are refused, so no setting can override a hard rule.
- **Scrapers.** A marketplace scraper cannot be switched on until the terms version, read date and confirming owner are recorded.

## Sign-in and phone access (round 2)

- **Sign-in is by passkey** (Face ID, fingerprint or Windows Hello). On the first start the terminal prints a one-time setup code. Open the app, choose "Set up this device", enter the code and create your passkey. `AUTH_MODE=local` in `.env` turns sign-in off for trusted local runs; the tests use it.
- **Phone access goes through Tailscale**, which keeps the app private to your own devices and gives it an HTTPS address. Install Tailscale on the laptop and the phone, sign in to the same account, turn on MagicDNS and HTTPS certificates in the Tailscale admin console, then run:

```bash
python run.py phone
```

  It routes `/api` straight to the API (so large video uploads skip the web app's 10 MB proxy limit) and everything else to the web app, and records the address in `.env`. Restart, open the printed `https://…ts.net` address on the phone, choose "Set up this device" and enter a code from Settings › Add another device.
- **The laptop stays awake while jobs run.** Only the sleep timer is held; the screen can still turn off.
