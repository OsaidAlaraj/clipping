-- Real providers set up on 2026-11-17 (owner's Azure for Students subscription and Deepgram account).
-- Azure OpenAI: resource clipping-ai-7k3 (France Central; the student policy allows Austria East, Belgium Central,
-- France Central, Poland Central and Spain Central). The student quota only covers light models: gpt-5-mini is
-- deployed (Global Standard, 100K tokens/min). gpt-5-mini list price: $0.25 per 1M input, $2 per 1M output tokens;
-- the per-call estimate uses a blended $0.0011 per 1K tokens so the monthly cap is never under-counted.
update adapters
   set config_json = config_json
       || '{"price_per_1k_tokens_usd":0.0011,"api_version":"2024-10-21","deployment":"gpt-5-mini","region":"francecentral","note":"Azure for Students; quota only for light models (gpt-5-mini). Ask for gpt-5.2 quota later if needed."}'::jsonb,
       plan = 'Azure for Students credit'
 where stage = 'llm' and provider = 'azure_openai';

-- Deepgram Nova-3 (pay as you go, $200 free credit on sign-up). Price kept at the conservative multilingual rate.
update adapters
   set config_json = config_json || '{"note":"Deepgram account created 2026-11-17 with $200 free credit"}'::jsonb
 where stage = 'transcription' and provider = 'deepgram';
