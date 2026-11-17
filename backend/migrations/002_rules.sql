-- Guards enforced in the database, so no code path can skip them:
-- append-only audit log, automatic audit rows, human gates, kill switch, ledger order, settings bounds.

-- ---------------------------------------------------------------- audit log (spec 11.3)

create or replace function app.audit_row() returns trigger language plpgsql as $$
declare
  v_old jsonb; v_new jsonb; v_action text; v_kind text;
begin
  if tg_op <> 'INSERT' then v_old := to_jsonb(old); end if;
  if tg_op <> 'DELETE' then v_new := to_jsonb(new); end if;
  if tg_op = 'UPDATE' and (v_old - 'updated_at') = (v_new - 'updated_at') then return new; end if;
  v_action := tg_table_name || '.' || lower(tg_op);
  if tg_op = 'UPDATE' and (v_old ? 'status') and (v_old->>'status') is distinct from (v_new->>'status') then
    v_action := tg_table_name || '.status:' || coalesce(v_old->>'status', '') || '>' || coalesce(v_new->>'status', '');
  end if;
  v_kind := case when current_user = 'app_worker' then 'worker'
                 when app.operator_id() is not null then 'operator' else 'system' end;
  insert into audit_log (actor_id, actor_kind, action, entity_type, entity_id, rule_version_id, before_hash, after_hash)
  values (
    app.operator_id(), v_kind, v_action, tg_table_name,
    coalesce(v_new->>'id', v_old->>'id')::uuid,
    coalesce(v_new->>'campaign_version_id', v_old->>'campaign_version_id',
             v_new->>'approved_version_id', v_new->>'current_version_id')::uuid,
    case when v_old is null then null else encode(sha256(convert_to(v_old::text, 'UTF8')), 'hex') end,
    case when v_new is null then null else encode(sha256(convert_to(v_new::text, 'UTF8')), 'hex') end
  );
  return coalesce(new, old);
end $$;

do $$ declare t text; begin
  for t in select tablename from pg_tables where schemaname = 'public' and tablename <> 'audit_log' loop
    execute format('create trigger audit after insert or update or delete on %I for each row execute function app.audit_row()', t);
  end loop;
end $$;

create or replace function app.audit_immutable() returns trigger language plpgsql as $$
begin raise exception 'audit_log is append-only'; end $$;
create trigger audit_no_change before update or delete on audit_log for each row execute function app.audit_immutable();
create trigger audit_no_truncate before truncate on audit_log for each statement execute function app.audit_immutable();

-- ---------------------------------------------------------------- kill switch (FR-71)

create or replace function app.kill_switch_on() returns boolean language sql stable as $$
  select coalesce((select active from kill_switch limit 1), false)
$$;

create or replace function app.jobs_guard() returns trigger language plpgsql as $$
declare v_status text; v_approved uuid;
begin
  if app.kill_switch_on() then
    raise exception 'kill switch is on: no new jobs';
  end if;
  -- FR-05: only approved campaigns create production jobs, against the approved version.
  if new.campaign_version_id is not null then
    select c.status, c.approved_version_id into v_status, v_approved
      from campaign_versions v join campaigns c on c.id = v.campaign_id where v.id = new.campaign_version_id;
    if v_status is distinct from 'approved' or v_approved is distinct from new.campaign_version_id then
      raise exception 'jobs need an approved campaign version';
    end if;
  end if;
  return new;
end $$;
create trigger jobs_guard before insert on jobs for each row execute function app.jobs_guard();

-- ---------------------------------------------------------------- three human gates (FR-70)

create or replace function app.campaign_gate() returns trigger language plpgsql as $$
declare v record;
begin
  if new.status = 'approved' and old.status is distinct from 'approved' then
    if not app.is_human() then
      raise exception 'gate campaign_approval needs a recorded human action';
    end if;
    select * into v from campaign_versions where id = new.current_version_id;
    if v.id is null then raise exception 'campaign has no version to approve'; end if;
    if v.compliance_status = 'blocked' or jsonb_array_length(v.hard_blocks) > 0 then
      raise exception 'campaign is hard-blocked and cannot be approved';
    end if;
    if v.rules_confirmed_by is null then
      raise exception 'the owner must confirm the extracted rules before approval';
    end if;
    new.approved_version_id := new.current_version_id;
  end if;
  return new;
