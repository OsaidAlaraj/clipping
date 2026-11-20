"""One command for the whole system.

    python run.py          ONE command for everything: install what is missing, start the API, the worker and the review app
                           (a production build, rebuilt only when the web sources changed), wait until they answer, open the
                           browser, and print the phone address (or double-click start.cmd)
    python run.py --dev    the same with the hot-reload dev server (`next dev`) for working on the web app
    python run.py test     run the backend and web test suites
    python run.py demo     run the sample-campaign demo (all fakes) against a fresh throwaway database and print the run log
    python run.py demo --no-keys   the same flow with fakes off and every key blanked: it runs on the declared fallbacks
                                   (local Whisper, signals-only ranking), whatever is in .env
    python run.py phone    make the app reachable from your phone through Tailscale
    python run.py stop     stop the local Postgres if a killed run left it running
    python run.py backup   write a database dump now into .data/backups (keeps the newest 14)

Needs Python 3.11+ and Node 20+ (with pnpm or npm). Postgres and FFmpeg are installed into .venv by pip.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"

# Keep everything this project writes on its own drive (owner's rule): temp files, pip, npm and model caches.
_DATA = ROOT / ".data"
for _key, _path in {"TMP": _DATA / "tmp", "TEMP": _DATA / "tmp", "PIP_CACHE_DIR": _DATA / "cache" / "pip",
                    "npm_config_cache": _DATA / "cache" / "npm", "HF_HOME": _DATA / "cache" / "huggingface",
                    "XDG_CACHE_HOME": _DATA / "cache"}.items():
    _path.mkdir(parents=True, exist_ok=True)
    os.environ[_key] = str(_path)
tempfile.tempdir = str(_DATA / "tmp")
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
BACKEND, WEB = ROOT / "backend", ROOT / "web"


def node_tool() -> str:
    for tool in ("pnpm", "npm"):
        if shutil.which(tool):
            return shutil.which(tool)  # type: ignore[return-value]
    sys.exit("Node.js with pnpm or npm is required for the review app.")


def bootstrap() -> None:
    if not PY.exists():
        print("Creating .venv ...")
        venv.create(VENV, with_pip=True)
    marker = VENV / ".deps-installed"
    requirements = BACKEND / "requirements.txt"
    if not marker.exists() or marker.stat().st_mtime < requirements.stat().st_mtime:
        print("Installing Python packages ...")
        subprocess.check_call([str(PY), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(requirements)])
        marker.write_text("ok")
    if not (WEB / "node_modules").exists():
        print("Installing web packages ...")
        subprocess.check_call([node_tool(), "install"], cwd=WEB)


WEB_SOURCES = ("app", "components", "lib", "public", "package.json", "pnpm-lock.yaml", "next.config.mjs", "tsconfig.json")


def web_build_is_stale(web: Path = WEB) -> bool:
    """True when there is no production build, or any web source is newer than it (so `next build` must run again)."""
    marker = web / ".next" / "BUILD_ID"
    if not marker.exists():
        return True
    built = marker.stat().st_mtime
    for name in WEB_SOURCES:
        path = web / name
        files = [path] if path.is_file() else [f for f in path.rglob("*") if f.is_file()] if path.is_dir() else []
        if any(f.stat().st_mtime > built for f in files):
            return True
    return False


def ensure_web_build() -> bool:
    """Build the review app once and again only when its sources changed. Returns False when the build failed."""
    if not web_build_is_stale():
        return True
    print("Building the review app (first start, or the web code changed) ...")
    if subprocess.call([node_tool(), "run", "build"], cwd=WEB, env=env()) != 0:
        print("The web build failed. Fix the errors above, or run  python run.py --dev  to use the dev server.")
        return False
    return True


def phone_setup() -> int:
    """Make the app reachable from your phone through Tailscale (private to your devices, HTTPS on ts.net)."""
    import json

    tailscale = shutil.which("tailscale") or (r"C:\Program Files\Tailscale\tailscale.exe" if os.name == "nt" else None)
    if not tailscale or not Path(tailscale).exists():
        print("Tailscale is not installed. Install it from https://tailscale.com/download on this laptop and on your phone,")
        print("sign in to the same account on both, then run:  python run.py phone")
        return 1
    try:
        status = json.loads(subprocess.check_output([tailscale, "status", "--json"], text=True))
    except Exception:
        print("Tailscale is installed but not running or not signed in. Open Tailscale, sign in, then run this again.")
        return 1
    host = (status.get("Self", {}).get("DNSName") or "").rstrip(".")
    if not host:
        print("Tailscale did not report this laptop's name. Turn on MagicDNS in the Tailscale admin console, then run this again.")
        return 1
    print(f"Serving the app privately at https://{host} (only your Tailscale devices can open it) ...")
    code = subprocess.call([tailscale, "serve", "--bg", "--set-path", "/api", "http://127.0.0.1:8787/api"])
    if code == 0:
        code = subprocess.call([tailscale, "serve", "--bg", "3000"])
    if code != 0:
        print("tailscale serve failed. In the admin console, turn on HTTPS certificates (DNS page), then run this again.")
        return code
    env_file = ROOT / ".env"
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    lines = [line for line in lines if not line.startswith("CLIPPING_PUBLIC_ORIGIN=")] + [f"CLIPPING_PUBLIC_ORIGIN=https://{host}"]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Done. Restart with  python run.py  and open https://{host} on your phone.")
    print("First time on the phone: choose 'Set up this device' and enter a code from Settings > Add another device.")
    return 0


# Everything that could reach a live service. `demo --no-keys` blanks these in the demo process only; .env is never edited
# (an empty value in the process environment wins over the file).
KEY_NAMES = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET",
             "DATABASE_URL", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_VISION_DEPLOYMENT",
             "DEEPGRAM_API_KEY", "VIZARD_API_KEY", "KLAP_API_KEY", "OPUSCLIP_API_KEY", "BUFFER_ACCESS_TOKEN", "R2_ACCOUNT_ID",
             "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "TOKEN_VAULT_KEY", "GOOGLE_CLIENT_SECRET", "INSTAGRAM_APP_SECRET",
             "TIKTOK_CLIENT_SECRET")


def demo_env(data_dir: str, no_keys: bool) -> dict:
    """The demo never uses the owner's real services: all fakes by default, or fakes off with no keys at all."""
    extra = {"CLIPPING_USE_FAKES": "0", **{name: "" for name in KEY_NAMES}} if no_keys else {"CLIPPING_USE_FAKES": "1"}
    return {**env(), "CLIPPING_DATA_DIR": data_dir, **extra}


