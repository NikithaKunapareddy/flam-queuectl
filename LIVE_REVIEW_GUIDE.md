# QueueCTL — Live Review & Defense Guide

This document is engineered to help you **ace the 30-minute live technical review** and demonstrate 100% mastery of every line of code in `queuectl`.

---

## 1. The 60-Second Architecture Pitch (Memorize This)

> *"QueueCTL is a multi-process, background job processing queue built on Python and SQLite. It uses SQLite as a persistent, cross-process transactional store where `BEGIN IMMEDIATE` locks the database file at the OS filesystem level, ensuring that concurrent worker processes can never claim the same job. Worker processes are spawned as independent OS processes with their own PID files and communicate via OS signals (`SIGTERM`/`SIGINT`). To recover from hard crashes like `SIGKILL`, workers maintain a lightweight heartbeat while running jobs; if a worker dies without cleanup, any active worker automatically reaps and re-queues stale jobs whose heartbeat has expired."*

---

## 2. Master Answers to the 5 Core Decision Questions

### Q1: Which exact line(s) prevent two workers from claiming the same job, and why is that atomic across separate OS processes?
- **The Exact Lines** (in `db.py` -> `claim_job()`):
  ```python
  conn.execute("BEGIN IMMEDIATE")
  cur = conn.execute(
      """SELECT id FROM jobs
         WHERE state = 'pending'
            OR (state = 'failed' AND next_retry_at IS NOT NULL AND next_retry_at <= ?)
         ORDER BY created_at ASC LIMIT 1""",
      (now_iso(),),
  )
  row = cur.fetchone()
  ...
  conn.execute(
      """UPDATE jobs SET state = 'processing', updated_at = ?, locked_at = ?, locked_by = ?
         WHERE id = ?""",
      (now_iso(), now_iso(), worker_id, job_id),
  )
  conn.execute("COMMIT")
  ```
- **Why it is atomic across OS processes**:
  - `BEGIN IMMEDIATE` acquires SQLite's **RESERVED / EXCLUSIVE write lock** immediately on the database file before the `SELECT` query runs.
  - This lock is enforced by the operating system (filesystem locks), **not** by Python threads or memory.
  - While Worker A holds this transaction lock, any concurrent Worker B process calling `claim_job()` blocks at `BEGIN IMMEDIATE` (up to `busy_timeout = 30000ms`).
  - By the time Worker B's transaction starts, Worker A has already committed `state = 'processing'`, so Worker B's `SELECT` query skips that row.

---

### Q2: A worker is `SIGKILL`ed halfway through a job. Walk through what happens line-by-line.
1. **Immediate Crash**: Because `SIGKILL` cannot be caught or handled by user code, the worker process terminates instantly. The database row for the job remains in `state = 'processing'`.
2. **Heartbeat Stops**: The background heartbeat thread (`_beat()` in `worker.py`) stops renewing the `locked_at` timestamp.
3. **Staleness Threshold Exceeded**: Once `locked_at` is older than `STALE_LOCK_SECONDS` (20 seconds), the lock is considered abandoned.
4. **Reaping by Surviving Workers**:
   - At the top of every poll loop (`run_worker_loop()` in `worker.py`), every active worker calls `db.reap_stale_jobs()`.
   - `reap_stale_jobs()` executes:
     ```sql
     UPDATE jobs
     SET state = 'pending', locked_at = NULL, locked_by = NULL, updated_at = ?
     WHERE state = 'processing' AND locked_at < ?
     ```
   - The job is reset to `pending` and immediately claimed by the next available worker.
5. **Recovery Time Math**:
   - Maximum time to notice crash = `STALE_LOCK_SECONDS` (20s) + `POLL_INTERVAL` (2s) ≈ **22 seconds**, well below the **<60s** SLA requirement.

---

