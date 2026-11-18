-- Phase D, step 3: per-second signals (2026-11-18). Additive only.
-- One row per source. `heatmap` and `chat_counts` are inputs captured when a YouTube link is fetched (validated, null
-- when the video has none); `series` and `topics` are filled once by the signals job. Null means "absent", never zero.

create table if not exists source_signals (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  source_id uuid not null unique references source_assets(id) on delete cascade,
  heatmap jsonb,                 -- [{start_time, end_time, value}] from yt-dlp "most replayed"
  chat_counts jsonb,             -- [[second, messages]] from the stream's live chat replay
  file_hash text,                -- the file the series were measured on
  transcript_version int,        -- the transcript the text signals and topics were built from
  step_s numeric not null default 1,
  series jsonb,                  -- {"loudness": [...], "speech_rate": [...], ...}; null until computed
  topics jsonb not null default '[]',
  computed_at timestamptz
);

create trigger touch before update on source_signals for each row execute function app.touch_updated_at();
create trigger audit after insert or update or delete on source_signals for each row execute function app.audit_row();

-- Same policies as the other owner tables (003_rls.sql, 009b_transcripts.sql).
alter table source_signals enable row level security;
alter table source_signals force row level security;
grant select, insert, update, delete on source_signals to app_user, app_worker;
create policy p_select on source_signals for select to app_user using (
  app.role() = 'admin' or owner_operator_id = app.operator_id());
create policy p_insert on source_signals for insert to app_user with check (
  app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_update on source_signals for update to app_user
  using (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))
  with check (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_delete on source_signals for delete to app_user using (
  app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_worker on source_signals for all to app_worker using (true) with check (true);
