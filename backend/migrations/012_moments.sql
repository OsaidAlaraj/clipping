-- Phase D, step 4: the moment engine (2026-11-18). Additive only.
-- Profiles (one per content type), hook patterns and the engine router are DATA: new ones need no release.
-- Candidates keep their variants, features, origin and, when the filter set them aside, the reason and layer.
-- Owner decisions on moments become labels (moment_labels) that the learning step reads.

-- ------------------------------------------------------------------ candidates: what the engine knows about a moment
alter table candidates add column if not exists variant text not null default 'tight'
  check (variant in ('hook_first', 'with_setup', 'tight'));
alter table candidates add column if not exists variants jsonb;          -- {"hook_first": {"start_s","end_s"}, "with_setup": {...}, "tight": {...}}
alter table candidates add column if not exists features jsonb;          -- {"hook": 0.7, ...} the auditable subscores
alter table candidates add column if not exists reasons jsonb;           -- ["why this moment", ...]
alter table candidates add column if not exists origin text;             -- the engine (or vendor) that proposed it
alter table candidates add column if not exists engine text;             -- the router engine name (own, topic, hype, pilot, vendor)
alter table candidates add column if not exists profile text;            -- the content profile it was judged under
alter table candidates add column if not exists style jsonb;             -- {"crop","audio","edit"} from the profile, for rendering
alter table candidates add column if not exists hook_text text;
alter table candidates add column if not exists hook_pattern text;
alter table candidates add column if not exists filtered_reason text;
alter table candidates add column if not exists filtered_layer int check (filtered_layer in (1, 2, 3));

alter table candidates drop constraint if exists candidates_status_check;
alter table candidates add constraint candidates_status_check
  check (status in ('new', 'hooked', 'rendered', 'discarded', 'filtered'));

alter table source_assets add column if not exists profile text;          -- the owner's per-video choice of content profile
alter table hooks add column if not exists pattern text;                  -- the library pattern the hook was written in

-- ------------------------------------------------------------------ content profiles (reference data, like categories)
create table if not exists content_profiles (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  name text not null unique,
  niches text[] not null default '{}',   -- campaign niches (category names) that pick this profile
  active boolean not null default true,
  config jsonb not null default '{}'     -- every missing field falls back to the general defaults in code
);
create trigger touch before update on content_profiles for each row execute function app.touch_updated_at();

