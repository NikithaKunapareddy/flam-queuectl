"""
A single worker process. Each worker is its own OS process (spawned via
subprocess.Popen from cli.py), so `worker start --count 3` from one
terminal produces 3 independent PIDs that `worker stop` from another
terminal can discover and signal individually.
"""

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import db

POLL_INTERVAL = 2  # seconds between "is there a job for me?" checks
HEARTBEAT_INTERVAL = 5  # seconds between heartbeat renewals on an in-flight job


def get_workers_dir() -> Path:
    return db.get_queuectl_dir() / "workers"


_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    # Graceful: just flip a flag. We check it between jobs, never mid-job,
    # so the current job always finishes (per the interface contract).
    _shutdown_requested = True


def _pid_file_path(pid: int) -> Path:
    return get_workers_dir() / f"{pid}.json"


def _register(worker_id: str):
    w_dir = get_workers_dir()
    w_dir.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    _pid_file_path(pid).write_text(
        json.dumps(
            {
                "pid": pid,
                "worker_id": worker_id,
                "started_at": db.now_iso(),
            }
        )
    )


def _unregister():
    p = _pid_file_path(os.getpid())
    if p.exists():
        p.unlink()


def _run_job(job: dict, worker_id: str):
    job_id = job["id"]
    stop_heartbeat = threading.Event()

    def _beat():
        while not stop_heartbeat.wait(HEARTBEAT_INTERVAL):
            db.heartbeat(job_id, worker_id)

    t = threading.Thread(target=_beat, daemon=True)
    t.start()

    try:
        # shell=True: spec requires commands to run "via the shell".
        result = subprocess.run(job["command"], shell=True)
        if result.returncode == 0:
            db.mark_completed(job_id)
            print(f"[{worker_id}] job {job_id} completed")
        else:
            db.mark_failed(job_id, f"exit code {result.returncode}")
            print(f"[{worker_id}] job {job_id} failed (exit {result.returncode})")
    except Exception as e:
        db.mark_failed(job_id, str(e))
        print(f"[{worker_id}] job {job_id} errored: {e}")
    finally:
        stop_heartbeat.set()
        t.join(timeout=1)


def run_worker_loop():
    """Entry point executed inside each worker subprocess."""
    worker_id = f"pid{os.getpid()}-{uuid.uuid4().hex[:6]}"
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _register(worker_id)
    print(f"[{worker_id}] worker started")

    try:
        while not _shutdown_requested:
            reclaimed = db.reap_stale_jobs()
            if reclaimed:
                print(f"[{worker_id}] reclaimed {reclaimed} stale job(s)")

            job = db.claim_job(worker_id)
            if job:
                _run_job(job, worker_id)
                continue  # check for shutdown flag / next job immediately

            # No job available - sleep, but wake up periodically to check
            # the shutdown flag instead of one long uninterruptible sleep.
            for _ in range(POLL_INTERVAL * 10):
                if _shutdown_requested:
                    break
                time.sleep(0.1)
    finally:
        _unregister()
        print(f"[{worker_id}] worker stopped")


if __name__ == "__main__":
    db.init_db()
    run_worker_loop()
