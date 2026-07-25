import os
import sqlite3
import time
from pathlib import Path

def get_queuectl_dir() -> Path:
    return Path(os.environ.get("QUEUECTL_HOME", ".queuectl"))

def get_db_path() -> Path:
    return get_queuectl_dir() / "queue.db"

QUEUECTL_DIR = get_queuectl_dir()
DB_PATH = get_db_path()
STALE_LOCK_SECONDS = 20

def _connect() -> sqlite3.Connection:
    q_dir = get_queuectl_dir()
    db_path = get_db_path()
    q_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def init_db():
    conn = _connect()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            command TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 3,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            locked_at TEXT,
            locked_by TEXT,
            next_retry_at TEXT,
            last_error TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )
    conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('max_retries', '3')")
    conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('backoff_base', '2')")
    conn.close()
