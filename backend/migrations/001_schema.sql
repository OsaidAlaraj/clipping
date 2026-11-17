-- Clipping Campaign System: schema (spec section 11).
-- Every table has id, created_at, updated_at, created_by.

create schema if not exists app;

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'app_user') then create role app_user nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'app_worker') then create role app_worker nologin; end if;
end $$;
grant app_user to current_user;
grant app_worker to current_user;
grant usage on schema app to app_user, app_worker;
grant usage on schema public to app_user, app_worker;

-- Who is acting. Set per transaction by the backend (set_config(..., true)).
create or replace function app.operator_id() returns uuid language sql stable as $$
  select nullif(current_setting('app.operator_id', true), '')::uuid
$$;
create or replace function app.role() returns text language sql stable as $$
  select nullif(current_setting('app.role', true), '')
$$;
-- A human actor is an identified operator on the app_user role. The worker and agents are never human.
create or replace function app.is_human() returns boolean language sql stable as $$
  select app.operator_id() is not null and app.role() in ('operator', 'admin') and current_user <> 'app_worker'
$$;

create or replace function app.touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end $$;

-- ---------------------------------------------------------------- reference tables

create table operators (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  name text not null,
  role text not null default 'operator' check (role in ('operator', 'reviewer', 'admin')),
  status text not null default 'active' check (status in ('active', 'inactive')),
  payout_ref text,                       -- a reference ID only, never bank details
  daily_hours_cap numeric not null default 5,
  languages text[] not null default '{en}',
  auth_user_id text unique               -- auth adapter subject (Supabase user id)
);

create table marketplaces (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  name text not null unique,
  slug text not null unique,
  base_url text,
  pay_model text,
  fee_rules jsonb not null default '{}',
  withdrawal_methods jsonb not null default '[]',
  hold_rules jsonb not null default '{}',
  platforms text[] not null default '{}',
  source_adapter text not null default 'paste' check (source_adapter in ('paste', 'scraper', 'api', 'none')),
  scraper_enabled boolean not null default false,
  terms_version text,
  terms_read_at timestamptz,
  terms_confirmed_by uuid references operators(id),
  notes text,
  -- Hard rule 3: a scraper stays off until the owner has read and recorded the terms.
  constraint scraper_needs_terms check (
    not scraper_enabled or (terms_version is not null and terms_read_at is not null and terms_confirmed_by is not null)
  )
);

create table categories (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  name text not null unique,
  default_platforms text[] not null default '{tiktok,instagram_reels,youtube_shorts}',
  caption_language text not null default 'en',
  disclosure_template_id uuid,
  music_policy text not null default 'campaign_audio_only',
  banned_words text[] not null default '{}'
);

create table disclosure_templates (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  category_id uuid references categories(id) on delete cascade,   -- null = default for the language
  language text not null,
  caption_text text not null check (length(trim(caption_text)) > 0),
  overlay_text text not null check (length(trim(overlay_text)) > 0)
);
create unique index disclosure_templates_key on disclosure_templates (coalesce(category_id, '00000000-0000-0000-0000-000000000000'::uuid), language);
alter table categories add constraint categories_disclosure_fk foreign key (disclosure_template_id) references disclosure_templates(id) on delete set null;

create table settings (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  key text not null,
  value jsonb not null,
  "default" jsonb not null,
  min numeric,
  max numeric,
  allowed jsonb,                         -- for choice settings
  scope text not null check (scope in ('system', 'operator', 'account', 'campaign')),
  scope_id uuid,                         -- null = the base row for the key
  updated_by uuid
);
create unique index settings_key on settings (key, coalesce(scope_id, '00000000-0000-0000-0000-000000000000'::uuid));

create table adapters (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  stage text not null,
  provider text not null,
  plan text,
  config_json jsonb not null default '{}',     -- prices, limits, endpoints. Never keys: keys live in env vars.
  quota_used numeric not null default 0,
  quota_limit numeric,
  cost_month_usd numeric not null default 0,
  cost_month text not null default to_char(now(), 'YYYY-MM'),
  fallback_provider text,
  is_fake boolean not null default false,
  active boolean not null default false,
  paused_reason text,
  unique (stage, provider)
);
create unique index adapters_one_active on adapters (stage) where active;

create table kill_switch (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  singleton boolean not null default true unique check (singleton),
  active boolean not null default false,
  set_by uuid,
  set_at timestamptz,
  reason text
);

-- ---------------------------------------------------------------- campaigns

create table campaigns (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  marketplace_id uuid not null references marketplaces(id),
  external_ref text,
  name text not null,
  source_url text,
  category_id uuid references categories(id),
  status text not null default 'draft'
    check (status in ('draft', 'reading', 'pending_approval', 'approved', 'paused', 'closed', 'rejected')),
  status_reason text,
  deadline timestamptz,
  current_version_id uuid,
  approved_version_id uuid
);

