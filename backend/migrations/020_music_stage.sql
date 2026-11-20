-- Decision 32: the music bed stage. `catalog` picks a licence-checked free track from the local catalog (no network, no cost);
-- `fake` is the test provider with its own synthetic tracks. A clip with no usable track simply gets no bed.
insert into adapters (stage, provider, plan, config_json, fallback_provider, is_fake, active) values
  ('music', 'catalog', null, '{"note":"free CC BY 4.0 catalog, tracks under .data/music"}', null, false, true),
  ('music', 'fake', null, '{}', null, true, false);
