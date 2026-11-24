-- Real progress of a running job: what it is doing, how much of the real work is done and a time left measured from its own speed.
alter table jobs add column if not exists progress jsonb;
