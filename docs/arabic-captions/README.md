# Arabic captions: what was wrong, measured (2026-11-25)

Test footage (internal only, deleted after): a 75 s slice of an Egyptian interview (`.data/samples`) and a 75 s slice of a Gulf/Jordanian-host podcast. Engines: Deepgram Nova-3 (`ar`, `ar-JO`, `ar-SA`, `ar-EG`), faster-whisper large-v3-turbo.

| Item | Finding | Result |
|---|---|---|
| Word order (root cause) | Every caption style's `Encoding` was 1, which libass reads as a left-to-right paragraph. Any colour tag inside the line (the karaoke highlight, dim, box) cut the line into segments laid out left to right: a 3-word line came out as words 2-3-1, 3-2-1 or 3-1-2. RLM/RLE marks did not help; `Encoding -1` (auto direction) does. | 0 of 40 real lines in the right order before, 40 of 40 after. Dim-ahead, word-box and pop styles checked by eye (`styles-before-after.png`). |
| Lone punctuation | Deepgram returns "،" as a word of its own (3 of 131 words on the Egyptian clip): it took a place in the 3-word line and started the line up to 0.4 s early. | Now joins the word before; caption starts on the real word. |
| Cut-off article | "ال" then "خبراء" arrived as two words and showed with a gap. | Joined ("الخبراء"). Bare "ال" in captions: 2 before, 0 after. |
| Dialect label | "Jordanian"/"Levantine" typed in words fell back to plain `ar`. | Maps to `ar-JO` (also Palestinian, Lebanese, Syrian, Egyptian). On both clips `ar`, `ar-JO`, `ar-SA` gave identical Deepgram text, so no accuracy gain was measured. |
| Word timing | Deepgram word spans agree with speech activity within 30 ms (best global shift +30 ms) on the clean clip. Not a cause. Whisper showed no stable shift. | No change. |
| Line fit | Longest line 18 characters, none wrap at the used size. | No change. |

Not fixed / still wrong: the Egyptian clip's text is poor in both engines (names such as "سيف الإسلام" misheard, dialect words dropped), which no rendering change repairs. Whisper drops commas and had one wrong word ("العمل" for "العميل") where Deepgram was right; Deepgram stays the better engine on the sample. No hand-checked word-error rate was computed: there was no human reference transcript for either clip. Digits and Latin words inside Arabic lines were not re-measured after the merge.
