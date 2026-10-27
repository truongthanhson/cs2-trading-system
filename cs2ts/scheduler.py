"""Run `daily` once a day at a fixed local time, in a background thread.

Used by `python -m cs2ts serve`, which keeps the dashboard and the daily job
in one long-running process (handy in a container, where there is no cron).
"""

import threading
import time
import traceback
from datetime import date, datetime, timedelta


def next_run(now: datetime, at: str) -> datetime:
    hour, minute = (int(x) for x in at.split(":"))
    run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


def run_once(job, already_done, log=print) -> None:
    """Run the job unless today's snapshot exists (e.g. after a restart).

    Skipping matters because CSFloat's history limit only covers one run a day.
    """
    if already_done(date.today().isoformat()):
        log("Today's snapshot already exists; skipping.")
        return
    try:
        job()
    except Exception:
        log("Daily run failed:\n" + traceback.format_exc())


def start(job, already_done, at: str, log=print) -> threading.Thread:
    def loop():
        while True:
            when = next_run(datetime.now(), at)
            log(f"Next daily run at {when:%Y-%m-%d %H:%M}.")
            time.sleep(max(0.0, (when - datetime.now()).total_seconds()))
            run_once(job, already_done, log)

    thread = threading.Thread(target=loop, name="daily", daemon=True)
    thread.start()
    return thread
