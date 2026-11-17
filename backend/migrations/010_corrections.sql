-- Phase D, step 2: the correction screen (2026-11-17). Additive only.
-- Running word-error counts per transcript: how many words the owner's corrected text differs from the provider's
-- first reading (version 1), and how many words the corrected text has. save_version keeps them current, and the
-- accuracy report adds them up per provider and dialect. No new table, so the existing transcripts policies cover it.

alter table transcripts add column if not exists wer_errors int not null default 0 check (wer_errors >= 0);
alter table transcripts add column if not exists wer_words int not null default 0 check (wer_words >= 0);
