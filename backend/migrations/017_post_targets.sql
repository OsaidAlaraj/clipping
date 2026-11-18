-- Phase E, phase 3: post targets (one per clip, platform and account), post records, and the guards around them.
-- One owner tap creates the targets; the worker sends each exactly once. Applied migrations are not edited: the older
-- gate functions (post_gate, clip_gate) are replaced here with the same rules plus the confirmed-target path.

insert into settings (key, value, "default", min, max, allowed, scope) values
  ('post_stagger_min', '20', '20', 10, 240, null, 'operator')     -- minutes between one tap's posts (hard rule 4: no blasts)
on conflict do nothing;

-- ---------------------------------------------------------------- post_targets

create table post_targets (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  clip_id uuid not null references clips(id),
  platform text not null check (platform in ('youtube_shorts', 'instagram_reels', 'tiktok')),
  account_id uuid not null references accounts(id),
  connection_id uuid references platform_connections(id) on delete set null,
  manual boolean not null default false,          -- the owner posts by hand and pastes the link; the worker never sends it
  scheduled_at timestamptz not null,
  state text not null default 'scheduled' check (state in
    ('scheduled', 'sending', 'processing', 'published', 'draft', 'private', 'needs_check', 'failed', 'cancelled')),
  idempotency_key text not null unique,           -- hash of clip + platform + the request id: a replayed tap adds nothing
  request_id text not null,
  attempts int not null default 0,
  platform_post_id text,
  url text,
  reason text,
  privacy text check (privacy in ('private', 'unlisted', 'public')),
  sent_at timestamptz,                            -- when the upload started (becomes posts.submitted_at)
  checked_at timestamptz,                         -- single-flight claim for the status job
  checklist_json jsonb not null,                  -- the owner's ticks and the server checks as they stood at the tap
  confirmed_by uuid not null references operators(id),
  constraint published_has_link check (state <> 'published' or coalesce(url ~* '^https?://', false)),
  constraint manual_has_no_connection check (not manual or connection_id is null)
);
-- One live target per clip and platform: never the same clip on two accounts of one platform.
create unique index post_targets_one_per_platform on post_targets (clip_id, platform) where state not in ('cancelled', 'failed');
create index post_targets_request on post_targets (clip_id, request_id);
create index post_targets_open on post_targets (state) where state in ('scheduled', 'sending', 'processing');

alter table posts
  add column platform_post_id text,
  add column state text not null default 'published' check (state in ('published', 'removed')),
  add column privacy text check (privacy in ('private', 'unlisted', 'public')),
  add column submitted_at timestamptz not null default now(),
  add column target_id uuid references post_targets(id);
create unique index posts_one_per_target on posts (target_id) where target_id is not null;
create unique index posts_platform_post on posts (platform, platform_post_id) where platform_post_id is not null;

alter table post_targets enable row level security;
alter table post_targets force row level security;
grant select, insert, update on post_targets to app_user, app_worker;
create policy p_select on post_targets for select to app_user using (app.role() = 'admin' or owner_operator_id = app.operator_id());
create policy p_insert on post_targets for insert to app_user
  with check (app.role() in ('admin', 'operator') and owner_operator_id = app.operator_id());
