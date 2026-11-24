-- How long the hook stays on screen, in seconds. 0 = automatic (3 to 5 s from the word count).
insert into settings (key, value, "default", min, max, allowed, scope) values
  ('hook_show_s', '0', '0', 0, 10, null, 'operator')
on conflict do nothing;
