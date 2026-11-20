# Active-speaker bake-off: rubric (fixed 2026-11-20, BEFORE any candidate was run)

Question: which method puts the **person who is speaking** inside the 9:16 crop, steadily, at a cost this laptop (GTX 1660 Ti 6 GB) can pay?

## Candidates
1. **baseline** = current `faces.py` (YuNet, largest/most confident face, smoothing).
2. **diar** = YuNet face tracks + Deepgram utterance speaker labels mapped to face tracks (no new model).
3. **LR-ASD**, 4. **FabuLight-ASD**, 5. **ASDnB** (audio-visual ASD nets, run on YuNet face tracks, pretrained weights as published).

## Test set (frozen before running)
- The 15 segments of the earlier reframe bake-off (30 s each: 5 gaming, 5 interview, 5 Arabic lecture; `docs/bakeoff/reframe/metrics.json`, `_meta.t0`).
- Plus at least 3 **listener-from-behind** podcast clips and at least 2 **multi-person** clips (two or more faces in frame), picked by looking at contact sheets of the long sources, 30 s each. Pick criteria are visual only; no candidate output is looked at while picking.

## Ground truth
Per clip, at 1 sample per second, I label by eye on full-size frames, with the transcript text at that second: the **speaker's box centre x** (0..1 of the width), or "none" (no visible speaker: slides, voice-over). Where the speaker is off-screen (listener-from-behind shot with the speaker not visible), label "off". A frame with the speaker visible only from behind counts as visible. Labels are stored in `labels.json` before scoring. Clips without two or more people carry labels too, so "do no harm" is measured.

## Metrics (all computed from each method's crop-centre path, crop width = 9/16 of the height, at 1 Hz)
1. **Speaker-in-crop rate** (primary): share of labelled seconds where the speaker centre lies inside the crop and at least 10 % of the crop width from its edge. Reported overall, on multi-person clips, and on listener-from-behind clips separately.
2. **Listener failure rate**: share of seconds in listener-from-behind clips where the crop centre is nearer a non-speaker face than the speaker.
3. **Crop jitter**: mean absolute second difference of the crop centre (px of a 1080-wide frame) and number of direction reversals per minute; and **move count** against the number of true speaker changes.
4. **Speed**: seconds of processing per 30 s clip on GPU (and CPU for YuNet/diar), peak VRAM.
5. **Install size** (new packages + weights, MB) and **licence** of code and weights/training data.

## Adoption rule
A candidate is adopted only if its speaker-in-crop rate on multi-person plus listener clips beats the baseline by **at least 10 points** and by more than the bootstrap interval over clips, **and** it does not lose more than 2 points on the single-person clips, and licence is fine for private use (no non-commercial restriction on the weights if avoidable; GPL ok), and speed fits (<= about 3x real time on this GPU is fine for batch rendering). If nothing clears that bar, the best cheap fix is shipped instead and the report says so.