insert into content_profiles (name, niches, config) values
 ('podcast', '{podcast,interview,talk}', '{
   "label":"Podcast and interview","clips":6,"min_clips":3,"max_clips":12,"full_episode_min":30,"length_min_s":30,"length_max_s":60,
   "weights":{"hook":0.20,"standalone":0.20,"payoff":0.15,"emotion":0.12,"value":0.13,"energy":0.02,"peaks":0.05,"laughter":0.08,"length_fit":0.05},
   "moments":["a standalone opinion","a surprising fact","a story with a payoff","a confession","funny banter"],
   "crop":"follow_speaker","audio":"speech","edit":"dead_air_out","route":["own","topic","vendor"],"needs_speech":true,"info_ok":false}'),
 ('stream', '{gaming,stream,streams,twitch}', '{
   "label":"Streams and gaming","clips":5,"min_clips":3,"max_clips":10,"full_episode_min":30,"length_min_s":20,"length_max_s":45,
   "weights":{"hook":0.10,"standalone":0.05,"payoff":0.10,"emotion":0.15,"value":0.0,"energy":0.15,"peaks":0.25,"laughter":0.10,"length_fit":0.10},
   "moments":["a clutch play","a fail","a loud reaction","a chat explosion"],
   "crop":"facecam_over_game","audio":"natural","edit":"quiet_parts_kept","route":["hype","own","vendor"],"needs_speech":false,"info_ok":false}'),
 ('sports', '{sports,sport,football}', '{
   "label":"Sports","clips":5,"min_clips":3,"max_clips":10,"full_episode_min":30,"length_min_s":15,"length_max_s":40,
   "weights":{"hook":0.05,"standalone":0.05,"payoff":0.10,"emotion":0.15,"value":0.0,"energy":0.20,"peaks":0.30,"laughter":0.0,"length_fit":0.15},
   "moments":["a goal or score","a big play","a crowd roar","a heated argument"],
   "crop":"follow_action","audio":"natural","edit":"quiet_parts_kept","route":["hype","own"],"needs_speech":false,"info_ok":false}'),
 ('film_tv', '{film,movie,tv,series,film_tv}', '{
   "label":"Film and TV","clips":5,"min_clips":3,"max_clips":8,"full_episode_min":30,"length_min_s":20,"length_max_s":60,
   "weights":{"hook":0.15,"standalone":0.15,"payoff":0.20,"emotion":0.20,"value":0.0,"energy":0.12,"peaks":0.08,"laughter":0.05,"length_fit":0.05},
   "moments":["a self-contained scene","a twist or reveal","a memorable line"],
   "crop":"centre","audio":"natural","edit":"quiet_parts_kept","route":["own","topic","vendor"],"needs_speech":false,"info_ok":false}'),
 ('music', '{music,song,concert}', '{
   "label":"Music","clips":4,"min_clips":2,"max_clips":8,"full_episode_min":20,"length_min_s":15,"length_max_s":45,
   "weights":{"hook":0.10,"standalone":0.0,"payoff":0.10,"emotion":0.20,"value":0.0,"energy":0.20,"peaks":0.30,"laughter":0.0,"length_fit":0.10},
   "moments":["the chorus or drop","a big vocal moment","a crowd singalong"],
   "crop":"centre","audio":"natural","edit":"keep_all","route":["hype","own"],"needs_speech":false,"info_ok":false}'),
 ('promo', '{promo,ad,ads,brand,ugc,advert}', '{
   "label":"Brand ads, UGC and promo","clips":4,"min_clips":2,"max_clips":8,"full_episode_min":20,"length_min_s":15,"length_max_s":30,
   "weights":{"hook":0.25,"standalone":0.15,"payoff":0.15,"emotion":0.12,"value":0.18,"energy":0.05,"peaks":0.0,"laughter":0.0,"length_fit":0.10},
   "moments":["the main benefit","a demo","a strong claim","a testimonial: hook, proof, call to action"],
   "crop":"follow_speaker","audio":"speech","edit":"dead_air_out","route":["own","pilot","vendor"],"needs_speech":true,"info_ok":true}'),
 ('lifestyle', '{lifestyle,vlog,behind_the_scenes,reddit_story}', '{
   "label":"Lifestyle, vlog and behind the scenes","clips":5,"min_clips":3,"max_clips":10,"full_episode_min":30,"length_min_s":20,"length_max_s":45,
   "weights":{"hook":0.18,"standalone":0.12,"payoff":0.15,"emotion":0.20,"value":0.05,"energy":0.12,"peaks":0.05,"laughter":0.08,"length_fit":0.05},
   "moments":["a transformation","a reveal","a relatable moment"],
   "crop":"follow_speaker","audio":"natural","edit":"quiet_parts_kept","route":["own","topic","pilot","vendor"],"needs_speech":true,"info_ok":false}'),
 ('general', '{}', '{"label":"General","route":["own","topic"]}')
on conflict (name) do nothing;

-- ------------------------------------------------------------------ hook pattern library (reference data)
create table if not exists hook_patterns (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  name text not null,
  language text not null check (language in ('en', 'ar')),
  profiles text[] not null default '{}',         -- empty = every profile
  description text not null,                     -- what the pattern is (the model is told this)
  template text not null,                        -- an example shape, {x} marks the gist (the fake model fills it)
  hook_type text not null default 'question' check (hook_type in ('question', 'contrarian', 'stakes', 'number', 'story-open')),
  active boolean not null default true,
  unique (name, language)
);
create trigger touch before update on hook_patterns for each row execute function app.touch_updated_at();

