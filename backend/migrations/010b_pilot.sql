-- Phase D, step 2P: the pilot slice (2026-11-17). Additive only.
-- Practice campaigns let the owner try the whole pipeline with no money and no posting. They are real campaigns
-- approved through the normal owner gate; this file only adds a flag, a "Practice" marketplace row (campaigns need
-- one) and a guard that stops a post, a payout or a ready-to-post clip from ever existing for one.
-- No new table, so the existing campaigns policies cover the new column.

alter table campaigns add column if not exists is_practice boolean not null default false;

insert into marketplaces (name, slug, platforms, source_adapter, notes)
values ('Practice', 'practice', '{}', 'none', 'Owner-created practice campaigns. Nothing is posted and no money moves.')
on conflict (slug) do nothing;

create or replace function app.practice_guard() returns trigger language plpgsql as $$
declare v_practice boolean;
begin
  if tg_table_name = 'clips' then
    if new.status is not distinct from old.status or new.status not in ('ready_to_post', 'queued', 'posted') then
      return new;
    end if;
    select is_practice into v_practice from campaigns where id = new.campaign_id;
  else
    select c.is_practice into v_practice from clips cl join campaigns c on c.id = cl.campaign_id where cl.id = new.clip_id;
  end if;
  if coalesce(v_practice, false) then
    raise exception 'practice campaigns cannot be posted, scheduled or paid';
  end if;
  return new;
end $$;

create trigger practice_guard before update on clips for each row execute function app.practice_guard();
create trigger practice_guard before insert on posts for each row execute function app.practice_guard();
create trigger practice_guard before insert on payouts for each row execute function app.practice_guard();
