import argparse
import json
import os
import signal
import subprocess
import sys
import time

from . import db
from .worker import get_workers_dir


def cmd_enqueue(args):
    db.init_db()
    payload = json.loads(args.job_json)
    job_id = payload["id"]
    command = payload["command"]
    max_retries = payload.get("max_retries")  # None -> falls back to config default
    db.enqueue_job(job_id, command, max_retries)
    print(f"enqueued {job_id}")


def _live_workers():
    """Read PID files and filter out any whose process no longer exists
    (a worker that was SIGKILLed has no chance to delete its own file)."""
    w_dir = get_workers_dir()
    if not w_dir.exists():
        return []
    alive = []
    for f in w_dir.glob("*.json"):
        try:
            info = json.loads(f.read_text())
            pid = info["pid"]
        except Exception:
            f.unlink(missing_ok=True)
            continue
        try:
            os.kill(pid, 0)  # signal 0: existence check, doesn't actually kill
            alive.append(info)
        except ProcessLookupError:
            f.unlink(missing_ok=True)  # stale file from a crashed worker
        except PermissionError:
            alive.append(info)  # process exists, owned by someone else
    return alive


def cmd_worker_start(args):
    db.init_db()
    procs = []

    def _forward_and_wait(signum, frame):
        for p in procs:
            try:
                p.send_signal(signum)
            except ProcessLookupError:
                pass
        for p in procs:
            p.wait()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _forward_and_wait)
    signal.signal(signal.SIGINT, _forward_and_wait)

    for _ in range(args.count):
        p = subprocess.Popen([sys.executable, "-m", "queuectl.worker"])
        procs.append(p)

    print(f"started {args.count} worker(s), pids: {[p.pid for p in procs]}")
    for p in procs:
        p.wait()


def cmd_worker_stop(args):
    workers = _live_workers()
    if not workers:
        print("no live workers found")
        return
    for w in workers:
        try:
            os.kill(w["pid"], signal.SIGTERM)
            print(f"sent SIGTERM to pid {w['pid']} ({w['worker_id']})")
        except ProcessLookupError:
            pass

    deadline = time.time() + 30
    while time.time() < deadline:
        if not _live_workers():
            print("all workers stopped")
            return
        time.sleep(0.5)
    print("timed out waiting for some workers to stop")


def cmd_status(args):
    db.init_db()
    counts = db.counts_by_state()
    for state in ["pending", "processing", "failed", "completed", "dead"]:
        print(f"{state:10s}: {counts.get(state, 0)}")
    workers = _live_workers()
    print(f"active workers: {len(workers)}")
    for w in workers:
        print(f"  pid {w['pid']}  ({w['worker_id']})  started {w['started_at']}")


def cmd_list(args):
    db.init_db()
    jobs = db.list_jobs(args.state)
    if args.json:
        print(json.dumps(jobs))
    else:
        for j in jobs:
            print(
                f"{j['id']:20s} {j['state']:12s} attempts={j['attempts']}/{j['max_retries']}  {j['command']}"
            )


def cmd_dlq_list(args):
    db.init_db()
    jobs = db.list_jobs("dead")
    if args.json:
        print(json.dumps(jobs))
    else:
        for j in jobs:
            print(
                f"{j['id']:20s} attempts={j['attempts']}  last_error={j['last_error']}"
            )


def cmd_dlq_retry(args):
    db.init_db()
    db.dlq_retry(args.job_id)
    print(f"re-enqueued {args.job_id}")


def cmd_config_set(args):
    db.init_db()
    key = args.key.replace("-", "_")
    db.set_config(key, args.value)
    print(f"set {key} = {args.value}")


def cmd_config_get(args):
    db.init_db()
    key = args.key.replace("-", "_")
    print(db.get_config(key))


def build_parser():
    p = argparse.ArgumentParser(prog="queuectl")
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("enqueue", help="add a new job")
    e.add_argument(
        "job_json", help='JSON string, e.g. \'{"id":"job1","command":"sleep 2"}\''
    )
    e.set_defaults(func=cmd_enqueue)

    w = sub.add_parser("worker", help="manage workers")
    wsub = w.add_subparsers(dest="worker_command", required=True)

    ws = wsub.add_parser("start", help="start workers in the foreground")
    ws.add_argument("--count", type=int, default=1)
    ws.set_defaults(func=cmd_worker_start)

    wt = wsub.add_parser("stop", help="stop all running workers")
    wt.set_defaults(func=cmd_worker_stop)

    st = sub.add_parser("status", help="summary of job states & workers")
    st.set_defaults(func=cmd_status)

    ls = sub.add_parser("list", help="list jobs by state")
    ls.add_argument(
        "--state", choices=["pending", "processing", "failed", "completed", "dead"]
    )
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)

    d = sub.add_parser("dlq", help="inspect / retry dead jobs")
    dsub = d.add_subparsers(dest="dlq_command", required=True)
    dl = dsub.add_parser("list")
    dl.add_argument("--json", action="store_true")
    dl.set_defaults(func=cmd_dlq_list)
    dr = dsub.add_parser("retry")
    dr.add_argument("job_id")
    dr.set_defaults(func=cmd_dlq_retry)

    c = sub.add_parser("config", help="get/set configuration")
    csub = c.add_subparsers(dest="config_command", required=True)
    cs = csub.add_parser("set")
    cs.add_argument("key")
    cs.add_argument("value")
    cs.set_defaults(func=cmd_config_set)
    cg = csub.add_parser("get")
    cg.add_argument("key")
    cg.set_defaults(func=cmd_config_get)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
