-- Phase C review fixes (2026-11-17).
-- 1. "Not for me" is each owner's own choice, not a flag on the shared feed row.
-- 2. Briefs are read again after a few days (enriched_at), not on every pass.

alter table listings add column if not exists enriched_at timestamptz;
update listings set enriched_at = updated_at where raw ? 'brief' and enriched_at is null;

create table if not exists listing_choices (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  listing_id uuid not null references listings(id) on delete cascade,
  dismissed_at timestamptz,
  unique (owner_operator_id, listing_id)
);

alter table listing_choices enable row level security;
alter table listing_choices force row level security;
grant select, insert, update, delete on listing_choices to app_user, app_worker;
create policy p_select on listing_choices for select to app_user using (owner_operator_id = app.operator_id());
create policy p_write on listing_choices for all to app_user
  using (app.role() in ('admin', 'operator') and owner_operator_id = app.operator_id())
  with check (app.role() in ('admin', 'operator') and owner_operator_id = app.operator_id());
create policy p_worker on listing_choices for all to app_worker using (true) with check (true);

-- Existing dismissals were made by the only owner so far (the admin).
insert into listing_choices (owner_operator_id, listing_id, dismissed_at)
select (select id from operators where role = 'admin' order by created_at limit 1), id, dismissed_at
from listings where dismissed_at is not null
  and exists (select 1 from operators where role = 'admin')
on conflict do nothing;

comment on column listings.dismissed_at is 'Unused since 007: see listing_choices.';
comment on column listings.campaign_id is 'Unused since 007: campaigns.listing_id links each owner''s own campaign.';