create table campaign_versions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  campaign_id uuid not null references campaigns(id) on delete cascade,
  version_no int not null,
  snapshot_hash text not null,
  raw_text text not null,
  read_at timestamptz not null default now(),
  rules_json jsonb not null default '{}',
  rate_per_1k numeric,
  fee_pct numeric,                        -- null = fee unknown (never invented)
  max_payout_per_clip numeric,
  budget_total numeric,
  budget_remaining numeric,
  min_views_to_qualify numeric,
  payout_model text,
  compliance_status text not null default 'review' check (compliance_status in ('pass', 'review', 'blocked')),
  hard_blocks jsonb not null default '[]',
  unclear_fields jsonb not null default '[]',
  rules_confirmed_by uuid references operators(id),
  rules_confirmed_at timestamptz,
  unique (campaign_id, version_no)
);
alter table campaigns add constraint campaigns_current_version_fk foreign key (current_version_id) references campaign_versions(id);
alter table campaigns add constraint campaigns_approved_version_fk foreign key (approved_version_id) references campaign_versions(id);

create table campaign_rules (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  version_id uuid not null references campaign_versions(id) on delete cascade,
  rule_text_verbatim text not null,
  category text not null,
  requirement text not null,              -- required | forbidden | allowed | unclear
  source_quote text                       -- null means the rule is stored as unclear
);

create table source_assets (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  campaign_id uuid not null references campaigns(id) on delete cascade,
  origin_url text,
  file_hash text not null,
  duration_s numeric,
  language text not null check (language in ('en', 'ar')),
  dialect text,
  licence_ref text,
  permission_ref text,
  stored_path text not null,
  delete_after timestamptz,
  deleted_at timestamptz,
  -- Hard rule 7: only permitted footage. A source needs a licence or a written permission on file.
  constraint source_needs_permission check (licence_ref is not null or permission_ref is not null)
);

create table transcripts (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  source_id uuid not null references source_assets(id) on delete cascade,
  source_hash text not null,
  language text not null,
  dialect text,
  provider text not null,
  model text,
  word_json_path text not null,
  srt_path text,
  wer_estimate numeric,
  unique (source_hash, language, dialect, provider)
);

create table jobs (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  type text not null,
  source_id uuid references source_assets(id) on delete cascade,
  campaign_version_id uuid references campaign_versions(id),
  input_hash text not null,
  idempotency_key text not null unique,   -- type + ':' + input_hash
  payload jsonb not null default '{}',
  status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed', 'cancelled')),
  attempts int not null default 0,
  cost_usd numeric not null default 0,
  error text,
  run_after timestamptz not null default now()
);
create index jobs_claim on jobs (status, run_after, created_at);

create table candidates (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  source_id uuid not null references source_assets(id) on delete cascade,
  provider text not null,
  start_s numeric not null,
  end_s numeric not null check (end_s > start_s),
  score numeric,
  rank int,
  reason text,
  reframed_path text,
  status text not null default 'new' check (status in ('new', 'hooked', 'rendered', 'discarded'))
);

create table accounts (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  -- Hard rule 1: exactly one owner. Not null, and one row per platform handle.
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  platform text not null,
  handle text not null,
  verified boolean not null default false,
  daily_cap int,                          -- null = use the setting
  posting_window jsonb,                   -- {"start":"09:00","end":"22:00"}; null = use the setting
  status text not null default 'active' check (status in ('active', 'warned', 'restricted', 'retired')),
  status_note text,
  unique (platform, handle)
);

create table clips (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  candidate_id uuid not null references candidates(id) on delete cascade,
  campaign_id uuid not null references campaigns(id) on delete cascade,
  campaign_version_id uuid not null references campaign_versions(id),
  parent_clip_id uuid references clips(id),
  version_no int not null default 1,
  start_s numeric not null,
  end_s numeric not null,
  file_path text,
  file_hash text,
  approved_file_hash text,                -- hash of the version the owner accepted
  duration_s numeric,
  width int,
  height int,
  caption_text text,
  hashtags text[] not null default '{}',
  mentions text[] not null default '{}',
  language text not null,
  dialect text,
  overlay_json jsonb,                     -- {"text":..., "start_s":0, "end_s":duration}
  render_meta jsonb not null default '{}',
  compliance_status text not null default 'pending' check (compliance_status in ('pending', 'pass', 'review', 'fail')),
  compliance_report jsonb,
  account_id uuid references accounts(id),
  platform text,
  hook_score numeric,
  park_reason text,
  due_at timestamptz,
  status text not null default 'rendering' check (status in
    ('rendering', 'ready_for_review', 'accepted', 'queued', 'review_later', 'rejected', 'ready_to_post', 'posted', 'archived'))
);
create index clips_status on clips (owner_operator_id, status);

