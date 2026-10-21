"""Background loops: webhook delivery (every 2s) and the billing scheduler (every 30s).

Run exactly one worker process per database when you scale out: start the API servers with
SANGAM_RUN_WORKERS=false and a single `python -m sangam.workers` process."""
from __future__ import annotations

import logging
import threading
import time

from .db import SessionLocal, init_db
from .webhooks import dispatch_due

log = logging.getLogger("sangam.workers")


def _loop(stop: threading.Event, interval: float, fn, name: str) -> threading.Thread:
    def run() -> None:
        while not stop.is_set():
            try:
                with SessionLocal() as db:
                    fn(db)
            except Exception:  # keep the loop alive; individual items record their own errors
                log.exception("%s loop failed", name)
            stop.wait(interval)

    t = threading.Thread(target=run, name=f"sangam-{name}", daemon=True)
    t.start()
    return t


def start_workers(stop: threading.Event) -> None:
    from .billing import run_billing

    _loop(stop, 2.0, dispatch_due, "webhooks")
    _loop(stop, 30.0, run_billing, "billing")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    stop_event = threading.Event()
    start_workers(stop_event)
    log.info("SangamPay workers running")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop_event.set()
