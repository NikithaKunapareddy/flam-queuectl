# Decisions

## 1. Which exact line(s) prevent two workers from claiming the same job, and why is that atomic across separate OS processes?

In `db.py`, `claim_job()`:

```python
conn.execute("BEGIN IMMEDIATE")
...
cur = conn.execute("SELECT id FROM jobs WHERE state='pending' OR (...) ORDER BY created_at LIMIT 1")
...
conn.execute("UPDATE jobs SET state='processing', locked_at=?, locked_by=? WHERE id=?", ...)
conn.execute("COMMIT")
```

`BEGIN IMMEDIATE` acquires SQLite's write lock immediately, before the
SELECT runs. SQLite allows only one writer at a time *per database file*,
enforced by an OS-level file lock — this holds across separate processes,
not just threads, because it's implemented at the filesystem/OS level, not
in Python. A second worker process calling `claim_job()` concurrently
blocks on `BEGIN IMMEDIATE` (up to `busy_timeout`, 30s) until the first
transaction commits. So the SELECT-then-UPDATE pair is never interleaved
between two processes: worker B's SELECT can only run after worker A's
UPDATE has already committed, meaning the job A just claimed no longer
matches `state='pending'` and B's SELECT correctly skips it.

## 2. A worker is SIGKILLed halfway through a job. Walk through what happens.

1. Job `X` is `processing`, `locked_by=<worker-id>`, `locked_at` renewed
   every 5s by a heartbeat thread.
2. `SIGKILL` hits the worker process. No handler runs (can't be caught),
   so the process dies instantly. The DB row for `X` is untouched — it
   stays `processing` with whatever `locked_at` it last had.
3. `locked_at` stops advancing since no thread is renewing it.
4. Once `locked_at` is more than `STALE_LOCK_SECONDS` (20s) old, *any*
   worker's next poll iteration calls `reap_stale_jobs()`, which runs
   `UPDATE jobs SET state='pending' WHERE state='processing' AND
   locked_at < cutoff`. Job `X` becomes `pending` again.
5. On its next poll (`POLL_INTERVAL`, 2s), some worker claims `X` and runs
   it from scratch (the command has no way to resume mid-execution, so it
   restarts from the top — this assumes commands are safe to re-run,
   which is a reasonable assumption for a job queue but worth flagging).

**Worst-case recovery delay**: a worker could renew the heartbeat right
before being killed, so we might wait almost the full 20s staleness
window, plus up to one `POLL_INTERVAL` (2s) for some worker to notice, plus
the time for `reap_stale_jobs` and `claim_job` to run (milliseconds).
Worst case ≈ 22-23 seconds, comfortably under the 60s requirement. These
two constants (20s stale threshold, 2s poll interval) are the tunable
trade-off: lower them for faster recovery at the cost of more DB polling
overhead; raise them to reduce load at the cost of slower recovery.

## 3. Does `dlq retry` reset `attempts`? Why is that the right call?

Yes — `db.dlq_retry()` sets `attempts=0` on retry. A job reaches the DLQ
because it exhausted its retry budget under conditions that were, by
definition, not working. A manual `dlq retry` is a human decision that
circumstances have changed (a bug was fixed, a downstream dependency came
back up, credentials were rotated) — so the job deserves its full original
retry budget again rather than immediately re-dying after zero more
attempts (which is what would happen if `attempts` stayed at
`max_retries`). The alternative (not resetting) would make `dlq retry`
almost useless, since the job would fail the atomics check
(`attempts >= max_retries`) again on its very next failure.

## 4. What designs did you consider and reject for `worker stop`?

- **Rejected: DB row-based signaling** (write a `stop_requested` flag to
  a table, workers poll it). Rejected because it adds an extra poll query
  every cycle and, more importantly, is *slower* — a worker mid-sleep
  might not notice for up to a full poll interval, and it doesn't
  distinguish "stop this one worker" from "stop all workers" without more
  bookkeeping.
- **Rejected: a Unix domain control socket** that `worker start` listens
  on and `worker stop` connects to. This works but adds real complexity
  (socket lifecycle, cleanup on crash, one socket per worker or a shared
  one with routing) for no benefit over PID files, given OS signals
  already do exactly what we need.
- **Chosen: PID files + direct OS signals.** Each worker writes
  `.queuectl/workers/<pid>.json` on start and deletes it on clean exit.
  `worker stop` reads all PID files and calls `os.kill(pid, SIGTERM)`
  directly. This is simple, uses OS primitives workers already have to
  handle (SIGTERM/SIGINT), and naturally handles the "different terminal"
  requirement since PID files are on disk, not tied to any terminal
  session. Downside: a stale PID file (worker crashed without cleanup)
  could in theory reference a *reused* PID belonging to an unrelated
  process; we mitigate by checking process liveness with `os.kill(pid, 0)`
  before signaling, though a full fix would also compare a stored start
  timestamp against `/proc/<pid>/stat` — noted as a known gap.

## 5. If priorities were added tomorrow, what survives and what breaks?

**Survives unchanged**: the atomicity mechanism (`BEGIN IMMEDIATE` +
single UPDATE), the heartbeat/reap crash-recovery mechanism, the
worker-stop PID-file mechanism, the backoff/DLQ state machine — none of
these care about ordering.

**Breaks / needs changes**: `claim_job()`'s `ORDER BY created_at ASC`
is the only place priority-ordering logic lives, so it's a small,
localized change — `ORDER BY priority DESC, created_at ASC`, plus a new
`priority` column and accepting it in `enqueue`. The job spec's JSON
shape would need an optional `priority` field. Nothing about the
concurrency or crash-recovery story changes, because those operate on
"whichever row the SELECT picks," not on a specific ordering.
