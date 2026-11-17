-- Phase C: campaign discovery (ClippingAlpha feed + quick capture), audience geography, niches, ranking inputs.

-- Marketplaces found in research (fees as published; null = unknown, never guessed).
insert into marketplaces (name, slug, base_url, pay_model, fee_rules, withdrawal_methods, hold_rules, platforms, source_adapter, notes) values
  ('Shoot', 'shoot', 'https://tryshoot.com', 'cpm',
   '{"creator_fee":[{"models":["cpm"],"pct":7}],"note":"7% platform fee per its terms (2026-10-22)"}',
   '[{"method":"wise_bank","min_usd":10,"fee_usd":null},{"method":"usdt_ethereum","min_usd":10,"fee_usd":null}]',
   '{}', '{tiktok,instagram_reels,youtube_shorts}', 'paste', 'Terms forbid scraping. Manual capture only.'),
  ('ClipAffiliates', 'clipaffiliates', 'https://www.clipaffiliates.com', 'cpm',
   '{"creator_fee":null,"note":"9% reported by a third party; site was down when checked"}', '[]', '{}',
   '{tiktok,instagram_reels,youtube_shorts}', 'paste', 'Terms not read: site was unreachable on 2026-11-17.'),
  ('Clipping.io', 'clipping_io', 'https://clipping.io', 'cpm', '{"creator_fee":null}', '[]', '{}',
   '{tiktok,instagram_reels,youtube_shorts}', 'paste', 'Blocked visitors when checked.'),
  ('Promote.fun', 'promote_fun', 'https://www.promote.fun', 'cpm', '{"creator_fee":null}', '[]', '{}',
   '{tiktok,instagram_reels,youtube_shorts,x}', 'paste', 'Discovered through ClippingAlpha.'),
  ('Clipster', 'clipster', 'https://www.clipster.com', 'cpm', '{"creator_fee":null}', '[]', '{}',
   '{tiktok,instagram_reels,youtube_shorts}', 'paste', 'Discovered through ClippingAlpha. Reviews report slow payouts.'),
  ('Clipstake', 'clipstake', 'https://www.clipstake.com', 'cpm', '{"creator_fee":null}', '[]', '{}',
   '{tiktok,instagram_reels,youtube_shorts}', 'paste', 'Discovered through ClippingAlpha.'),
  ('Evangelist', 'evangelist', 'https://evangelist.so', 'cpm', '{"creator_fee":null}', '[]', '{}',
   '{tiktok,instagram_reels,youtube_shorts}', 'paste', 'Run by the ClippingAlpha team.')
on conflict (slug) do nothing;

update marketplaces set fee_rules = '{"creator_fee":null,"withdrawal_pct":3,"note":"3% withdrawal fee per its terms (2026-11-16)"}',
       withdrawal_methods = '[{"method":"crypto","min_usd":10,"max_usd":1000,"fee_pct":3}]',
       platforms = '{tiktok,instagram_reels,youtube_shorts,x,facebook}', notes = 'Terms unclear on scraping: manual capture only. Brands get a lasting licence to your clip.'
 where slug = 'clipflip';
update marketplaces set notes = 'Terms forbid scraping (section 2(g)). Discovered through ClippingAlpha; manual capture.' where slug = 'vyro';
update marketplaces set notes = 'Terms forbid scraping. Manual capture only. Payment may be direct between brand and creator.' where slug = 'elev8or';

