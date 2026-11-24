# Instagram Reels posting: owner setup (clipmind.official)

Official "Instagram API with Instagram Login" (scopes `instagram_business_basic`, `instagram_business_content_publish`). Nothing is posted without your swipe-right confirm; the app never posts on its own.

## One-time setup
1. **Account:** clipmind.official must be a Professional account (Creator or Business) and public.
2. **Meta app:** developers.facebook.com > Create app > type Business (or "Other" > Business) > add product **Instagram** > "API setup with Instagram login".
3. **Roles:** App roles > Roles > add clipmind.official as **Instagram Tester**, then accept the invite in Instagram (Settings > Apps and websites > Tester invites). In development mode only added testers can sign in; Standard Access is enough for your own account, so no app review is needed.
4. **Tailscale HTTPS** (the redirect must be HTTPS): install Tailscale on this laptop, sign in; in the admin console DNS page turn on **HTTPS certificates**; then run `python run.py phone`. It writes `CLIPPING_PUBLIC_ORIGIN=https://<laptop>.<tailnet>.ts.net` to `.env`. The redirect only has to work in your own browser (which is on your tailnet), so nothing is exposed publicly.
5. **Redirect URL:** in the Meta dashboard, Instagram > API setup with Instagram login > Business login settings > **OAuth redirect URIs**, add exactly
   `https://<laptop>.<tailnet>.ts.net/api/connections/instagram_reels/callback`
   (Meta may add a trailing slash: compare the saved list with the line above and keep it identical).
6. **`.env`** (never paste these in chat): `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET` are the **Instagram app** id and secret shown on that same page (not the Facebook app id), `TOKEN_VAULT_KEY` (a new random 32-byte key), `OAUTH_REDIRECT_BASE` = the same `https://...ts.net` origin (no trailing slash). Set `CLIPPING_USE_FAKES=0` when you want real calls.
7. **Connect:** Profile > Connections > Instagram > Connect. Sign in as clipmind.official and allow both permissions. You land back on Profile with "connected".

## Safe first test (dry run)
Set `CLIPPING_PUBLISH_DRY_RUN=1` in `.env` and restart. A confirmed Instagram post then creates the container, uploads the video and waits for Instagram to process it, but **never calls `media_publish`**. The post card shows "Dry run finished ... Nothing was published." and uses none of the 100-per-day allowance. Remove the line (or set it to 0) for real posting. Other platforms are refused while it is on.

## Paid partnership
The API does not expose the paid partnership label for this login type. It stays a checklist step: after the post is live, open it in Instagram and tick "Paid partnership" if the campaign requires it.

## Where tokens live
Sealed (AES-GCM) in the database with `TOKEN_VAULT_KEY` from `.env`; never returned to the web app, logged or put in alerts (`config.redact` scrubs the secrets). The long-lived token lasts about 60 days and is refreshed by the app; if it lapses, Profile shows "reconnect".
