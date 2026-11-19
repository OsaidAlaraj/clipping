# Quality gate v1 on the four before/after clips

Measured on the rendered MP4s with the real code (`backend/clipping/quality_gate.py`): face in frame (YuNet, about 2 frames a second, face box fully inside the 9:16 picture) and loudness (ebur128). Speech density, sentence end, word start, language and disclosure need the transcript and caption, which these test renders did not have (no word captions, transcripts not kept), so they are not scored here; they are covered by unit tests.

| Clip | Face in frame | LUFS | Verdict | Reasons |
|---|---|---|---|---|
| interview_en_old_skin | 0% | -14.6 | fail | The speaker's face is in frame only 0% of the time. |
| interview_en_new_yunet | 49% | -14.6 | fail | The speaker's face is in frame only 49% of the time. |
| interview_ar_old_skin | 56% | -13.9 | warn | The speaker's face is in frame 56% of the time. |
| interview_ar_new_yunet | 100% | -13.9 | pass |  |

Reading: the gate separates the old and new crop on real clips (English 0 % to 49 %, Arabic 56 % to 100 %). The English new clip still fails because half its shots show the listener from behind, as noted in `README.md`. The face check counts any face fully inside the picture, not specifically the speaker.
