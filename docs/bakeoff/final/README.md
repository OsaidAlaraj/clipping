# Before and after, real render stage

Four clips (30 s, 720x1280, one-encode render, same hook text, no word captions, no zooms of note): `interview_en_old_skin.mp4`, `interview_en_new_yunet.mp4`, `interview_ar_old_skin.mp4`, `interview_ar_new_yunet.mp4`. `contact_sheet.jpg` shows frames at 1, 6, 11, 16, 21 and 26 s (rows: en old, en new, ar old, ar new).

- **English interview (109-min podcast, 17:00 to 22:00 slice, from 1:40):** old crop shows nobody's face, only an arm and a shirt; new crop keeps a face in frame and jumps with each shot cut.
- **Where it is still wrong:** when the camera shows the listener from behind or in profile (frames 2 and 5 of the new row), YuNet frames his head, not the speaker. Speaker choice needs audio-visual detection (Light-ASD), not tested yet.
- **Arabic interview (8 min, 320x240 source, very soft):** one static speaker; the old crop was already acceptable, the new one frames the head slightly better. Small gain.
- Captions were left off in both versions so only the crop differs.
- `language-proof.txt`: the language check on the 5-minute English slice marked as Arabic (detected en, retried, 875 words).
