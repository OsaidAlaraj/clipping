-- Review feed: a clip the owner swiped past without deciding comes back after about five other clips, at most twice,
-- and the third skip moves it to Review Later. The state lives on the clip so a reload keeps the order.
alter table clips add column skip_count int not null default 0;
alter table clips add column skip_anchor uuid;          -- the clip it was placed after when it was skipped
alter table clips add column skipped_at timestamptz;
