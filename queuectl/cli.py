import argparse
import json
import os
import sys
from . import db

def cmd_enqueue(args):
    db.init_db()
    payload = json.loads(args.job_json)
    job_id = payload["id"]
    command = payload["command"]
    max_retries = payload.get("max_retries")
    db.enqueue_job(job_id, command, max_retries)
    print(f"enqueued {job_id}")

def cmd_status(args):
    db.init_db()
    counts = db.counts_by_state()
    for state in ["pending", "processing", "failed", "completed", "dead"]:
        print(f"{state:10s}: {counts.get(state, 0)}")

def cmd_list(args):
    db.init_db()
    jobs = db.list_jobs(args.state)
    if args.json:
        print(json.dumps(jobs))
    else:
        for j in jobs:
            print(f"{j['id']:20s} {j['state']:12s} attempts={j['attempts']}/{j['max_retries']}  {j['command']}")

def build_parser():
    p = argparse.ArgumentParser(prog="queuectl")
    sub = p.add_subparsers(dest="command", required=True)
    e = sub.add_parser("enqueue", help="add a new job")
    e.add_argument("job_json")
    e.set_defaults(func=cmd_enqueue)
    st = sub.add_parser("status", help="summary of job states")
    st.set_defaults(func=cmd_status)
    ls = sub.add_parser("list", help="list jobs by state")
    ls.add_argument("--state", choices=["pending", "processing", "failed", "completed", "dead"])
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)
    return p

def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
