-- Phase D, step 6: the quality gate and brand checks (2026-11-18). Additive only.
-- A marketplace's reason for rejecting a clip is kept per brand and turned into an extra compliance check for that
-- brand's next clips. A reason too vague to check is kept as a reminder (it never blocks). The review feed orders clips
-- by their chance of clearing the campaign minimum; the cut-off below which a clip sinks is a setting.

create table if not exists brand_checks (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  brand text not null check (length(brand) between 1 and 120),       -- lower-case brand (listing brand, else campaign name)
  kind text not null check (kind in ('banned_phrase', 'require_text', 'reminder')),
  pattern text not null check (length(pattern) between 1 and 200),   -- the phrase or tag to look for; the quote for a reminder
  rule text not null check (length(rule) between 1 and 300),         -- the check in one sentence the owner can read
  quote text not null check (length(quote) between 1 and 500),       -- the marketplace's own words
  severity text not null check (severity in ('block', 'warn', 'reminder')),
  active boolean not null default true,
  clip_id uuid references clips(id) on delete set null,              -- the rejected clip that taught this check
  unique (owner_operator_id, brand, kind, pattern)
);
create index if not exists brand_checks_brand on brand_checks (owner_operator_id, brand) where active;

create trigger touch before update on brand_checks for each row execute function app.touch_updated_at();
create trigger audit after insert or update or delete on brand_checks for each row execute function app.audit_row();

-- Same policies as the other owner tables (003_rls.sql, 012_moments.sql, 013_templates.sql).
alter table brand_checks enable row level security;
alter table brand_checks force row level security;
grant select, insert, update, delete on brand_checks to app_user, app_worker;
create policy p_select on brand_checks for select to app_user using (
  app.role() = 'admin' or owner_operator_id = app.operator_id());
create policy p_insert on brand_checks for insert to app_user with check (
  app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_update on brand_checks for update to app_user
  using (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))
  with check (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_delete on brand_checks for delete to app_user using (
  app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_worker on brand_checks for all to app_worker using (true) with check (true);

-- A clip whose chance of reaching the campaign minimum is below this share sinks to the bottom of Review.
insert into settings (key, value, "default", min, max, allowed, scope) values
  ('quality_min_p_clear', '0.25', '0.25', 0, 0.9, null, 'operator')
on conflict do nothing;
