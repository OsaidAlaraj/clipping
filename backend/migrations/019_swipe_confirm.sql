-- Decision 31: a swipe right in the review feed is the owner's confirm to post. The swipe stands in for the owner ticks
-- caption, hashtags and account, and it never exposes the disclosure toggle. This is the ONE adjustment to a human gate:
-- the post_targets guard also accepts a checklist that has NO disclosure_toggle_on tick, and only when ALL of these hold:
--   * the confirm is marked confirm_via = 'swipe' (set by the API from the owner's own authenticated session);
--   * the three swipe ticks are true and every server check is true;
--   * the clip's compliance is not a fail (a blocked clip never posts from a swipe);
--   * when the brief requires disclosure, the caption starts with it and the overlay is present for the whole clip
--     (both deterministic compliance checks pass), so the disclosure is on.
-- Every other path (the posting screen, any caller without the marker) still needs the full checklist, exactly as before.
-- One hardening: app.checklist_complete returned NULL (not false) when a tick key was missing, and `if not NULL` does not raise,
-- so a checklist without the key slipped past the trigger. It now returns false in that case. A complete checklist is unaffected.
-- Practice campaigns, the kill switch, the human check, the owner's own active account and the allowed platform are unchanged.

create or replace function app.checklist_complete(j jsonb) returns boolean language sql immutable as $$
  select coalesce(
        j is not null
     and jsonb_typeof(j->'ticks') = 'object' and jsonb_typeof(j->'server') = 'object'
     and (j#>>'{ticks,account_and_platform_correct}') = 'true' and (j#>>'{ticks,disclosure_toggle_on}') = 'true'
     and (j#>>'{ticks,caption_pasted}') = 'true' and (j#>>'{ticks,hashtags_and_tags_checked}') = 'true'
     and exists (select 1 from jsonb_each(j->'server'))
     and not exists (select 1 from jsonb_each(j->'server') e where e.value <> 'true'::jsonb), false)
$$;

create or replace function app.swipe_checklist_complete(j jsonb, v_compliance text, v_report jsonb, v_rules jsonb) returns boolean
language sql immutable as $$
  select coalesce(j is not null
     and j->>'confirm_via' = 'swipe'
     and jsonb_typeof(j->'ticks') = 'object' and jsonb_typeof(j->'server') = 'object'
     and (j#>>'{ticks,account_and_platform_correct}') = 'true'
     and (j#>>'{ticks,caption_pasted}') = 'true' and (j#>>'{ticks,hashtags_and_tags_checked}') = 'true'
     and exists (select 1 from jsonb_each(j->'server'))
     and not exists (select 1 from jsonb_each(j->'server') e where e.value <> 'true'::jsonb)
     and v_compliance in ('pass', 'review')
     and (
       coalesce((v_rules#>>'{disclosure,required_by_brief}')::boolean, false) = false
       or (
         exists (select 1 from jsonb_array_elements(coalesce(v_report->'deterministic', '[]'::jsonb)) c
                 where c->>'check' = 'caption_starts_with_disclosure' and c->>'status' = 'pass')
         and exists (select 1 from jsonb_array_elements(coalesce(v_report->'deterministic', '[]'::jsonb)) c
                     where c->>'check' = 'overlay_present_full_duration' and c->>'status' = 'pass')
       )
     ), false)
$$;

create or replace function app.post_targets_guard() returns trigger language plpgsql as $$
declare v_clip record; v_account record; v_campaign record; v_rules jsonb;
begin
  if not app.is_human() then
    raise exception 'post targets are created by the owner only';
  end if;
  if app.kill_switch_on() then
    raise exception 'kill switch is on: nothing can be queued to post';
  end if;
  select * into v_clip from clips where id = new.clip_id;
  select is_practice into v_campaign from campaigns where id = v_clip.campaign_id;
  if coalesce(v_campaign.is_practice, false) then
    raise exception 'practice campaigns cannot be posted, scheduled or paid';
  end if;
  if v_clip.status not in ('ready_to_post', 'queued', 'posted') then
    raise exception 'clip is not approved for publishing';
  end if;
  select owner_operator_id, status, platform into v_account from accounts where id = new.account_id;
  if v_account.owner_operator_id is distinct from app.operator_id() then
    raise exception 'an account can only be used by its owner';
  end if;
  if v_account.status <> 'active' then
    raise exception 'account is % and excluded from posting', v_account.status;
  end if;
  if v_account.platform is distinct from new.platform then
    raise exception 'the account is not a % account', new.platform;
  end if;
  select rules_json into v_rules from campaign_versions where id = v_clip.campaign_version_id;
  if not (coalesce(v_rules->'platforms_allowed', '[]'::jsonb) @> to_jsonb(new.platform)) then
    raise exception 'the campaign does not allow % posts', new.platform;
  end if;
  if not (app.checklist_complete(new.checklist_json)
          or coalesce(app.swipe_checklist_complete(new.checklist_json, v_clip.compliance_status, v_clip.compliance_report, v_rules), false)) then
    raise exception 'the publish checklist is not complete';
  end if;
  if new.state <> 'scheduled' then
    raise exception 'a post target starts as scheduled';
  end if;
  if new.connection_id is not null and not exists (
       select 1 from platform_connections where id = new.connection_id and account_id = new.account_id and platform = new.platform) then
    raise exception 'that connection does not belong to this account';
  end if;
  new.owner_operator_id := app.operator_id();
  new.confirmed_by := app.operator_id();
  return new;
end $$;
