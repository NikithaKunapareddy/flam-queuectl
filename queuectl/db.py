"""
Storage layer for queuectl.

Why SQLite instead of raw JSON files:
- SQLite gives us a single writer at a time across *separate OS processes*
  for free (it takes an OS-level file lock on write). That means a single
  UPDATE statement is atomic across processes without us writing any
  locking code ourselves.
- We still enable WAL mode so readers (e.g. `queuectl status`) don't block
  writers, and we set a busy_timeout so a second process that tries to
  write while another is mid-transaction *waits* instead of erroring out.
"""

import os
import sqlite3
import time
from pathlib import Path


def get_queuectl_dir() -> Path:
    return Path(os.environ.get("QUEUECTL_HOME", ".queuectl"))


def get_db_path() -> Path:
    return get_queuectl_dir() / "queue.db"


# How long a worker can go without renewing its heartbeat on an in-flight
# job before another worker is allowed to assume it crashed and reclaim
# the job. Must comfortably clear the "<60s worst case recovery" rule.
STALE_LOCK_SECONDS = 20


def _connect() -> sqlite3.Connection:
    q_dir = get_queuectl_dir()
    db_path = get_db_path()
    q_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    # isolation_level=None -> autocommit mode; we open explicit
    # transactions ourselves with BEGIN IMMEDIATE where atomicity matters.
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")  # wait up to 30s on lock contention
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            command TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            next_retry_at TEXT,
            locked_at TEXT,
            locked_by TEXT,
            last_error TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    # seed defaults if absent
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('max_retries', '3')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('backoff_base', '2')"
    )
    conn.close()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------- config ----------


def get_config(key: str, default=None):
    conn = _connect()
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_config(key: str, value: str):
    conn = _connect()
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.close()


# ---------- job creation ----------


def enqueue_job(job_id: str, command: str, max_retries: int | None = None):
    conn = _connect()
    ts = now_iso()
    if max_retries is None:
        max_retries = int(get_config("max_retries", 3))
    try:
        conn.execute(
            """INSERT INTO jobs (id, command, state, attempts, max_retries,
                                  created_at, updated_at)
               VALUES (?, ?, 'pending', 0, ?, ?, ?)""",
            (job_id, command, max_retries, ts, ts),
        )
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"job id '{job_id}' already exists")
    conn.close()


# ---------- the atomic claim (Decision Q1 lives here) ----------


def claim_job(worker_id: str):
    """
    Atomically claim exactly one eligible job (pending, or failed whose
    backoff has elapsed) and mark it 'processing'.

    Atomicity story: BEGIN IMMEDIATE grabs SQLite's write lock for this
    connection *before* the SELECT subquery runs. Any other process
    calling claim_job() at the same moment blocks (via busy_timeout) until
    this transaction commits or rolls back - so two workers can never see
    the same "next eligible job" and both write to it. The single UPDATE
    statement (with its embedded SELECT) is what makes claiming atomic.
    """
    conn = _connect()
    ts = now_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            """
            SELECT id FROM jobs
            WHERE state = 'pending'
               OR (state = 'failed' AND next_retry_at IS NOT NULL AND next_retry_at <= ?)
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (ts,),
        )
        row = cur.fetchone()
        if row is None:
            conn.execute("COMMIT")
            conn.close()
            return None

        job_id = row["id"]
        conn.execute(
            """UPDATE jobs
               SET state = 'processing', locked_at = ?, locked_by = ?, updated_at = ?
               WHERE id = ?""",
            (ts, worker_id, ts, job_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.close()
        raise

    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return dict(job)


def heartbeat(job_id: str, worker_id: str):
    """Renew the lock on a job we're still actively running."""
    conn = _connect()
    conn.execute(
        "UPDATE jobs SET locked_at = ? WHERE id = ? AND locked_by = ? AND state = 'processing'",
        (now_iso(), job_id, worker_id),
    )
    conn.close()


def mark_completed(job_id: str):
    conn = _connect()
    ts = now_iso()
    conn.execute(
        """UPDATE jobs SET state='completed', updated_at=?, locked_at=NULL,
           locked_by=NULL, last_error=NULL WHERE id=?""",
        (ts, job_id),
    )
    conn.close()


def mark_failed(job_id: str, error: str):
    """
    Job's command exited non-zero. Bump attempts; either schedule a
    backoff retry ('failed') or move to the DLQ ('dead') if retries
    are exhausted.
    """
    conn = _connect()
    row = conn.execute(
        "SELECT attempts, max_retries FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    attempts = row["attempts"] + 1
    max_retries = row["max_retries"]
    ts = now_iso()

    if attempts >= max_retries:
        conn.execute(
            """UPDATE jobs SET state='dead', attempts=?, updated_at=?,
               locked_at=NULL, locked_by=NULL, last_error=? WHERE id=?""",
            (attempts, ts, error, job_id),
        )
    else:
        base = float(get_config("backoff_base", 2))
        delay = base**attempts
        next_retry_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + delay)
        )
        conn.execute(
            """UPDATE jobs SET state='failed', attempts=?, updated_at=?,
               next_retry_at=?, locked_at=NULL, locked_by=NULL, last_error=?
               WHERE id=?""",
            (attempts, ts, next_retry_at, error, job_id),
        )
    conn.close()


# ---------- crash recovery ----------


def reap_stale_jobs():
    """
    Any job still 'processing' whose heartbeat is older than
    STALE_LOCK_SECONDS is assumed to belong to a dead worker (SIGKILL
    leaves no trace, so this staleness check is the *only* signal we
    have). Put it back to 'pending' so it's picked up again.
    Returns the number of jobs reclaimed.
    """
    conn = _connect()
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - STALE_LOCK_SECONDS)
    )
    conn.execute("BEGIN IMMEDIATE")
    cur = conn.execute(
        """UPDATE jobs SET state='pending', locked_at=NULL, locked_by=NULL,
           updated_at=?
           WHERE state='processing' AND locked_at IS NOT NULL AND locked_at < ?""",
        (now_iso(), cutoff),
    )
    n = cur.rowcount
    conn.execute("COMMIT")
    conn.close()
    return n


# ---------- reads ----------


def list_jobs(state: str | None = None):
    conn = _connect()
    if state:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE state = ? ORDER BY created_at ASC", (state,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def counts_by_state():
    conn = _connect()
    rows = conn.execute(
        "SELECT state, COUNT(*) as n FROM jobs GROUP BY state"
    ).fetchall()
    conn.close()
    return {r["state"]: r["n"] for r in rows}


def dlq_retry(job_id: str):
    """
    Re-enqueue a dead job. We reset attempts to 0 - see DECISIONS.md Q3:
    a manual dlq retry is a human signal that circumstances changed
    (bug fixed, dependency back up), so the job should get its full
    retry budget again rather than immediately re-dying on attempt N.
    """
    conn = _connect()
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"no such job: {job_id}")
    if row["state"] != "dead":
        conn.close()
        raise ValueError(f"job {job_id} is not in the DLQ (state={row['state']})")
    ts = now_iso()
    conn.execute(
        """UPDATE jobs SET state='pending', attempts=0, next_retry_at=NULL,
           updated_at=?, last_error=NULL WHERE id=?""",
        (ts, job_id),
    )
    conn.close()
