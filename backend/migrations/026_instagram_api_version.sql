-- Instagram API version: v21.0 is past Meta's support window; v27.0 is the current one in Meta's docs. Still owner-editable per row.
update adapters set config_json = jsonb_set(config_json, '{instagram_api_version}', '"v27.0"')
 where stage = 'publishing' and config_json ? 'instagram_api_version';
