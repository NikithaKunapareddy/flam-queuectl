# queuectl

A CLI-based background job queue with retries, exponential backoff, a dead
letter queue, and crash-safe persistence.

## Setup

```bash
pip install -e .
```

This installs a `queuectl` command backed by `queuectl/`. State lives in
`.queuectl/` in the current directory (a SQLite DB plus worker PID files) —
delete that folder to reset everything.

## Usage

```bash
# add jobs
queuectl enqueue '{"id":"job1","command":"echo hello"}'
queuectl enqueue '{"id":"job2","command":"sleep 2 && exit 1","max_retries":3}'

# terminal A - start 3 worker processes, blocks in the foreground
queuectl worker start --count 3

# terminal B - stop them gracefully (finishes any in-flight job first)
queuectl worker stop

# inspect
queuectl status
queuectl list --state pending
queuectl list --state dead --json

# dead letter queue
queuectl dlq list
queuectl dlq retry job2

# configuration
queuectl config set max-retries 5
queuectl config set backoff-base 3
```

## Architecture

- **Storage**: single SQLite file (`.queuectl/queue.db`, WAL mode). SQLite
  serializes writes across processes at the OS file-lock level, which is
  what makes job claiming atomic without any custom locking code.
- **Claiming a job**: one `UPDATE ... WHERE id = (SELECT ... LIMIT 1)`
  statement inside a `BEGIN IMMEDIATE` transaction (`db.claim_job`). See
  `DECISIONS.md` Q1 for why this is safe across processes.
- **Workers**: each `worker start --count N` spawns N independent OS
  processes (`subprocess.Popen`), each running its own poll loop
  (`worker.py`). Each writes a PID file to `.queuectl/workers/` on start.
- **Worker discovery/stop**: `worker stop` reads those PID files (from any
  terminal) and sends `SIGTERM` directly to each PID via `os.kill`. See
  `DECISIONS.md` Q4 for alternatives considered.
- **Crash recovery**: while running a job, a worker renews `locked_at`
  (heartbeat) every 5s on a background thread. Every worker's poll loop
  reaps any `processing` job whose heartbeat is >20s stale back to
  `pending`. See `DECISIONS.md` Q2.
- **Backoff**: on failure, `next_retry_at = now + backoff_base ^ attempts`.
  A failed job becomes claimable again once `next_retry_at` has passed.
  After `max_retries` failures the job moves to `dead` (the DLQ).

## Testing

Manual verification of the five required scenarios (also see git history
for how these were built up incrementally):

1. **Basic completion** — `queuectl enqueue` + `worker start`, confirm
   `queuectl list --state completed`.
2. **Fail → backoff → DLQ** — enqueue a job with `command` that exits
   non-zero and a low `max_retries`; watch it cycle `pending → processing
   → failed → processing → dead`.
3. **Many jobs, many workers, exactly once** — enqueue N jobs, start
   several workers, confirm `completed` count == N and no job ID appears
   twice in worker logs.
4. **SIGKILL mid-job** — start a worker on a long-running job, `kill -9`
   the worker PID, confirm the job is still marked `processing`, then
   start a fresh worker and confirm it gets reclaimed and completes
   within the recovery window.
5. **Restart persistence** — enqueue jobs, kill everything, restart
   workers, confirm nothing was lost (trivial given SQLite persistence,
   but verified).

Demo recording: <ADD YOUR LINK HERE>

## Development

To run the test-suite locally (recommended to run in a virtualenv):

```bash
python -m pip install -e .[dev]
pytest -q
```

Formatting & linting:

```bash
black .
ruff . --fix
```

Configuration & state:

- Runtime state is stored in `.queuectl/` by default; set `QUEUECTL_HOME`
  to change location (useful for tests or CI).
- Worker PID files live in `.queuectl/workers/` and are cleaned up when
  processes exit normally. If you see stale PID files, it's safe to remove
  them if the referenced process no longer exists.

Developer tools not on PATH
---------------------------------

If you installed dev dependencies with `python -m pip install -e .[dev]` on
Windows you may see warnings that scripts (e.g. `pytest`, `black`, `ruff`) are
installed to a Python `Scripts` directory that is not on your `PATH`. Two
options:

- Run tools via the interpreter module form (works regardless of PATH):

```powershell
python -m pytest -q
python -m black .
python -m ruff . --fix
```

- Add the Scripts folder to your PATH (example for PowerShell):

```powershell
$env:Path += ";$env:LocalAppData\Programs\Python\Python311\Scripts"
# or permanently via System Settings > Environment Variables
```

CI
--
An automated CI workflow is included at `.github/workflows/ci.yml` that runs
linters and tests on push & PRs.
