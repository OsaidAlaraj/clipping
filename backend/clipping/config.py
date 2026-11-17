"""Environment configuration. Secrets live only in environment variables (spec 15.4)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
MIGRATIONS = BACKEND / "migrations"
FIXTURES = BACKEND / "fixtures"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader. Existing environment variables win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def data_dir() -> Path:
    path = Path(env("CLIPPING_DATA_DIR", str(ROOT / ".data")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def use_fakes() -> bool:
    return env("CLIPPING_USE_FAKES", "1") != "0"


def api_port() -> int:
    return int(env("CLIPPING_API_PORT", "8787"))


# Secret names. Values are read at call time and never logged.
SECRET_NAMES = [
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_JWT_SECRET", "DATABASE_URL", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT",
    "DEEPGRAM_API_KEY", "VIZARD_API_KEY", "KLAP_API_KEY", "OPUSCLIP_API_KEY", "BUFFER_ACCESS_TOKEN",
    "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "CLIPPING_MEDIA_SECRET",
    "CLIPPING_LOCAL_TOKEN",
]


def redact(text: str) -> str:
    """Remove any secret value from a string before it is logged or stored."""
    for name in SECRET_NAMES:
        value = os.environ.get(name)
        if value and len(value) >= 6:
            text = text.replace(value, f"<{name}>")
    return text