-- Discovered campaigns (market data). Not the owner's campaigns until added; adding runs intake and the approval gate.
create table listings (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  source text not null check (source in ('clippingalpha', 'capture', 'fake')),
  external_id text not null,
  ref text,                                   -- brand/series for the feed's own lookups
  title text not null,
  brand text,
  marketplace_id uuid references marketplaces(id),
  board_slug text,
  agency text,
  campaign_type text,                         -- clipping | ugc | logo | music
  pay_model text,
  platforms text[] not null default '{}',
  niche text,
  rate_per_1k numeric,
  net_per_1k numeric,
  budget_left numeric,
  budget_total numeric,
  spent_per_day numeric,
  drain_p10 timestamptz,
  drain_p50 timestamptz,
  stalled boolean,
  alpha_score numeric,
  alpha_tier text,
  open_join boolean,
  region text,
  audience text,
  audience_countries text[] not null default '{}',
  languages text[] not null default '{}',
  min_views numeric,
  min_payout numeric,
  max_payout numeric,
  board_fee_pct numeric,
  payout_window_days numeric,
  qualify_share numeric,                      -- the feed's estimate of the share of clips that qualify
  brief_grade text,
  brief_flags jsonb not null default '[]',
  checklist jsonb not null default '[]',
  age_restricted boolean not null default false,
  first_seen_at timestamptz,
  new_at timestamptz,
  page_url text,
  join_url text,
  brief_url text,
  picture_url text,
  state text not null default 'live' check (state in ('live', 'ended')),
  last_seen_at timestamptz not null default now(),
  campaign_id uuid references campaigns(id) on delete set null,
  dismissed_at timestamptz,
  raw jsonb not null default '{}',
  unique (source, external_id)
);
create index listings_live on listings (state, last_seen_at);

create table listing_snapshots (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  listing_id uuid not null references listings(id) on delete cascade,
  seen_at timestamptz not null default now(),
  budget_left numeric,
  rate_per_1k numeric,
  alpha_score numeric
);
create index listing_snapshots_by on listing_snapshots (listing_id, seen_at);

create table market_snapshots (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  source text not null,
  as_of timestamptz not null,
  data jsonb not null
);

alter table campaigns add column listing_id uuid references listings(id) on delete set null;
alter table campaign_versions add column brief_grade text;

-- Audience geography per account (from platform analytics; entered by hand until the APIs are connected).
alter table accounts add column audience_tier1_pct numeric check (audience_tier1_pct between 0 and 100);
alter table accounts add column audience_countries jsonb;
alter table accounts add column audience_source text check (audience_source in ('manual', 'api', 'estimate'));
alter table accounts add column audience_updated_at timestamptz;

-- The owner's niches (onboarding; Explore defaults to them).
alter table operators add column niches text[] not null default '{}';

do $$ declare t text; begin
  foreach t in array array['listings', 'listing_snapshots', 'market_snapshots'] loop
    execute format('create trigger touch before update on %I for each row execute function app.touch_updated_at()', t);
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('grant select on %I to app_user', t);
    execute format('grant select, insert, update on %I to app_worker', t);
    execute format('create policy p_select on %I for select to app_user using (true)', t);
    execute format('create policy p_worker on %I for all to app_worker using (true) with check (true)', t);
  end loop;
end $$;
-- The owner links a listing to a campaign and may dismiss a listing.
grant update on listings to app_user;   -- owners refresh a listing's brief (enrich) and link or dismiss it
create policy p_owner_update on listings for update to app_user using (app.role() in ('operator', 'admin')) with check (app.role() in ('operator', 'admin'));
-- Operators may set their own niches.
grant update (niches) on operators to app_user;
create policy p_own_niches on operators for update to app_user using (id = app.operator_id()) with check (id = app.operator_id());

insert into settings (key, value, "default", min, max, allowed, scope) values
  ('discovery_interval_hours', '12', '12', 2, 48, null, 'system'),
  ('assumed_tier1_pct', '30', '30', 0, 100, null, 'account'),
  ('assumed_median_views', '2500', '2500', 100, 1000000, null, 'account'),
  ('minutes_per_clip', '30', '30', 5, 180, null, 'operator'),
  ('portfolio_max_share_pct', '40', '40', 10, 100, null, 'operator');

insert into adapters (stage, provider, plan, config_json, fallback_provider, is_fake, active) values
  ('discovery', 'clippingalpha', 'free public feed', '{"endpoint":"https://clippingalpha.com/mcp/general","per_call":25,"note":"Data: ClippingAlpha (clippingalpha.com), CC BY 4.0. Rates and budgets come from each platform''s public pages; the platform is the source of truth."}', 'manual', false, true),
  ('discovery', 'fake', null, '{}', 'manual', true, false),
  ('discovery', 'manual', null, '{}', null, false, false);
