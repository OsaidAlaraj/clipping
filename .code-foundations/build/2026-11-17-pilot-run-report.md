# Pilot run report (Phase 2P, DW-2P.3)

Run on this laptop on 2026-11-18 with the real providers, through the system's own code: practice campaign created with `campaigns.create_practice` (owner actor, real approval gate), footage registered with `register_source`, the whole pipeline run by the worker loop. Throwaway database and storage under `L:\Projects\Clipping\.data\pilot-run` (the dev data in `.data/pg` was not touched). Script: `.data/pilot-run/run_pilot.py`. Keys were read from `.env` by the system's own code and never printed.

**Scope change (owner-approved):** only short material was run: the full Arabic interview (8 min) and the first 10 minutes of the English podcast (cut with FFmpeg into `.data/tmp/en_cordkillers_first10min.mp4`). The 49-minute Arabic lecture (`ar_kau_physiology_l38.mp4`) and the full 59-minute podcast are **deferred to the phase 4 engine tests**. This means the chunked path of the moment picker (several LLM calls with overlap) was proven only by unit tests with synthetic 40 to 130 minute transcripts, not by a real long run.

## Active providers (as `ops.configure_adapters` sets them with the dev `.env`, CLIPPING_USE_FAKES=0)
transcription = **deepgram** (nova-3), fallback **faster_whisper** (local GPU); llm = **azure_openai** (gpt-5-mini); render = ffmpeg; captions = ffmpeg; storage = local. The `moments` adapter row stays `fake` (no Vizard key), but practice campaigns never use it: they use the LLM picker.

## Results

| | Arabic interview | English podcast, first 10 min |
|---|---|---|
| Video length | 485.8 s | 600.0 s |
| Transcription (hash check + audio + Deepgram + store) | 81.3 s | 40.8 s |
| Transcript | deepgram nova-3, language_code `ar` | deepgram nova-3 |
| Moments (LLM picker, one call) | 29.6 s | 20.2 s |
| Hooks (3 LLM calls) | 25.2 s | 25.9 s |
| Render (3 clips) | 31.1 s | 30.7 s |
| Captions + disclosure + cover (3 clips) | 31.2 s | 36.2 s |
| Compliance | 10.7 s | 10.9 s |
| **Whole run, upload to ready for review** | **209 s** | **166 s** |
| Clips in the review feed | 3 | 3 |
| Compliance | 3 pass | 3 pass |
| Deepgram minutes | 8.1 | 10.0 |
| Deepgram cost (price from adapter config, $0.0052/min) | $0.042 | $0.052 |
| LLM cost (estimate: moments + hooks + compliance) | $0.0125 | $0.016 |
| Alerts raised | none | none |
| Jobs failed or retried | none (all attempts = 1) | none |

Total for both videos: 18.1 Deepgram minutes ($0.094) and about $0.029 of LLM spend (the adapter's blended per-call estimate, which deliberately over-counts reasoning tokens; the real Azure bill will be at or below it).

Each clip has: a 9:16 render, burned captions (Arabic and English), the burned disclosure label (`#إعلان` / `#ad`, top left as the renderer places it today; the Arabic label's top-right position is a phase 4/5 item), a caption text starting with the disclosure, a hook, the reason the moment was picked (shown in the feed as "Why this moment"), and a JPEG cover. Frames checked by eye: English captions and `#ad` visible; Arabic `#إعلان` visible.

### Arabic interview (Egyptian Arabic), clips in `.data/pilot-run/storage/clips/<id>/`
| Clip | Cut | Hook (chosen) | Reason from the picker |
|---|---|---|---|
| `0cac3f42-d2c1-4d21-95b1-0009e332ee12` | 16.5 to 53.0 s (36.5 s) | قال ان هحطلك ورا الشمس ومش هتشوف الشارع مره ثانيه | بداية شخصية قوية («أنا دخلت سجني من 83») تشد الانتباه، تروي تهديدًا وإجراءات تظلم وتنتهي بخلاصة عن إهمال الملف الطبي |
| `f777bc09-b293-4f1f-b9ef-36a1c1e4eeec` | 278.4 to 330.0 s (51.7 s) | هل القاضي كان يكتفي بالنظر ولا يحقق في شكاوى التعذيب؟ | سرد تاريخي واضح عن مرحلة كان فيها القاضي يشاهد أثر التعذيب بنفسه... |
| `71131bc2-c345-4997-99bf-a561e225180d` | 171.5 to 217.2 s (45.7 s) | هل الحكومة هي الراعي الرسمي للتعذيب في مصر؟ | يكشف عن دور الحكومة في توفير أدوات التعذيب... خاتمة توضح المسؤولية الرسمية |

### English podcast (first 10 minutes)
| Clip | Cut | Hook (chosen) | Reason from the picker |
|---|---|---|---|
| `22a94577-24ae-4eb4-83c0-b1830aa2a096` | 441.7 to 500.4 s (58.6 s) | Netflix counts a view as just two minutes — should we trust those numbers? | A provocative metric reveal ('two minutes = a view') followed by strong analysis. |
| `74d08511-1c43-4d53-b0d7-877c018be211` | 317.0 to 355.5 s (38.5 s) | Can Netflix legally adapt every Roald Dahl story now? | Big industry news about a major acquisition that naturally hooks viewers. |
| `9481ed90-1076-429c-b8a3-3bfe387dffcb` | 5.9 to 40.3 s (34.5 s) | Could this accident be a cover-up after two of Harold's wives died? | A gripping true-crime setup that raises suspicion and resolves with a punchy line. |

Rendered files (open them directly): `L:\Projects\Clipping\.data\pilot-run\storage\clips\<id>\final.mp4` and `cover.jpg` for each id above. Raw results: `.data/pilot-run/results-*.json`.

All clips fall inside the 30 to 60 s podcast range, start and end on sentence edges, and carry their reasons.

## What broke and how it was fixed
- **Nothing broke in the real run.** No job failed or retried, no alert was raised, Deepgram answered first time, so the local Whisper fallback was not used.
- Found while building (all fixed, with tests): the first unit tests used a niche that is not a category (test error); the pilot cover failed when the clip's storage folder did not exist yet (`_make_cover` now creates it); a late answer from the Home request could drop a just-created practice campaign from the upload form's list (the list is now merged, not replaced).
- Owner request handled: a cached transcript made by the `fake` provider is now discarded and re-transcribed whenever a real provider is active (`transcript.fake_discarded` audit row; test added).

## Notes for the owner
- The live dev app must be restarted once to apply migration `010b_pilot.sql` (it applies on start). I did not stop or restart it.
- The 8-minute video took about 3.5 minutes to go from upload to reviewable clips; transcription is the largest share (hash, audio extraction and Deepgram).
