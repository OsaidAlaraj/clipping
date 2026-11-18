-- Phase D, step 5: the creative library (2026-11-18). Additive only.
-- A template is the look of an account's clips (caption font and colours, title card, progress bar, zooms, cover style).
-- Templates are DATA: an account has any number of them, each change is a new immutable version, and the render rotates
-- through the account's templates (least recently used first), so a new or edited template is used by the next clip and no
-- release is needed. A revert is a new version that copies an older one. The template that was just added, edited or
-- reverted is `fresh`: it goes first.

alter table clips add column if not exists cover_path text;     -- storage key of the cover PNG

create table if not exists templates (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  account_id uuid not null references accounts(id) on delete cascade,
  name text not null check (length(name) between 1 and 60),
  active boolean not null default true,
  current_version int not null default 1 check (current_version >= 1),
  fresh boolean not null default false, -- just added, edited or reverted: the next clip uses it (one per account at a time)
  last_used_at timestamptz,            -- the rotation is least recently used first (never used goes before used)
  unique (account_id, name)
);
create index if not exists templates_account on templates (account_id, created_at);

create table if not exists template_versions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  template_id uuid not null references templates(id) on delete cascade,
  version_no int not null check (version_no >= 1),
  spec jsonb not null,                 -- {"font","primary","accent","caption_position","title_card","progress_bar","zooms","font_size","cover"}
  note text,
  unique (template_id, version_no)
);

create or replace function app.template_version_frozen() returns trigger language plpgsql as $$
begin
  if new.spec is distinct from old.spec or new.version_no <> old.version_no or new.template_id <> old.template_id then
    raise exception 'A template version never changes. Save a new version instead.';
  end if;
  return new;
end $$;
create trigger frozen before update on template_versions for each row execute function app.template_version_frozen();

create trigger touch before update on templates for each row execute function app.touch_updated_at();
create trigger touch before update on template_versions for each row execute function app.touch_updated_at();
create trigger audit after insert or update or delete on templates for each row execute function app.audit_row();
create trigger audit after insert or update or delete on template_versions for each row execute function app.audit_row();

-- Same policies as the other owner tables (003_rls.sql, 009b_transcripts.sql).
alter table templates enable row level security;
alter table templates force row level security;
grant select, insert, update, delete on templates to app_user, app_worker;
create policy p_select on templates for select to app_user using (
  app.role() = 'admin' or owner_operator_id = app.operator_id());
create policy p_insert on templates for insert to app_user with check (
  app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_update on templates for update to app_user
  using (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))
  with check (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_delete on templates for delete to app_user using (
  app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_worker on templates for all to app_worker using (true) with check (true);

alter table template_versions enable row level security;
alter table template_versions force row level security;
grant select, insert, update, delete on template_versions to app_user, app_worker;
create policy p_select on template_versions for select to app_user using (
  app.role() = 'admin' or owner_operator_id = app.operator_id());
create policy p_insert on template_versions for insert to app_user with check (
  app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_update on template_versions for update to app_user
  using (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))
  with check (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_delete on template_versions for delete to app_user using (
  app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_worker on template_versions for all to app_worker using (true) with check (true);
