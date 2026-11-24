# Brief extractor: one real run (Azure gpt-5-mini through the adapter, strict quote validation)

| Brief | Must-extract found | Invented | LLM items (returned / accepted) | Conflicts |
|---|---|---|---|---|
| Topps x ClipFarm | 13/13 | 0 | 48 / 40 | 0 |

## Topps x ClipFarm
- Must-extract found: 13/13
- Invented values (quote not in brief): 0
- LLM items returned 48, accepted 40, dropped 8 (no verbatim quote, or number not in its quote)
- Conflicts between rules and LLM: 0
- Items only the LLM found: 27

Per layer: captions 1/1, sources 2/2, account 1/1, hook 2/2, edit_style 1/1, deadlines 1/1, priority 1/1, payout 1/1, audience 1/1, platforms 1/1, rejection 1/1

Missed:
- none

Reported unclear: payout.budget_total, audience.language, length.min_s, assets.logo
