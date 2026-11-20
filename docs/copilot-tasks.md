# Copilot tasks (paste as one prompt)

Setup for Copilot: create a NEW local project folder `L:\Projects\Clipping-copilot` (drive L only, never C:), clone `github.com/OsaidAlaraj/clipping` there, and work on one branch per task, never on `main`, never push to `main`. Do not touch the render path, `quality_gate.py` or `.data/pg`. Never print `.env` values. Use the repo's `.venv` for tests (`-n 4`).

1. **Edge-case tests** for the music selector (`backend/clipping`, music module) and the speaker-crop fallback (`faces.track` falling back to the old crop): missing files, empty catalog, clips over 90 s, no audio, same track not repeated on consecutive clips. Branch `copilot/edge-tests`. Tests only, no production code changes.
2. **Arabic UI copy proofread** in `web/` (labels, empty states, errors): natural Jordanian-friendly Modern Standard phrasing, no logic changes, keep keys. Branch `copilot/ar-copy`. Run web tests, tsc and lint.
3. **Second licence check** of the 37 tracks in `backend/clipping/assets/music-catalog.json`: open each track's Free Music Archive page and confirm it says CC0 and Instrumental; write results to `docs/music-licence-check.md` (track id, URL, licence seen, pass/fail). Do not download audio. Branch `copilot/licence-check`.

Report back per task: branch, last commit, what is not done. The owner (via Claude) reviews before any merge.
