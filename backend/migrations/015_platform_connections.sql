-- Phase E, phase 1: connected platform accounts and OAuth sign-in state.
-- Tokens exist only as AES-GCM ciphertext (secret_ct); the key lives in .env (TOKEN_VAULT_KEY), never here.

create table platform_connections (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  account_id uuid not null unique references accounts(id) on delete cascade,
  platform text not null check (platform in ('youtube_shorts', 'instagram_reels', 'tiktok')),
  external_account_id text not null,
  handle text,
  state text not null default 'connected' check (state in ('connected', 'needs_reconnect')),
  scopes jsonb not null default '[]',
  secret_ct bytea not null,                 -- nonce + AES-GCM(JSON {access, refresh}), bound to this row's id
  key_id text not null,                     -- which vault key sealed it (a hash prefix, not the key)
  expires_at timestamptz,                   -- access token expiry
  refresh_expires_at timestamptz,           -- when the refresh path itself runs out (null = unknown)
  last_refreshed_at timestamptz,
  last_error text,
  unique (platform, external_account_id)
);

-- One sign-in attempt. The state is stored hashed and used once; the PKCE verifier is sealed like a token.
create table oauth_states (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  state_hash text not null unique,
  platform text not null,
  account_id uuid references accounts(id) on delete cascade,
  verifier_ct bytea not null,
  key_id text not null,
  expires_at timestamptz not null,
  used_at timestamptz
);

do $$
declare t text;
begin
  foreach t in array array['platform_connections', 'oauth_states'] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('grant select, insert, update, delete on %I to app_user, app_worker', t);
    -- Owner-only: not even an admin reads another owner's connection.
    execute format($f$create policy p_select on %I for select to app_user using (owner_operator_id = app.operator_id())$f$, t);
    execute format($f$create policy p_write on %I for all to app_user
      using (app.role() in ('admin', 'operator') and owner_operator_id = app.operator_id())
      with check (app.role() in ('admin', 'operator') and owner_operator_id = app.operator_id())$f$, t);
    execute format('create policy p_worker on %I for all to app_worker using (true) with check (true)', t);
    execute format('create trigger touch before update on %I for each row execute function app.touch_updated_at()', t);
  end loop;
end $$;
create trigger audit after insert or update or delete on platform_connections for each row execute function app.audit_row();

-- Hard rule 1: an account belongs to one owner. Answers only yes/no, so it leaks nothing about the other owner.
create or replace function app.identity_taken(p_platform text, p_external text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from platform_connections
                 where platform = p_platform and external_account_id = p_external
                   and owner_operator_id is distinct from app.operator_id())
$$;
grant execute on function app.identity_taken(text, text) to app_user, app_worker;

-- Sign-in providers: the real platform endpoints (untested live) and the fake used until developer apps exist.
-- Neither is 'active': connections.py picks one per call (fake while CLIPPING_USE_FAKES is on).
insert into adapters (stage, provider, plan, config_json, fallback_provider, is_fake, active) values
  ('oauth', 'platforms', 'official OAuth endpoints (Google, Instagram Login, TikTok v2)',
   '{"note":"sign-in credentials come from environment variables (see .env.example); nothing stored here"}', null, false, false),
  ('oauth', 'fake', null, '{}', null, true, false)
on conflict do nothing;
