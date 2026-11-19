"""Command line: python -m clipping <command>

  dev        migrate, seed the sample campaign, start the worker and the API (used by run.py)
  migrate    apply database migrations
  api        start the API only
  worker     start the worker only
  demo       run the sample-campaign demo and print the run log
  keepalive  run the database keep-alive once
  stop       stop the embedded Postgres if it was left running
  backup     write a database dump now into .data/backups (keeps the newest 14)
  restore    restore a dump into a new scratch database (never the live one)
"""
from __future__ import annotations

import sys
import threading

from . import config, ops
from .db import get_db


def main(argv: list[str]) -> int:
    config.load_dotenv()
    command = argv[1] if len(argv) > 1 else "dev"
    if command in ("backup", "restore"):
        from . import backup

        return backup.cli(argv)
    if command == "stop":   # stop the embedded Postgres if an abrupt exit left it running
        from . import db as dbmod

        if not config.env("DATABASE_URL") and (config.data_dir() / "pg" / "PG_VERSION").exists():
            dbmod.start_local_postgres()
            dbmod._local_server.cleanup()
        return 0
    if command not in ("dev", "migrate", "api", "worker", "demo", "keepalive"):
        print(__doc__)
        return 2
    db = get_db()
    applied = db.migrate()
    if applied:
        print("Applied migrations:", ", ".join(applied))
    ops.ensure_owner(db)
    if command in ("dev", "api"):
        from . import auth

        code = auth.startup_code(db)
        if code:
            print("\n  First sign-in: open the app and enter this one-time setup code to create your passkey:")
            print(f"      {code}      (valid 30 minutes; restart to get a new one)\n")
    for change in ops.configure_adapters(db):
        print("Adapter switched on:", change)
    if command == "migrate":
        return 0
    if command == "keepalive":
        return 0 if ops.keepalive(db) else 1
    if command == "demo":
        from . import demo

        print("Sample-campaign demo")
        demo.run(db, finish_all="--feed-only" not in argv)
        return 0
    stop = threading.Event()
    if command in ("dev", "worker"):
        if command == "dev" and "--no-seed" not in argv:
            from . import demo

            demo.run(db, log=lambda *_: None, finish_all=False)
        thread = threading.Thread(target=ops.worker_loop, args=(db, stop), daemon=True)
        thread.start()
        if command == "worker":
            print("Worker running. Ctrl+C to stop.")
            try:
                thread.join()
            except KeyboardInterrupt:
                stop.set()
            return 0
    import uvicorn

    host = config.env("CLIPPING_API_HOST", "127.0.0.1")
    print(f"API on http://{host}:{config.api_port()}  (fakes: {'on' if config.use_fakes() else 'off'})")
    uvicorn.run("clipping.api:app", host=host, port=config.api_port(), log_level="warning")
    stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
