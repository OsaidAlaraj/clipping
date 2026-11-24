"""Wait until YouTube stops asking for a sign-in (anonymous probe every 4 min), then prep the benchmark videos one by one, slowly."""
import subprocess, sys, time
import yt_dlp
PY = r"L:\Projects\Clipping\.venv\Scripts\python.exe"
JOBS = [("QGLvwQX-Aos", "en_podcast", "Druski|Theo Von Druski"), ("6GI89f8TuCc", "ar_podcast", "التشتت|فنجان التركيز"),
        ("V7WdPpqu9Q8", "en_gaming", "Happy Game"), ("D6EmhvQgBh4", "en_business", "Hormozi|Ed Mylett Hormozi"), ("kX1PASUTrEo", "en_stream", "Avengers|Kai Cenat Avengers")]
def ok():
    try:
        yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "js_runtimes": {"node": {}}}).extract_info("https://www.youtube.com/watch?v=QGLvwQX-Aos", download=False)
        return True
    except Exception as exc:
        print("blocked:", str(exc)[:60], flush=True); return False
for jid, label, q in JOBS:
    for _ in range(40):
        if ok(): break
        time.sleep(240)
    else:
        sys.exit("still blocked")
    r = subprocess.run([PY, "L:/Projects/Clipping/.claude/worktrees/owner-notes-7eaac2/tools/shots_eval/prep.py", jid, label, q], capture_output=True, text=True, encoding="utf-8")
    print(label, r.returncode, r.stdout[-300:], r.stderr[-200:], flush=True)
    time.sleep(120)
print("ALLDONE", flush=True)
