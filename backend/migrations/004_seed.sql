-- Reference data from the spec. Figures marked "verify" in the spec stay marked here.
-- Unknown fees are stored as null ("fee unknown"). Nothing is invented.

insert into kill_switch (active) values (false);

-- ---------------------------------------------------------------- settings (spec 4.3; bounds are proposals)
insert into settings (key, value, "default", min, max, allowed, scope) values
  ('hours_cap_per_day',               '5',  '5',  1, 8, null, 'operator'),
  ('session_length_min',              '25', '25', 10, 60, null, 'operator'),
  ('undo_window_s',                   '10', '10', 5, 30, null, 'operator'),
  ('pay_per_clip_display',            '"summary"', '"summary"', null, null, '["card","summary","off"]', 'operator'),
  ('posts_per_operator_per_day',      '6',  '6',  1, 8, null, 'operator'),
  ('posts_per_account_per_day',       '2',  '2',  1, 3, null, 'account'),
  ('posting_window',                  '{"start":"09:00","end":"22:00"}', '{"start":"09:00","end":"22:00"}', null, null, null, 'account'),
  ('clips_per_campaign_per_week',     '10', '10', 1, 30, null, 'campaign'),
  ('budget_alert_threshold_pct',      '20', '20', 5, 50, null, 'system'),
  ('health_alert_drop_pct',           '60', '60', 30, 80, null, 'system'),
  ('source_retention_days',           '30', '30', 7, 90, null, 'system'),
  ('clip_retention_days_after_close', '90', '90', 30, 365, null, 'system'),
  ('priority_weight_net_pay',         '5',  '5',  0, 10, null, 'operator'),
  ('priority_weight_deadline',        '4',  '4',  0, 10, null, 'operator'),
  ('priority_weight_hook_score',      '3',  '3',  0, 10, null, 'operator'),
  ('priority_weight_budget',          '2',  '2',  0, 10, null, 'operator'),
  ('priority_weight_account_cap',     '2',  '2',  0, 10, null, 'operator'),
  ('priority_weight_compliance',      '3',  '3',  0, 10, null, 'operator'),
  ('adapter_cap_usd_llm',             '10', '10', 0, 100, null, 'system'),
  ('adapter_cap_usd_transcription',   '10', '10', 0, 100, null, 'system'),
  ('adapter_cap_usd_moments',         '20', '20', 0, 100, null, 'system'),
  ('adapter_cap_usd_database',        '25', '25', 0, 100, null, 'system'),
  ('adapter_alert_pct',               '80', '80', 50, 100, null, 'system'),
  ('max_accounts_per_campaign_platform', '1', '1', 1, 2, null, 'system'),
  ('clip_min_duration_s',             '5',  '5',  1, 60, null, 'system'),
  ('clip_max_duration_s',             '90', '90', 10, 180, null, 'system');

-- ---------------------------------------------------------------- marketplaces (spec section 9)
insert into marketplaces (name, slug, base_url, pay_model, fee_rules, withdrawal_methods, hold_rules, platforms, source_adapter, notes) values
  ('Content Rewards', 'content_rewards', 'https://contentrewards.com', 'cpm, pool, per_post, retainer',
   '{"creator_fee":[{"models":["cpm","pool"],"pct":10},{"models":["per_post","retainer"],"budget_lt":5000,"pct":10},{"models":["per_post","retainer"],"budget_gte":5000,"pct":0}],"legacy_pct_possible":7,"source":"spec 9.1"}',
   '[{"method":"standard","min_usd":1,"timing":"up to 5 business days","fee_usd":null},{"method":"instant","fee_usd":null,"note":"carries a fee"},{"method":"ach","fee_usd":null,"note":"unknown until open item O6 is answered"}]',
   '{"earning_window_days":7,"hold_days":3,"verify":true}',
   '{tiktok,instagram_reels,youtube_shorts}', 'scraper', 'Scraper targets the public Discover page. Off until terms are confirmed (O9).'),
  ('Vyro', 'vyro', null, 'cpm',
   '{"creator_fee":null,"note":"fee not published (O5)"}',
   '[{"method":"stripe","min_usd":10,"timing":"weekly","fee_usd":null},{"method":"paypal","min_usd":10,"timing":"weekly","fee_usd":null}]',
   '{"pending_until_campaign_end":true}',
   '{tiktok,instagram_reels,youtube_shorts,x}', 'scraper', 'Off until terms are confirmed (O9).'),
  ('Clipping.net', 'clipping_net', null, 'cpm',
   '{"creator_fee":null,"note":"fee not published (O5)"}',
   '[{"method":"paypal","fee_usd":null,"note":"site labels PayPal no fees (verify)"},{"method":"usdc","fee_usd":null},{"method":"usdt_ethereum","fee_usd":null}]',
   '{"paid_when_cycle_closes":true}',
   '{tiktok,instagram_reels,youtube_shorts,x}', 'scraper', 'Per-campaign minimum view thresholds. Off until terms are confirmed (O9).'),
  ('Elev8or Bounties', 'elev8or', 'https://www.elev8or.io/bounty', 'cpm',
   '{"creator_fee":null,"note":"fee not found (verify)"}',
   '[{"method":"wallet","min_usd":null,"note":"one source says $5 minimum (verify)","fee_usd":null}]',
   '{}',
   '{tiktok,instagram_reels,youtube_shorts}', 'scraper', 'X stays off until open item O4 is answered. Off until terms are confirmed (O9).'),
  ('ClipFlip', 'clipflip', 'https://clipflip.io', null,
   '{"creator_fee":null,"note":"not verified"}', '[]', '{}',
   '{instagram_reels,tiktok,youtube_shorts}', 'paste', 'Manual paste only until verified.'),
  ('Direct programme', 'direct', null, 'per agreement',
   '{"creator_fee":null,"note":"per agreement"}', '[]', '{}',
   '{tiktok,instagram_reels,youtube_shorts}', 'none', 'Manual intake with a signed brief.');