end $$;
create trigger campaign_gate before update on campaigns for each row execute function app.campaign_gate();

create or replace function app.clip_gate() returns trigger language plpgsql as $$
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
      if not app.is_human() then
        raise exception 'gate publish_approval needs a recorded human action';
      end if;
      if app.kill_switch_on() then
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
create trigger clip_gate before update on clips for each row execute function app.clip_gate();

create or replace function app.post_gate() returns trigger language plpgsql as $$
declare v_owner uuid; v_status text; v_clip_status text;
begin
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
create trigger post_gate before insert on posts for each row execute function app.post_gate();

-- ---------------------------------------------------------------- ledger order (spec 13.1)

create or replace function app.payout_order() returns trigger language plpgsql as $$
declare ok boolean;
begin
  if new.status is distinct from old.status then
    ok := case old.status
      when 'submitted' then new.status in ('tracking', 'rejected', 'capped', 'budget_exhausted')
      when 'tracking'  then new.status in ('approved', 'rejected', 'capped', 'budget_exhausted')
      when 'approved'  then new.status in ('held', 'rejected', 'capped', 'budget_exhausted')
      when 'held'      then new.status in ('paid', 'rejected', 'capped', 'budget_exhausted')
      else false end;   -- paid, rejected, capped, budget_exhausted are terminal
    if not ok then
      raise exception 'payout cannot move from % to %', old.status, new.status;
    end if;
    if new.status = 'held' then new.held_since := coalesce(new.held_since, now()); end if;
    if new.status = 'paid' then new.paid_at := coalesce(new.paid_at, now()); end if;
  end if;
  return new;
end $$;
create trigger payout_order before update on payouts for each row execute function app.payout_order();

create or replace function app.payout_insert() returns trigger language plpgsql as $$
begin
  if new.status <> 'submitted' then
    raise exception 'a payout starts as submitted';
  end if;
  return new;
end $$;
create trigger payout_insert before insert on payouts for each row execute function app.payout_insert();

-- ---------------------------------------------------------------- settings bounds (spec 4.3)

create or replace function app.settings_bounds() returns trigger language plpgsql as $$
declare v numeric;
begin
  if tg_op = 'UPDATE' and (new.key <> old.key or new.scope <> old.scope
      or new.min is distinct from old.min or new.max is distinct from old.max) and not (app.role() is null) then
    raise exception 'setting keys, scopes and bounds are fixed';
  end if;
  if new.min is not null or new.max is not null then
    if jsonb_typeof(new.value) <> 'number' then
      raise exception 'setting % must be a number', new.key;
    end if;
    v := (new.value #>> '{}')::numeric;
    if (new.min is not null and v < new.min) or (new.max is not null and v > new.max) then
      raise exception 'setting % = % is outside % to %', new.key, v, new.min, new.max;
    end if;
  end if;
  if new.allowed is not null and not (new.allowed @> jsonb_build_array(new.value)) then
    raise exception 'setting % must be one of %', new.key, new.allowed;
  end if;
  new.updated_by := coalesce(app.operator_id(), new.updated_by);
  return new;
end $$;
create trigger settings_bounds before insert or update on settings for each row execute function app.settings_bounds();

-- ---------------------------------------------------------------- undo must reference a real decision on the same clip

create or replace function app.review_guard() returns trigger language plpgsql as $$
declare v record;
begin
  if not (app.operator_id() is not null and current_user <> 'app_worker') then
    raise exception 'reviews are human decisions';
  end if;
  new.reviewer_id := app.operator_id();
  if new.decision = 'undo' then
    select * into v from reviews where id = new.undo_of;
    if v.id is null or v.clip_id <> new.clip_id or v.decision = 'undo' then
      raise exception 'undo must reference the original decision on this clip';
    end if;
  end if;
  return new;
end $$;
create trigger review_guard before insert on reviews for each row execute function app.review_guard();