insert into hook_patterns (name, language, description, template, hook_type) values
 ('question', 'en', 'A question the clip answers, specific enough that the viewer wants the answer.', 'What if {x}?', 'question'),
 ('shocking_number', 'en', 'Open on a surprising, exact number from the clip.', 'The number that shocks everyone: {x}', 'number'),
 ('nobody_tells_you', 'en', 'Promise something nobody tells people, then deliver it.', 'Nobody tells you this: {x}', 'contrarian'),
 ('bold_opinion', 'en', 'State a clear, bold opinion the clip backs up.', 'Unpopular opinion: {x}', 'contrarian'),
 ('story_opening', 'en', 'Start a story at its most tense moment.', 'It started like this: {x}', 'story-open'),
 ('mistake_confession', 'en', 'Admit a real mistake the clip explains.', 'I got this wrong for years: {x}', 'story-open'),
 ('stakes_warning', 'en', 'Say what the viewer loses by ignoring this.', 'Ignore this and it costs you: {x}', 'stakes'),
 ('question', 'ar', 'سؤال يجيب عنه المقطع، محدد بما يكفي ليريد المشاهد الجواب.', 'ماذا لو {x}؟', 'question'),
 ('shocking_number', 'ar', 'ابدأ برقم مفاجئ ودقيق من المقطع.', 'الرقم الذي يصدم الجميع: {x}', 'number'),
 ('nobody_tells_you', 'ar', 'ابدأ بوعد بشيء لا يقوله أحد ثم قدمه.', 'لا أحد يخبرك بهذا: {x}', 'contrarian'),
 ('bold_opinion', 'ar', 'رأي جريء وواضح يدعمه المقطع.', 'رأي غير شعبي: {x}', 'contrarian'),
 ('story_opening', 'ar', 'ابدأ القصة من لحظتها الأكثر توتراً.', 'بدأ الأمر هكذا: {x}', 'story-open'),
 ('mistake_confession', 'ar', 'اعتراف بخطأ حقيقي يشرحه المقطع.', 'أخطأت في هذا سنوات: {x}', 'story-open'),
 ('stakes_warning', 'ar', 'قل ماذا يخسر المشاهد إن تجاهل هذا.', 'تجاهل هذا وسوف يكلفك: {x}', 'stakes')
on conflict (name, language) do nothing;

-- ------------------------------------------------------------------ owner data: stats, labels, learned weights
create table if not exists engine_stats (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  profile text not null,
  engine text not null,
  tries int not null default 0,
  wins int not null default 0,
  losses int not null default 0,
  unique (owner_operator_id, profile, engine)
);

create table if not exists hook_pattern_stats (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  pattern text not null,
  language text not null,
  profile text not null,
  uses int not null default 0,
  approvals int not null default 0,
  rejections int not null default 0,
  views_high int not null default 0,
  views_low int not null default 0,
  unique (owner_operator_id, pattern, language, profile)
);

create table if not exists moment_labels (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid default app.operator_id(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  candidate_id uuid not null references candidates(id) on delete cascade,
  profile text not null,
  engine text,
  language text,
  hook_pattern text,
  kind text not null check (kind in ('accept', 'reject', 'rescue', 'views_high', 'views_low')),
  features jsonb not null default '{}',   -- the candidate's subscores when it was judged
  unique (candidate_id, kind)
);

create table if not exists profile_weights (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  owner_operator_id uuid not null default app.operator_id() references operators(id),
  profile text not null,
  weights jsonb not null,
  labelled int not null,
  fitted_at timestamptz not null default now(),
  unique (owner_operator_id, profile)
);

create trigger touch before update on engine_stats for each row execute function app.touch_updated_at();
create trigger touch before update on hook_pattern_stats for each row execute function app.touch_updated_at();
create trigger touch before update on moment_labels for each row execute function app.touch_updated_at();
create trigger touch before update on profile_weights for each row execute function app.touch_updated_at();
create trigger audit after insert or update or delete on moment_labels for each row execute function app.audit_row();
create trigger audit after insert or update or delete on profile_weights for each row execute function app.audit_row();

-- ------------------------------------------------------------------ RLS (same policies as 003, 009b and 011)
do $$
declare t text;
begin
  foreach t in array array['engine_stats', 'hook_pattern_stats', 'moment_labels', 'profile_weights'] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('grant select, insert, update, delete on %I to app_user, app_worker', t);
    execute format($f$create policy p_select on %I for select to app_user using (
      app.role() = 'admin' or owner_operator_id = app.operator_id())$f$, t);
    execute format($f$create policy p_insert on %I for insert to app_user with check (
      app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))$f$, t);
    execute format($f$create policy p_update on %I for update to app_user
      using (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))
      with check (app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))$f$, t);
    execute format($f$create policy p_delete on %I for delete to app_user using (
      app.role() in ('admin', 'operator') and (app.role() = 'admin' or owner_operator_id = app.operator_id()))$f$, t);
    execute format('create policy p_worker on %I for all to app_worker using (true) with check (true)', t);
  end loop;

  foreach t in array array['content_profiles', 'hook_patterns'] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('grant select, insert, update, delete on %I to app_user', t);
    execute format('grant select on %I to app_worker', t);
    execute format('create policy p_select on %I for select to app_user, app_worker using (true)', t);
    execute format($f$create policy p_admin_write on %I for all to app_user
      using (app.role() = 'admin') with check (app.role() = 'admin')$f$, t);
  end loop;
end $$;
