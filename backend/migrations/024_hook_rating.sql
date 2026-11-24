-- The owner's thumbs on each hook in the hook editor: 1 = up, -1 = down. The first rating also teaches the hook library
-- (hook_pattern_stats: an up counts as an approval, a down as a rejection for the hook's format).
alter table hooks add column if not exists rating smallint check (rating in (-1, 1));
