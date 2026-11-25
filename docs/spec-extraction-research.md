# Spec extraction research (step 0, short)

Question: how do mature tools turn messy text into typed data, and what do we reuse instead of inventing?

## Options and verdict

| Need | Mature option | Verdict |
|---|---|---|
| HTML clipboard to Markdown with links | **turndown** (MIT, v7.2.5 checked on npm, the standard JS converter; `markdownify` is its Python cousin, `node-html-markdown` is faster but less used) | **Adopted** in `web/lib/richPaste.ts`. Custom rules only for what real clipboards need (below). |
| Strip page chrome (readability) | Mozilla Readability | Not needed for a paste: the owner selects the brief, the clipboard already holds only that part. Used only if the link layer fetches a whole page: server returns text for Google Docs `/mobilebasic` and Notion public pages, which are already clean. |
| LLM to typed JSON | OpenAI/Azure **structured outputs** (strict `json_schema`), Instructor and Pydantic-style validate-and-retry | The project already has one path: `registry.call(..., "llm", "complete_json", ...)` and a verbatim-quote validator in `agents/rules.py`. The idea we adopt from Instructor is "validate the model output against a schema and keep only what passes"; we do it with our own small validator, so no new dependency and the adapter registry keeps the cost cap, retries, fallback and audit row. |
| Rules plus LLM hybrid | Common production pattern: regex/grammar pass for high-precision facts, LLM for the long tail, disagreement flagged for a human | **Adopted**: deterministic pass first, LLM second, a disagreement becomes `conflict`. |
| Prompt injection from the pasted text | Treat input as data, never as instructions; spotlight/delimit untrusted text; validate output against a closed schema | Same stance as the existing `LLM_SYSTEM` prompts (text is JSON data); plus every LLM item must carry a quote that exists in the brief, so injected text cannot add a rule that is not quoted. |

## What real clipboards look like (why custom rules exist)

- **Google Docs**: everything is wrapped in `<b style="font-weight:normal" id="docs-internal-guid-...">` (so the tag `b` does NOT mean bold) and real bold is `<span style="font-weight:700">`. Links are Google redirects `google.com/url?q=REAL&sa=D`. Rule: read the style, unwrap the redirect.
- **Notion**: clean `h1-h3`, `ul/li`, `a href` pointing to notion.so or the real target; plus a leading `<meta charset>`.
- **Discord**: lines are `div`s or `br`; links are plain `a href`. Rule: a block `div` is a line.
- **Marketplace pages** (Clipster, Content Rewards): `article/p/b/a` plus tracking parameters (`utm_*`, `fbclid`). Rule: strip those parameters; `mailto:` and `#anchor` links keep their words, lose the link.
- Dropped everywhere: `script, style, meta, link, noscript, svg, iframe, button, input` and every attribute not `href`.

## Design decisions that follow

1. One flat item model `{layer, key, value, source_quote, confidence, status}` (not a deep nested schema): it is easy for the LLM to fill, easy to validate (the quote must exist in the brief), easy to edit one by one in the UI, and lets rule and LLM items be compared key by key.
2. `unclear` is the default. A missing key is reported `unclear` with no value; a value without a verbatim quote is demoted to `unclear` (same rule as `agents/rules.py`).
3. Links are classified by host and path (no fetching needed for most); only public pages, depth 1, through `sources.check_public_https`; Drive and Frame.io are classified, never downloaded; a page that needs a sign-in becomes `needs_paste`.
4. Measured on the five real briefs in `docs/briefs/real-briefs.md` (recall per layer, zero invented values) with the fake LLM in tests and once with the real adapter: `docs/bakeoff/brief-extract/`.
