# Brief extractor: one real run (Azure gpt-5-mini through the adapter, strict quote validation)

| Brief | Must-extract found | Invented | LLM items (returned / accepted) | Conflicts |
|---|---|---|---|---|
| CSGOBIG | 11/11 | 0 | 13 / 4 | 0 |
| VAVADA | 12/12 | 0 | 21 / 7 | 0 |
| Castle [RUNESCAPE + GAMBA] 3 | 7/7 | 0 | 17 / 8 | 0 |
| Social Media Justice | 9/9 | 0 | 25 / 25 | 1 |
| Topps x ClipFarm | 12/13 | 0 | 35 / 33 | 0 |

## CSGOBIG
- Must-extract found: 11/11
- Invented values (quote not in brief): 0
- LLM items returned 13, accepted 4, dropped 9 (no verbatim quote, or number not in its quote)
- Conflicts between rules and LLM: 0
- Items only the LLM found: 0

Per layer: audience 1/1, content 2/2, length 1/1, assets 2/2, account 1/1, payout 3/3, platforms 1/1

Missed:
- none

Reported unclear: payout.model, payout.rate_per_1k, captions.disclosure_policy

## VAVADA
- Must-extract found: 12/12
- Invented values (quote not in brief): 0
- LLM items returned 21, accepted 7, dropped 14 (no verbatim quote, or number not in its quote)
- Conflicts between rules and LLM: 0
- Items only the LLM found: 0

Per layer: audience 2/2, content 2/2, length 1/1, performance 1/1, assets 2/2, payout 4/4

Missed:
- none

Reported unclear: captions.disclosure_policy

## Castle [RUNESCAPE + GAMBA] 3
- Must-extract found: 7/7
- Invented values (quote not in brief): 0
- LLM items returned 17, accepted 8, dropped 9 (no verbatim quote, or number not in its quote)
- Conflicts between rules and LLM: 0
- Items only the LLM found: 0

Per layer: audience 1/1, content 3/3, assets 1/1, payout 2/2

Missed:
- none

Reported unclear: length.min_s, captions.disclosure_policy

## Social Media Justice
- Must-extract found: 9/9
- Invented values (quote not in brief): 0
- LLM items returned 25, accepted 25, dropped 0 (no verbatim quote, or number not in its quote)
- Conflicts between rules and LLM: 1
- Items only the LLM found: 3

Per layer: assets 1/1, captions 1/1, platforms 1/1, audience 1/1, account 1/1, content 1/1, submission 1/1, payout 1/1, enforcement 1/1

Missed:
- none

Conflict audience.min_share: rule {"pct": 50, "countries": ["US", "CA", "AU", "UK", "NZ"], "label": "native Englis vs [{"value": {"pct": 50, "countries": ["USA", "CA", "AU", "UK", "NZ"], "label": "native English-speaking countries"}, "source_quote": "At least 50% of the audienc

Reported unclear: payout.budget_total, audience.language, length.min_s, captions.disclosure_policy

## Topps x ClipFarm
- Must-extract found: 12/13
- Invented values (quote not in brief): 0
- LLM items returned 35, accepted 33, dropped 2 (no verbatim quote, or number not in its quote)
- Conflicts between rules and LLM: 0
- Items only the LLM found: 13

Per layer: captions 1/1, sources 1/2, account 1/1, hook 2/2, edit_style 1/1, deadlines 1/1, priority 1/1, payout 1/1, audience 1/1, platforms 1/1, rejection 1/1

Missed:
- sources: whitelist = the content bank

Reported unclear: payout.budget_total, audience.language, length.min_s, assets.logo
