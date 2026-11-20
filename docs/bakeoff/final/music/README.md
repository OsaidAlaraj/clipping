# Music bed bake-off (decision 32)

Real speech (30 s English interview clip, `../interview_en_new_yunet.mp4`, first 24 s), TikTok target -14 LUFS. `run_music_bakeoff.py` decodes the voice and the ducked bed as separate stems and measures them; `report.json` has every row.

**Proof clips:** `before_no_music.mp4` (voice only) and `after_with_music.mp4` (track `mm-dream-culture`, chosen by the selector, mixed in the same single encode). Both are 24 s, 540x960, one encode each, loudness -14 LUFS, peak under -1 dBFS.

**Mix grid (36 settings: bed level x compressor ratio x threshold).** Feasible = speech-to-music ratio while speaking >= 18 dB (speech band 300-3400 Hz), pauses <= 26 dB, final loudness within 1 LU of target, peak <= -1 dBFS. 31 of 36 passed. Winner = the most present bed that still passes: bed at -19 LUFS, ratio 14, threshold 0.06, release 350 ms. Result: 28.5 dB speech-to-music while speaking, 15.3 dB in the pauses, ducking depth 13.2 dB, final -14.1 LUFS, peak -1.5 dBFS. The winner sits on the edge of the grid (loudest bed, strongest ratio): a wider grid may find a bed that is a little louder still, but ducking already runs at the intelligibility floor's safe side, so it was not widened.

**Per-track check under the winning mix (17 tracks kept):** all but one keep the voice 20 dB or more above the bed while speaking. `Bit Quest` fell to 15.1 dB (busy mid-band) and was removed from the catalog. `Faster Does It` and `Investigations` are the quietest in the pauses (25.5 and 21.6 dB), i.e. their bed is the least present.

**Limits (honest):** the proxies are numbers, not ears: the owner should listen to the two proof clips once. The speech source is one interview; a gaming clip with loud game audio is not covered (the bed is mixed under the clip's whole original audio, so game sound is not ducked separately). Platform loudness is -14 LUFS for TikTok, Instagram and YouTube and -16 for Facebook: an assumption, not a measurement of each platform.
