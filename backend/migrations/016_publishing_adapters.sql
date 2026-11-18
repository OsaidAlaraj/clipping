-- Phase E, phase 2: publish adapters. Quota ledger plus the two `publishing` providers (no secrets; tokens come from the vault).

-- One row per upload that started, so a daily limit can be checked before the next call. Owner-only, like connections.
create table publish_quota_log (
  id uuid primary key default gen_random_uuid(),
  at timestamptz not null default now(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  connection_id uuid not null references platform_connections(id) on delete cascade,
  platform text not null check (platform in ('youtube_shorts', 'instagram_reels', 'tiktok')),
  units numeric not null check (units >= 0)
);
create index publish_quota_log_window on publish_quota_log (platform, connection_id, at);

alter table publish_quota_log enable row level security;
alter table publish_quota_log force row level security;
grant select, insert on publish_quota_log to app_user, app_worker;
create policy p_select on publish_quota_log for select to app_user using (owner_operator_id = app.operator_id());
create policy p_write on publish_quota_log for insert to app_user
  with check (app.role() in ('admin', 'operator') and owner_operator_id = app.operator_id());
create policy p_worker on publish_quota_log for all to app_worker using (true) with check (true);

-- Providers stay inactive: callers pick one per call (fake while CLIPPING_USE_FAKES is on), like the oauth stage.
-- limits: units_per_upload / max_units_24h per platform; scope = whose quota it is (YouTube: the whole Google project).
-- unverified_project: Google locks uploads from unaudited projects to private, so private is forced until this is false.
insert into adapters (stage, provider, plan, config_json, fallback_provider, is_fake, active) values
  ('publishing', 'platforms', 'official APIs: YouTube Data v3, Instagram Graph, TikTok Content Posting (inbox)',
   '{"unverified_project":true,
     "paid_partnership_supported":{"youtube_shorts":true,"instagram_reels":false,"tiktok":false},
     "instagram_api_version":"v21.0","poll_attempts":10,"poll_interval_s":30,"timeout_s":300,"chunk_bytes":10485760,
     "limits":{"youtube_shorts":{"units_per_upload":1600,"max_units_24h":10000,"scope":"platform","caption_max":5000},
               "instagram_reels":{"units_per_upload":1,"max_units_24h":100,"scope":"connection","caption_max":2200},
               "tiktok":{"units_per_upload":1,"max_units_24h":5,"scope":"connection","caption_max":2200}}}',
   null, false, false),
  ('publishing', 'fake', null,
   '{"unverified_project":true,
     "paid_partnership_supported":{"youtube_shorts":true,"instagram_reels":false,"tiktok":false},
     "instagram_api_version":"v21.0","poll_attempts":3,"poll_interval_s":0,"timeout_s":30,"chunk_bytes":10485760,
     "limits":{"youtube_shorts":{"units_per_upload":1600,"max_units_24h":10000,"scope":"platform","caption_max":5000},
               "instagram_reels":{"units_per_upload":1,"max_units_24h":100,"scope":"connection","caption_max":2200},
               "tiktok":{"units_per_upload":1,"max_units_24h":5,"scope":"connection","caption_max":2200}}}',
   null, true, false)
on conflict do nothing;
