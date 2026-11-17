-- Phase D, step 1: richer transcripts (2026-11-17). Additive only.
-- Transcript versions (the correction screen saves a new one per edit), speakers, the language code that was
-- actually sent, per-campaign key terms, and the local Whisper provider as Deepgram's declared fallback.
-- Existing transcripts keep their word files: each becomes version 1 and the files stay readable as they are.

alter table transcripts add column if not exists current_version int not null default 1;
alter table transcripts add column if not exists language_code text;
alter table campaigns add column if not exists key_terms text[] not null default '{}';

create table if not exists transcript_versions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  transcript_id uuid not null references transcripts(id) on delete cascade,
  version_no int not null check (version_no >= 1),
  word_json_path text not null,
  srt_path text,
  speakers jsonb not null default '{}',     -- {speaker id: display name}
  utterances jsonb not null default '[]',   -- derived from the words when empty (rows from before this migration)
  word_count int not null default 0,
  edited_by uuid,
  unique (transcript_id, version_no)
);

insert into transcript_versions (transcript_id, owner_operator_id, version_no, word_json_path, srt_path)
select id, owner_operator_id, 1, word_json_path, srt_path from transcripts
on conflict (transcript_id, version_no) do nothing;

create trigger touch before update on transcript_versions for each row execute function app.touch_updated_at();
create trigger audit after insert or update or delete on transcript_versions for each row execute function app.audit_row();

-- Same policies as the other owner tables (003_rls.sql).
alter table transcript_versions enable row level security;
alter table transcript_versions force row level security;
grant select, insert, update, delete on transcript_versions to app_user, app_worker;
create policy p_select on transcript_versions for select to app_user using (
  app.role() = 'admin' or owner_operator_id = app.operator_id());
create policy p_insert on transcript_versions for insert to app_user with check (
  app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_update on transcript_versions for update to app_user
  using (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))
  with check (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_delete on transcript_versions for delete to app_user using (
  app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()));
create policy p_worker on transcript_versions for all to app_worker using (true) with check (true);

-- Providers. Deepgram falls back to the local Whisper; the first Deepgram call stays switchable as deepgram_basic.
insert into adapters (stage, provider, plan, config_json, fallback_provider, is_fake, active) values
  ('transcription', 'faster_whisper', 'local GPU, free',
   '{"model":"large-v3-turbo","compute_type":"int8","device":"auto","max_keyterms":20,"note":"CUDA int8 on the GTX 1660 Ti, CPU int8 when CUDA is missing; models under .data/cache"}',
   'manual', false, false),
  ('transcription', 'deepgram_basic', 'pay-as-you-go',
   '{"model":"nova-3","price_per_min_usd":0.0052,"note":"the original call: language only, no speakers or key terms"}',
   'faster_whisper', false, false)
on conflict (stage, provider) do nothing;

update adapters
   set fallback_provider = 'faster_whisper',
       config_json = config_json || '{"max_keyterms":50}'::jsonb
 where stage = 'transcription' and provider = 'deepgram';
