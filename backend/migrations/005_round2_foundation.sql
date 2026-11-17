-- Round 2 foundation (decisions 2026-11-17): accounts per niche, passkey sign-in, source fetch from links.

-- One account per platform per niche. Null niche = usable for any niche.
alter table accounts add column niche text;

-- Passkeys (WebAuthn). Public keys only: nothing secret is stored.
create table auth_credentials (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  operator_id uuid not null references operators(id) on delete cascade,
  credential_id text not null unique,          -- base64url
  public_key text not null,                    -- base64url COSE key
  sign_count bigint not null default 0,
  rp_id text not null,
  device_name text,
  last_used_at timestamptz
);

-- Signed-in sessions. Only a SHA-256 of the cookie value is stored.
create table auth_sessions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  operator_id uuid not null references operators(id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  user_agent text
);

-- One-time codes that allow adding a passkey (first device, or a new device while signed in),
-- plus the pending WebAuthn challenges. Hashes only.
create table auth_challenges (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid,
  kind text not null check (kind in ('setup_code', 'register', 'login')),
  value_hash text not null,
  operator_id uuid references operators(id) on delete cascade,
  rp_id text,
  expires_at timestamptz not null,
  used_at timestamptz
);
create index auth_challenges_lookup on auth_challenges (kind, value_hash);

do $$ declare t text; begin
  foreach t in array array['auth_credentials', 'auth_sessions', 'auth_challenges'] loop
    execute format('create trigger touch before update on %I for each row execute function app.touch_updated_at()', t);
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    -- Only the backend's system connection reads these. An admin may list credentials and sessions (to revoke).
    execute format('grant select on %I to app_user', t);
    execute format($f$create policy p_admin_read on %I for select to app_user using (app.role() = 'admin')$f$, t);
  end loop;
end $$;
-- Audit sign-in changes, without secrets (the audit trigger stores hashes of the row, never the row).
create trigger audit after insert or update or delete on auth_credentials for each row execute function app.audit_row();
create trigger audit after insert or update or delete on auth_sessions for each row execute function app.audit_row();