-- ---------------------------------------------------------------- categories and disclosure templates (D11)
insert into categories (name, caption_language, music_policy) values
  ('podcast', 'en', 'campaign_audio_only'),
  ('gaming', 'en', 'campaign_audio_only'),
  ('lifestyle', 'en', 'campaign_audio_only'),
  ('reddit_story', 'en', 'campaign_audio_only');

insert into disclosure_templates (category_id, language, caption_text, overlay_text) values
  (null, 'en', '#ad', '#ad'),
  (null, 'ar', '#إعلان', '#إعلان');

-- ---------------------------------------------------------------- adapters (spec section 10)
-- Prices and limits are configuration. Keys are never stored here.
insert into adapters (stage, provider, plan, config_json, quota_limit, fallback_provider, is_fake, active) values
  ('source', 'paste', null, '{}', null, null, false, true),
  ('source', 'scraper', null, '{"min_seconds_between_requests":10}', null, 'paste', false, false),
  ('source', 'fake', null, '{}', null, 'paste', true, false),

  ('llm', 'azure_openai', 'Azure for Students credit', '{"price_per_1k_tokens_usd":null,"note":"set from your Azure price sheet"}', null, 'human', false, false),
  ('llm', 'fake', null, '{}', null, 'human', true, true),

  ('transcription', 'deepgram', 'pay-as-you-go', '{"model":"nova-3","price_per_min_usd":0.0052,"note":"upper figure from spec 16; verify"}', null, 'manual', false, false),
  ('transcription', 'fake', null, '{}', null, 'manual', true, true),

  ('moments', 'vizard', 'Creator', '{"plan_usd_per_month":14.5,"minutes_per_credit":1,"note":"spec 10.4; rate limits unknown (O7)"}', null, 'manual', false, false),
  ('moments', 'klap', 'pay per clip', '{"usd_per_input_video":0.44,"usd_per_short":0.32,"usd_per_export":0.48,"note":"spec 10.4; verify"}', null, 'manual', false, false),
  ('moments', 'opusclip', 'Business (quote only)', '{"note":"switch on only after a Business quote (O10)","quoted":false}', null, 'manual', false, false),
  ('moments', 'fake', null, '{}', null, 'manual', true, true),

  ('render', 'ffmpeg', null, '{"width":1080,"height":1920,"preset":"veryfast","crf":23}', null, 'centre_crop', false, true),
  ('captions', 'ffmpeg', null, '{"font":"Arial"}', null, 'plain_text', false, true),

  ('scheduler', 'manual', null, '{}', null, null, false, true),
  ('scheduler', 'buffer', 'free', '{"channels_limit":3,"queued_per_channel_limit":10,"paid_partnership_toggle":false,"note":"limits from spec 10.6; verify on buffer.com. Toggle assumed missing (O3)"}', 3, 'manual', false, false),
  ('scheduler', 'fake', null, '{"channels_limit":3,"queued_per_channel_limit":10,"paid_partnership_toggle":false}', 3, 'manual', true, false),

  ('storage', 'local', null, '{}', null, null, false, true),
  ('storage', 'r2', 'free tier', '{"note":"limits to verify on cloudflare.com"}', null, 'local', false, false),

  ('database', 'postgres', 'local or Supabase free', '{"keepalive_hours":24}', null, null, false, true),

  ('alerts', 'inapp', null, '{}', null, null, false, true),
  ('alerts', 'telegram', null, '{}', null, 'inapp', false, false),
  ('alerts', 'fake', null, '{}', null, 'inapp', true, false),

  ('metrics', 'manual', null, '{}', null, null, false, true),
  ('payouts', 'manual', null, '{}', null, null, false, true);
