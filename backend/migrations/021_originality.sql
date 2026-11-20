-- Originality layer: one commentary template per account (intro line per language, style note). A clip's own commentary and
-- the per-platform packaging live in clips.render_meta ("originality", "caption_lab", "packaging"), so they travel with the clip.
alter table accounts add column commentary_template jsonb;