def env() -> dict:
    out = dict(os.environ)
    out.setdefault("PYTHONIOENCODING", "utf-8")
    return out


def wait_and_open() -> None:
    """When the API and the review app both answer, open the browser and print where to find the app (runs in a thread)."""
    import threading
    import time
    import urllib.request
    import webbrowser

    def up(url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return response.status < 500
        except Exception:
            return False

    def run() -> None:
        deadline = time.time() + 240
        while time.time() < deadline and not (up("http://127.0.0.1:8787/api/health") and up("http://localhost:3000/signin")):
            time.sleep(2)
        else:
            if time.time() >= deadline:
                print("The app did not answer within 4 minutes. Check the messages above.")
                return
        webbrowser.open("http://localhost:3000")
        origin = ""
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("CLIPPING_PUBLIC_ORIGIN="):
                    origin = line.split("=", 1)[1].strip()
        print()
        print("Ready. On this laptop: http://localhost:3000")
        print(f"On your phone (Tailscale on): {origin}" if origin else "For the phone, run  python run.py phone  once.")
        print("Ctrl+C stops everything.")
        print()

    threading.Thread(target=run, daemon=True).start()


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--dev"]
    dev_server = "--dev" in sys.argv[1:]
    command = args[0] if args else "start"
    bootstrap()
    if command == "test":
        backend = subprocess.call([str(PY), "-m", "pytest", "-n", "auto"], cwd=BACKEND, env=env())   # parallel workers
        web = subprocess.call([node_tool(), "exec", "vitest", "run"], cwd=WEB, env=env())
        types = subprocess.call([node_tool(), "exec", "tsc", "--noEmit"], cwd=WEB, env=env())
        print(f"\nbackend tests: {'PASS' if backend == 0 else 'FAIL'}   web tests: {'PASS' if web == 0 else 'FAIL'}   "
              f"type check: {'PASS' if types == 0 else 'FAIL'}")
        return backend or web or types
    if command == "phone":
        return phone_setup()
    if command == "demo":
        with tempfile.TemporaryDirectory(prefix="clip_demo_", ignore_cleanup_errors=True) as tmp:
            return subprocess.call([str(PY), "-m", "clipping", "demo"], cwd=BACKEND, env=demo_env(tmp, "--no-keys" in sys.argv[2:]))
    if command == "stop":
        return subprocess.call([str(PY), "-m", "clipping", "stop"], cwd=BACKEND, env=env())
    if command == "backup":
        return subprocess.call([str(PY), "-m", "clipping", "backup"], cwd=BACKEND, env=env())
    if command not in ("start", "up"):
        print(__doc__)
        return 2
    if not dev_server and not ensure_web_build():
        return 1
    print("Starting the API and worker on http://127.0.0.1:8787 and the review app on http://localhost:3000"
          + (" (dev server)" if dev_server else ""))
    print("First start seeds the sample campaign, which takes about a minute. Ctrl+C stops everything.\n")
    wait_and_open()
    api = subprocess.Popen([str(PY), "-m", "clipping", "dev"], cwd=BACKEND, env=env())
    web = subprocess.Popen([node_tool(), "run", "dev" if dev_server else "start"], cwd=WEB, env=env())
    try:
        while api.poll() is None and web.poll() is None:
            try:
                api.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        for proc in (web, api):
            if proc.poll() is None:
                proc.terminate()
        subprocess.call([str(PY), "-m", "clipping", "stop"], cwd=BACKEND, env=env())   # a killed API cannot stop Postgres itself
    return 0


if __name__ == "__main__":
    sys.exit(main())