create policy p_update on post_targets for update to app_user
  using (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))
  with check (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_worker on post_targets for all to app_worker using (true) with check (true);
create trigger touch before update on post_targets for each row execute function app.touch_updated_at();
create trigger audit after insert or update or delete on post_targets for each row execute function app.audit_row();

-- ---------------------------------------------------------------- guards

-- Server checks all true and every owner tick ticked (mirrors publish.OWNER_TICKS).
create or replace function app.checklist_complete(j jsonb) returns boolean language sql immutable as $$
  select j is not null
     and jsonb_typeof(j->'ticks') = 'object' and jsonb_typeof(j->'server') = 'object'
     and (j#>>'{ticks,account_and_platform_correct}') = 'true' and (j#>>'{ticks,disclosure_toggle_on}') = 'true'
     and (j#>>'{ticks,caption_pasted}') = 'true' and (j#>>'{ticks,hashtags_and_tags_checked}') = 'true'
     and exists (select 1 from jsonb_each(j->'server'))
     and not exists (select 1 from jsonb_each(j->'server') e where e.value <> 'true'::jsonb)
$$;

-- Creating a target is gate 3 for that platform: a human, switch off, a real campaign, the owner's own active account
-- on a platform the campaign allows, and a complete checklist. The worker can send targets but never create one.
create or replace function app.post_targets_guard() returns trigger language plpgsql as $$
declare v_clip record; v_account record; v_campaign record; v_rules jsonb;
begin
  if not app.is_human() then
    raise exception 'post targets are created by the owner only';
  end if;
  if app.kill_switch_on() then
    raise exception 'kill switch is on: nothing can be queued to post';
  end if;
  select * into v_clip from clips where id = new.clip_id;
  select is_practice into v_campaign from campaigns where id = v_clip.campaign_id;
  if coalesce(v_campaign.is_practice, false) then
    raise exception 'practice campaigns cannot be posted, scheduled or paid';
  end if;
  if v_clip.status not in ('ready_to_post', 'queued', 'posted') then
    raise exception 'clip is not approved for publishing';
  end if;
  select owner_operator_id, status, platform into v_account from accounts where id = new.account_id;
  if v_account.owner_operator_id is distinct from app.operator_id() then
    raise exception 'an account can only be used by its owner';
  end if;
  if v_account.status <> 'active' then
    raise exception 'account is % and excluded from posting', v_account.status;
  end if;
  if v_account.platform is distinct from new.platform then
    raise exception 'the account is not a % account', new.platform;
  end if;
  select rules_json into v_rules from campaign_versions where id = v_clip.campaign_version_id;
  if not (coalesce(v_rules->'platforms_allowed', '[]'::jsonb) @> to_jsonb(new.platform)) then
    raise exception 'the campaign does not allow % posts', new.platform;
  end if;
  if not app.checklist_complete(new.checklist_json) then
    raise exception 'the publish checklist is not complete';
  end if;
  if new.state <> 'scheduled' then
    raise exception 'a post target starts as scheduled';
  end if;
  if new.connection_id is not null and not exists (
       select 1 from platform_connections where id = new.connection_id and account_id = new.account_id and platform = new.platform) then
    raise exception 'that connection does not belong to this account';
  end if;
  new.owner_operator_id := app.operator_id();
  new.confirmed_by := app.operator_id();
  return new;
end $$;
create trigger post_targets_guard before insert on post_targets for each row execute function app.post_targets_guard();

-- Legal moves. The worker sends and reports; only the owner cancels or pastes a link for something not yet public.
create or replace function app.post_targets_state() returns trigger language plpgsql as $$
declare ok boolean; human boolean := app.is_human();
begin
  if new.clip_id <> old.clip_id or new.platform <> old.platform or new.account_id <> old.account_id
     or new.idempotency_key <> old.idempotency_key or new.request_id <> old.request_id or new.manual <> old.manual
     or new.owner_operator_id <> old.owner_operator_id or new.confirmed_by <> old.confirmed_by
     or new.checklist_json is distinct from old.checklist_json
     or (new.connection_id is distinct from old.connection_id and new.connection_id is not null) then
    raise exception 'a post target cannot be pointed somewhere else';
  end if;
  if new.scheduled_at is distinct from old.scheduled_at and old.state <> 'scheduled' then
    raise exception 'only an unsent post target can be rescheduled';
  end if;
  if new.state is not distinct from old.state then
    return new;
  end if;
  ok := case old.state
    when 'scheduled'   then new.state in ('cancelled') or (not human and new.state = 'sending') or (human and new.state = 'published')
    when 'sending'     then not human and new.state in ('processing', 'published', 'draft', 'private', 'needs_check', 'failed')
    when 'processing'  then new.state in ('published') or (not human and new.state in ('draft', 'private', 'needs_check', 'failed'))
    when 'draft'       then new.state = 'published'
    when 'private'     then new.state = 'published'
    when 'needs_check' then human and new.state in ('published', 'cancelled')
    else false end;      -- published, failed, cancelled are final
  if not ok then
    raise exception 'a post target cannot move from % to %', old.state, new.state;
  end if;
  if new.state = 'sending' and app.kill_switch_on() then
    raise exception 'kill switch is on: nothing can be sent';
  end if;
  return new;
end $$;
create trigger post_targets_state before update on post_targets for each row execute function app.post_targets_state();

-- Turning the kill switch on cancels every target that has not started sending.
create or replace function app.kill_switch_stops_targets() returns trigger language plpgsql security definer set search_path = public as $$
begin
  update post_targets set state = 'cancelled', reason = 'The kill switch was turned on before this was sent.' where state = 'scheduled';
  return new;
end $$;
create trigger kill_switch_stops_targets after update on kill_switch for each row
  when (new.active and not old.active) execute function app.kill_switch_stops_targets();

-- Gate 3 for a post row. Same rules as before for the owner's own manual record. A row with a target is also allowed for the
-- worker (an API publish that already happened is a fact to record, even if the switch went on meanwhile), but only for a
-- target the owner confirmed that is published, on the same clip, account and platform.
create or replace function app.post_gate() returns trigger language plpgsql as $$
declare v_owner uuid; v_status text; v_clip_status text; t record;
begin
  if new.target_id is not null then
    select * into t from post_targets where id = new.target_id;
    if t.id is null or t.state <> 'published' or t.confirmed_by is null or t.clip_id <> new.clip_id
       or t.account_id <> new.account_id or t.platform <> new.platform or t.owner_operator_id <> new.owner_operator_id then
      raise exception 'a post record needs a confirmed, published post target';
    end if;
    new.confirmed_by := t.confirmed_by;
    return new;
  end if;
  if not app.is_human() then
    raise exception 'posts are written by the owner only';
  end if;
  select owner_operator_id, status into v_owner, v_status from accounts where id = new.account_id;
  -- Hard rule 1: an account is used only by its one owner.
  if v_owner is distinct from app.operator_id() then
    raise exception 'an account can only be used by its owner';
  end if;
  if v_status in ('restricted', 'retired') then
    raise exception 'account is % and excluded from posting', v_status;
  end if;
  select status into v_clip_status from clips where id = new.clip_id;
  if v_clip_status not in ('ready_to_post', 'queued') then
    raise exception 'clip is not approved for publishing';
  end if;
  if app.kill_switch_on() then
    raise exception 'kill switch is on';
  end if;
  new.confirmed_by := app.operator_id();
  return new;
end $$;

-- clip_gate: unchanged except that a clip with a published, confirmed target may be marked posted by the worker.
create or replace function app.clip_gate() returns trigger language plpgsql as $$
declare v_auto boolean;
begin
  if new.status is distinct from old.status then
    -- Hard compliance fails never reach review (FR-33).
    if new.status in ('ready_for_review', 'accepted', 'queued', 'ready_to_post', 'posted') and new.compliance_status = 'fail' then
      raise exception 'a clip that failed compliance cannot move to %', new.status;
    end if;
    if new.status = 'ready_for_review' and new.compliance_status = 'pending' then
      raise exception 'a clip needs a compliance report before review';
    end if;
    if new.status in ('accepted', 'rejected', 'review_later') and not app.is_human() then
      raise exception 'gate clip_acceptance needs a recorded human action';
    end if;
    if new.status = 'accepted' and old.status not in ('ready_for_review', 'review_later', 'ready_to_post', 'queued', 'rejected') then
      raise exception 'clip cannot be accepted from %', old.status;
    end if;
    if new.status in ('ready_to_post', 'queued', 'posted') then
      v_auto := new.status = 'posted' and exists (
        select 1 from post_targets where clip_id = new.id and state = 'published' and confirmed_by is not null);
      if not app.is_human() and not v_auto then
        raise exception 'gate publish_approval needs a recorded human action';
      end if;
      if app.kill_switch_on() and not v_auto then
        raise exception 'kill switch is on: no ready packages';
      end if;
    end if;
    if new.status = 'ready_to_post' and old.status not in ('accepted', 'queued') then
      raise exception 'publish approval needs an accepted clip';
    end if;
    if new.status = 'queued' and old.status <> 'ready_to_post' then
      raise exception 'only a publish-approved clip can be handed to the scheduler';
    end if;
    if new.status = 'posted' and old.status not in ('ready_to_post', 'queued') then
      raise exception 'only a publish-approved clip can be marked posted';
    end if;
  end if;
  return new;
end $$;
