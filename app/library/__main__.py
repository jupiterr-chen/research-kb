"""Command-line entrypoint: serve, ingest, render, print-config."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from datetime import timedelta

from .api import build_server
from .catalog import Catalog
from .config import Config
from .ingest import Ingestor
from .locking import FileLock
from .markdown import render_vault
from .runtime import (
    parse_iso,
    read_scheduler_state,
    scheduler_state_path,
    utc_now,
    write_json_atomic,
)


DEFAULT_CONFIG = os.environ.get("RESEARCHKB_CONFIG", "/app/config/config.json")


def load_config(path: str) -> Config:
    config = Config.load(path)
    return config.resolve(os.path.dirname(os.path.abspath(path)))


class Scheduler:
    """Periodic in-process ingestion with a persisted, honest runtime state.

    The next automatic check is only ever derived from *this* scheduler's own
    cadence (wait interval after the scheduled run finishes); unrelated manual
    ingest runs recorded in ``ingest_runs`` never move it. A heartbeat keeps
    ``updated_at`` fresh so the API can declare the scheduler stale/dead when the
    worker stops instead of leaving an old success green forever.
    """

    HEARTBEAT_SECONDS = 15

    def __init__(self, config: Config):
        self.config = config
        self.stop_event = threading.Event()
        self.thread = None
        self.heartbeat_thread = None
        self._lock = threading.Lock()
        self._state = {}
        self._started_at = utc_now()

    def _write(self, **updates) -> None:
        with self._lock:
            self._state.update(updates)
            self._state["updated_at"] = utc_now()
            self._state.setdefault("interval_seconds", max(60, int(self.config.ingest_interval_seconds)))
            self._state.setdefault("started_at", self._started_at)
            self._state.setdefault("kind", "scheduler")
            try:
                write_json_atomic(scheduler_state_path(self.config.state_dir), self._state)
            except OSError:
                pass

    def _heartbeat(self) -> None:
        while not self.stop_event.wait(self.HEARTBEAT_SECONDS):
            self._write()

    def _loop(self) -> None:
        interval = max(60, int(self.config.ingest_interval_seconds))
        self._write(state="idle", interval_seconds=interval, next_check_at=None)
        while not self.stop_event.is_set():
            attempt_started = utc_now()
            self._write(state="running", attempt_started_at=attempt_started, next_check_at=None)
            ingestor = Ingestor(self.config)
            ok = False
            error = None
            try:
                result = ingestor.run()
                ok = bool(result.get("ok"))
                if not ok:
                    error = next(
                        (value.get("error") for value in result.get("sources", {}).values()
                         if isinstance(value, dict) and value.get("error")),
                        "source/render failure",
                    )
            except Exception as exc:  # never kill the scheduler thread
                error = type(exc).__name__
                print("ingest loop error: %s" % exc, file=sys.stderr, flush=True)
            finally:
                ingestor.close()
            finished = utc_now()
            moment = parse_iso(finished)
            next_at = ((moment + timedelta(seconds=interval)).replace(microsecond=0)
                       .isoformat()) if moment else None
            self._write(
                state="idle", last_attempt_started_at=attempt_started,
                last_finished_at=finished, last_ok=ok, last_error=error,
                next_check_at=next_at,
            )
            self.stop_event.wait(interval)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="ingest-scheduler", daemon=True)
        self.thread.start()
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat, name="ingest-heartbeat", daemon=True)
        self.heartbeat_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._write(state="stopped", next_check_at=None)


def scheduler_runtime(config: Config) -> dict:
    """Read the persisted scheduler state (used by tests/diagnostics)."""
    return read_scheduler_state(config.state_dir)


def cmd_serve(args) -> int:
    config = load_config(args.config)
    os.makedirs(config.state_dir, exist_ok=True)
    catalog = Catalog(config.catalog_db)
    scheduler = Scheduler(config)
    scheduler.start()
    server = build_server(config, catalog)
    print("library serving on http://%s:%d" % (config.bind_host, config.bind_port), flush=True)

    def _shutdown(signum, frame):
        print("shutting down (signal %s)" % signum, flush=True)
        scheduler.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        server.serve_forever(poll_interval=1)
    finally:
        server.server_close()
        catalog.close()
    return 0


def cmd_ingest(args) -> int:
    config = load_config(args.config)
    ingestor = Ingestor(config)
    try:
        result = ingestor.run()
    finally:
        ingestor.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def cmd_render(args) -> int:
    config = load_config(args.config)
    lock = FileLock(os.path.join(config.state_dir, "ingest.lock"))
    if not lock.acquire(blocking=False):
        print(json.dumps({"ok": False, "reason": "another ingest/render holds the task lock"}))
        return 1
    catalog = Catalog(config.catalog_db)
    try:
        stats = render_vault(config, catalog)
    finally:
        catalog.close()
        lock.release()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def cmd_print_config(args) -> int:
    config = load_config(args.config)
    print(json.dumps({
        "bind_host": config.bind_host,
        "bind_port": config.bind_port,
        "catalog_db": config.catalog_db,
        "vault_dir": config.vault_dir,
        "state_dir": config.state_dir,
        "public_base_url": config.public_base_url,
        "sources": {k: {"type": v.type, "root": v.root} for k, v in config.sources.items()},
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_selfcheck(args) -> int:
    """Validate that configured source roots and catalog are readable."""
    config = load_config(args.config)
    problems = []
    for name, source in config.sources.items():
        if not os.path.isdir(source.root):
            problems.append("source %s root missing: %s" % (name, source.root))
    catalog = Catalog(config.catalog_db)
    try:
        counts = catalog.counts()
    finally:
        catalog.close()
    print(json.dumps({"ok": not problems, "problems": problems, "counts": counts}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="library", description="Research KB library service")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "ingest", "render", "print-config", "selfcheck"):
        child = sub.add_parser(name)
        child.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    commands = {
        "serve": cmd_serve,
        "ingest": cmd_ingest,
        "render": cmd_render,
        "print-config": cmd_print_config,
        "selfcheck": cmd_selfcheck,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
