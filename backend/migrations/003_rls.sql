-- Row-level security on every table, by owner and role (spec 4.1, 15.4).
-- app_user  = a signed-in operator (identity in app.operator_id / app.role).
-- app_worker = the Python worker. It can run the pipeline but cannot pass a human gate (002_rules.sql).

do $$
declare
  t text;
  owned text[] := array['campaigns', 'campaign_versions', 'campaign_rules', 'source_assets', 'transcripts', 'jobs',
    'candidates', 'clips', 'hooks', 'reviews', 'accounts', 'posts', 'metrics', 'payouts', 'time_entries',
    'sessions', 'alerts'];
  reviewer_reads text[] := array['campaigns', 'campaign_versions', 'campaign_rules', 'clips', 'hooks', 'reviews'];
  reference text[] := array['marketplaces', 'categories', 'disclosure_templates', 'settings', 'adapters', 'kill_switch'];
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
  end loop;

  foreach t in array owned loop
    execute format('grant select, insert, update, delete on %I to app_user, app_worker', t);
    execute format($f$create policy p_select on %I for select to app_user using (
      app.role() = 'admin' or owner_operator_id = app.operator_id() or (app.role() = 'reviewer' and %L))$f$,
      t, t = any (reviewer_reads));
    execute format($f$create policy p_insert on %I for insert to app_user with check (
      app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))$f$, t);
    execute format($f$create policy p_update on %I for update to app_user
      using (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))
      with check (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))$f$, t);
    execute format($f$create policy p_delete on %I for delete to app_user using (
      app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))$f$, t);
    execute format('create policy p_worker on %I for all to app_worker using (true) with check (true)', t);
  end loop;

  -- A reviewer may record review decisions, as themselves.
  create policy p_reviewer_insert on reviews for insert to app_user
    with check (app.role() = 'reviewer' and reviewer_id = app.operator_id());

  foreach t in array reference loop
    execute format('grant select, insert, update, delete on %I to app_user', t);
    execute format('grant select on %I to app_worker', t);
    execute format('create policy p_select on %I for select to app_user, app_worker using (true)', t);
    execute format($f$create policy p_admin_write on %I for all to app_user
      using (app.role() = 'admin') with check (app.role() = 'admin')$f$, t);
  end loop;
end $$;

-- The worker records adapter quota and cost, and nothing else on reference tables.
grant update (quota_used, cost_month_usd, cost_month, paused_reason, active) on adapters to app_worker;
create policy p_worker_update on adapters for update to app_worker using (true) with check (true);
-- Operator-scoped settings can be changed by the operator they belong to.
create policy p_own_setting on settings for all to app_user
  using (scope = 'operator' and scope_id = app.operator_id() and app.role() in ('operator', 'admin'))
  with check (scope = 'operator' and scope_id = app.operator_id() and app.role() in ('operator', 'admin'));

-- operators: see yourself; admins see and manage everyone.
grant select, insert, update on operators to app_user;
grant select on operators to app_worker;
create policy p_select on operators for select to app_user using (app.role() = 'admin' or id = app.operator_id());
create policy p_worker on operators for select to app_worker using (true);
create policy p_admin_write on operators for all to app_user using (app.role() = 'admin') with check (app.role() = 'admin');

-- audit_log: insert and read only. No UPDATE, DELETE or TRUNCATE grant exists for any app role.
revoke all on audit_log from app_user, app_worker;
grant select, insert on audit_log to app_user, app_worker;
create policy p_insert on audit_log for insert to app_user, app_worker with check (true);
create policy p_select on audit_log for select to app_user using (app.role() = 'admin' or actor_id = app.operator_id());
create policy p_worker_select on audit_log for select to app_worker using (true);

grant execute on all functions in schema app to app_user, app_worker;

-- Usage accounting for adapters runs for any actor (an operator's intake call also uses the LLM adapter).
create or replace function app.adapter_usage(p_id uuid, p_units numeric, p_cost numeric) returns setof adapters
language sql security definer set search_path = public as $$
  update adapters set quota_used = quota_used + p_units, cost_month_usd = cost_month_usd + p_cost
  where id = p_id returning *
$$;
create or replace function app.adapter_pause(p_id uuid, p_reason text) returns void
language sql security definer set search_path = public as $$
  update adapters set paused_reason = p_reason where id = p_id
$$;
create or replace function app.adapter_roll_month(p_id uuid, p_month text) returns setof adapters
language sql security definer set search_path = public as $$
  update adapters set cost_month = p_month, cost_month_usd = 0, paused_reason = null where id = p_id returning *
$$;
grant execute on function app.adapter_usage(uuid, numeric, numeric), app.adapter_pause(uuid, text), app.adapter_roll_month(uuid, text) to app_user, app_worker;