create table hooks (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  candidate_id uuid references candidates(id) on delete cascade,
  clip_id uuid references clips(id) on delete cascade,
  text text not null,
  hook_type text not null check (hook_type in ('question', 'contrarian', 'stakes', 'number', 'story-open')),
  chosen boolean not null default false,
  rank int not null default 1,
  score numeric
);

create table reviews (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  clip_id uuid not null references clips(id) on delete cascade,
  reviewer_id uuid not null references operators(id),
  decision text not null check (decision in ('accept', 'reject', 'review_later', 'undo')),
  reason_code text,
  notes text,
  undo_of uuid references reviews(id),
  session_id uuid,
  client_action_id text unique,           -- offline outbox de-duplication
  -- FR-31: a rejection needs a reason code; "other" needs a note.
  constraint reject_needs_reason check (decision <> 'reject' or reason_code is not null),
  constraint other_needs_note check (reason_code is distinct from 'other' or length(trim(coalesce(notes, ''))) > 0),
  constraint reason_code_known check (reason_code is null or reason_code in (
    'weak_hook', 'cut_mid_thought', 'wrong_context', 'caption_error', 'audio_problem', 'framing_problem',
    'copyright_or_music_risk', 'disclosure_problem', 'outside_campaign_rules', 'duplicate', 'other')),
  constraint undo_needs_target check (decision <> 'undo' or undo_of is not null)
);

create table posts (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  clip_id uuid not null references clips(id),
  account_id uuid not null references accounts(id),
  platform text not null,
  posted_at timestamptz not null default now(),
  post_url text not null check (post_url ~* '^https?://'),
  disclosure_toggle_on boolean not null,
  checklist_json jsonb not null,
  scheduler_ref text,
  confirmed_by uuid not null references operators(id),
  -- The disclosure toggle is part of every post (spec 12.3).
  constraint post_needs_disclosure check (disclosure_toggle_on),
  unique (clip_id, account_id)
);

create table metrics (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  post_id uuid not null references posts(id) on delete cascade,
  checked_at timestamptz not null default now(),
  "window" text not null check ("window" in ('h48', 'd7')),
  views bigint not null check (views >= 0),
  watch_time_s numeric,
  source text not null default 'manual' check (source in ('manual', 'export', 'api', 'screenshot'))
);

create table payouts (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  clip_id uuid not null references clips(id) unique,
  campaign_version_id uuid not null references campaign_versions(id),
  status text not null default 'submitted' check (status in
    ('submitted', 'tracking', 'approved', 'held', 'paid', 'rejected', 'capped', 'budget_exhausted')),
  gross_usd numeric,
  fee_usd numeric,                         -- null = fee unknown
  withdrawal_fee_usd numeric,              -- null = unknown
  net_usd numeric,
  below_min_views boolean not null default false,
  disputed boolean not null default false,
  held_since timestamptz,
  paid_at timestamptz,
  notes text
);

create table time_entries (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  operator_id uuid not null references operators(id),
  task_type text not null check (task_type in ('sourcing', 'cutting', 'review', 'posting', 'admin')),
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  minutes numeric,
  campaign_id uuid references campaigns(id) on delete set null,
  clip_id uuid references clips(id) on delete set null,
  session_id uuid
);

create table sessions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  operator_id uuid not null references operators(id),
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  length_setting numeric not null,
  clips_reviewed int not null default 0,
  accepted int not null default 0,
  rejected int not null default 0,
  parked int not null default 0,
  position_json jsonb                      -- "Stop here": campaign, clip, filters
);

create table alerts (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  type text not null,
  severity text not null check (severity in ('info', 'warn', 'block')),
  entity_type text,
  entity_id uuid,
  message text not null,
  acknowledged_at timestamptz,
  snoozed_until timestamptz,
  sent_channels jsonb not null default '[]'
);
create unique index alerts_open_dedupe on alerts (owner_operator_id, type, coalesce(entity_id, '00000000-0000-0000-0000-000000000000'::uuid))
  where acknowledged_at is null;

create table audit_log (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  actor_id uuid,
  actor_kind text not null default 'system' check (actor_kind in ('operator', 'worker', 'system')),
  action text not null,
  entity_type text not null,
  entity_id uuid,
  rule_version_id uuid,
  before_hash text,
  after_hash text,
  detail jsonb,                            -- small facts only: never tokens, never brief text
  at timestamptz not null default clock_timestamp()
);
create index audit_entity on audit_log (entity_type, entity_id, at);

-- updated_at on every table that can be updated
do $$ declare t text; begin
  for t in select tablename from pg_tables where schemaname = 'public' and tablename <> 'audit_log' loop
    execute format('create trigger touch before update on %I for each row execute function app.touch_updated_at()', t);
  end loop;
end $$;