### Q3: Does `dlq retry` reset `attempts`? Why is that the right call?
- **Yes**, `db.dlq_retry(job_id)` executes:
  ```sql
  UPDATE jobs
  SET state = 'pending', attempts = 0, next_retry_at = NULL, last_error = NULL
  WHERE id = ? AND state = 'dead'
  ```
- **Why**: A job lands in the Dead Letter Queue (`dead`) because it exhausted its retry attempts (`attempts >= max_retries`). A human operator running `queuectl dlq retry <job-id>` indicates that external conditions have been fixed (e.g., API key rotated, database restored, bug patched). Resetting `attempts = 0` gives the job a fresh retry budget so it doesn't immediately fail and re-enter the DLQ on a transient glitch.

---

### Q4: What designs did you consider and reject for `worker stop`?
1. **Rejected — DB Polling Flag (`stop_requested` table/column)**:
   - Workers would poll SQLite to check if they should shut down.
   - *Why rejected*: Unnecessary database read I/O on every cycle, slower responsiveness (workers sleeping between polls), and harder to target a specific worker PID.
2. **Rejected — Unix Domain Control Socket / Named Pipes**:
   - `worker start` listens on a socket; `worker stop` sends an IPC command.
   - *Why rejected*: Significant lifecycle complexity (stale socket cleanup on crash, routing protocols) with zero functional benefit over OS signals.
3. **Chosen — PID Files + Direct OS Signals (`SIGTERM`)**:
   - Each worker writes a JSON file `.queuectl/workers/<pid>.json` upon startup and deletes it on clean shutdown.
   - `worker stop` reads all live PID files, checks process existence via `os.kill(pid, 0)`, and sends `SIGTERM` (`os.kill(pid, signal.SIGTERM)`).
   - Simple, robust across different terminal sessions, and uses native OS primitives.

---

### Q5: If priorities were added tomorrow, what survives and what breaks?
- **What Survives (Unchanged)**:
  - All concurrency and atomicity (`BEGIN IMMEDIATE`, transaction locking).
  - All crash recovery and heartbeat reaping (`reap_stale_jobs()`).
  - Worker lifecycle, PID files, and DLQ backoff logic.
- **What Changes (Localized)**:
  - Add a `priority INTEGER DEFAULT 0` column to the `jobs` schema in `db.py`.
  - Update `claim_job()`'s `SELECT` query from `ORDER BY created_at ASC` to `ORDER BY priority DESC, created_at ASC`.
  - Accept an optional `--priority` or `"priority"` field in `queuectl enqueue`.

---

## 3. How to Ace the Live Coding Challenge (5-Minute Practice)

During the interview, the reviewer will ask you to modify the codebase live (e.g., *"Add a priority field"* or *"Change the JSON output sorting"*). Here is how to execute calmly and cleanly:

### Practice Task: "Sort `queuectl list --json` by attempts descending"
1. Open `cli.py` and locate `cmd_list(args)`.
2. Notice it calls `jobs = db.list_jobs(args.state)`.
3. Open `db.py` and locate `list_jobs(state)`.
4. Change:
   ```sql
   -- Old:
   SELECT * FROM jobs ORDER BY created_at ASC
   -- New:
   SELECT * FROM jobs ORDER BY attempts DESC, created_at ASC
   ```
5. Run `pytest` in your terminal to show that all tests still pass!

---

## 4. Test Suite Summary (All 6 Scenarios Automated)
Run `pytest -v` at any time to demonstrate:
- `test_enqueue_and_list` — Basic enqueueing & state filtering (Scenario 1)
- `test_claim_and_complete` — Job lifecycle completion (Scenario 1 & 5)
- `test_mark_failed_and_backoff` — Exponential backoff & DLQ routing (Scenario 2)
- `test_dlq_retry_resets_attempts` — DLQ human retry behavior (Scenario 2 & Decision Q3)
- `test_stale_job_reaping` — `SIGKILL` crash recovery & heartbeat SLA (Scenario 4)
- `test_concurrent_claims_no_duplicate` — Multi-thread/multi-process exactly-once execution (Scenario 3)
