-- Phase C, the remaining decisions (2026-11-17):
-- audience countries read from the platform APIs or a screenshot, the language each account posts in,
-- and the settings for Arabic audiences and new accounts.

alter table accounts add column if not exists language text check (language ~ '^[a-z]{2}$');
-- The platform's own ID for the account (Instagram's insights need it). Not a secret.
alter table accounts add column if not exists platform_account_id text;
alter table accounts drop constraint if exists accounts_audience_source_check;
alter table accounts add constraint accounts_audience_source_check
  check (audience_source in ('manual', 'api', 'screenshot', 'estimate'));

insert into settings (key, value, "default", min, max, allowed, scope) values
  -- Arabic clips mostly reach viewers outside the US, UK, Canada and Australia (practitioner lesson).
  ('assumed_tier1_pct_arabic', '5', '5', 0, 100, null, 'account'),
  -- An account is "new" until it has this many tracked posts; new accounts see fixed-pay campaigns first.
  ('new_account_posts', '10', '10', 0, 200, null, 'account'),
  -- How often audience countries are read again from the platform APIs.
  ('audience_refresh_days', '7', '7', 1, 60, null, 'system');

-- Audience countries from the official APIs. Tokens come from environment variables only (never stored).
insert into adapters (stage, provider, plan, config_json, fallback_provider, is_fake, active) values
  ('audience', 'platform_apis', 'official APIs, free',
   '{"youtube":"YouTube Analytics API reports, dimensions=country, metrics=views (token env YOUTUBE_ANALYTICS_TOKEN)","instagram":"Instagram Graph API insights, metric=follower_demographics, breakdown=country (token env INSTAGRAM_GRAPH_TOKEN)","tiktok":"not available until the TikTok app passes review: use a screenshot of TikTok Studio > Viewers"}',
   'manual', false, true),
  ('audience', 'fake', null, '{}', 'manual', true, false),
  ('audience', 'manual', null, '{}', null, false, false)
on conflict do nothing;
