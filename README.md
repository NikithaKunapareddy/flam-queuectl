# QueueCTL

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![SQLite WAL](https://img.shields.io/badge/backed%20by-SQLite%20WAL-003B57.svg)](https://sqlite.org/wal.html)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero%20core-brightgreen.svg)]()
[![Live Demo](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://nikithakunapareddy-flam-queuectl-streamlit-app-sg4odx.streamlit.app/)

> 🌐 **[Live Demo → QueueCTL Web Dashboard](https://nikithakunapareddy-flam-queuectl-streamlit-app-sg4odx.streamlit.app/)**

> **A production-grade, lightweight CLI background job queue backed by SQLite—featuring cross-process atomicity, automated crash recovery, exponential backoff, and Dead Letter Queue (DLQ) routing.**

---

## Executive Summary

**QueueCTL** is a fault-tolerant job queue system designed for multi-process concurrency without requiring heavy external infrastructure like Redis, RabbitMQ, or Docker. By leveraging SQLite's Write-Ahead Logging (WAL) and filesystem-level `BEGIN IMMEDIATE` transactions, QueueCTL guarantees **exactly-once execution** across independent OS processes while providing resilience against unexpected crashes (`SIGKILL`).

### ✨ Key Capabilities
* **Cross-Process Atomicity**: Safe multi-worker concurrent job claiming with zero race conditions or duplicate execution.
* **Automated Crash Recovery**: Worker background heartbeat threads (`locked_at`) and automatic reaping of stale jobs within `<20s`.
* **Smart Retry Engine**: Configurable exponential backoff (`backoff_base ** attempts`) to prevent overwhelming downstream services.
* **Dead Letter Queue (DLQ)**: Automatic quarantine of permanently failed jobs after attempt exhaustion, with inspection and 1-click rescue commands.
* **Zero External Dependencies**: Built using standard Python library components and SQLite for maximum portability.

---

## 🏗️ System Architecture

```text
       +-----------------------------------------------------------------+
       |                       QueueCTL Entry Point                      |
       |  (queuectl enqueue | status | list | worker | dlq | config)     |
       +-----------------------------------------------------------------+
                                        |
                 +----------------------+----------------------+
                 | (Writes jobs / config)                      | (Spawns worker pool)
                 v                                             v
       +-----------------------------------+     +---------------------------+
       |         SQLite Database           |     |     OS Worker Processes   |
       |     (.queuectl/queue.db - WAL)    |     |  [PID 1]  [PID 2]  [PID N]  |
       +-----------------------------------+     +---------------------------+
                 ^                                             |
                 |             BEGIN IMMEDIATE claim_job       |
                 +---------------------------------------------+
                 |
                 |-- State: pending    -> processing -> completed
                 |-- State: processing -> (crash >20s) -> reaped to pending
                 |-- State: failed     -> (retries exhausted) -> dead (DLQ)
```

For an in-depth architectural breakdown and answers to core design trade-offs, see [`DECISIONS.md`](./DECISIONS.md) and [`LIVE_REVIEW_GUIDE.md`](./LIVE_REVIEW_GUIDE.md).

---

## 🚀 Quickstart & Installation

### 1. Clone & Install in Editable Mode
Requires Python 3.10 or newer.

```bash
git clone https://github.com/NikithaKunapareddy/flam-queuectl.git
cd flam-queuectl
pip install -e .
```

*This installs the global `queuectl` CLI command. State is stored locally in `.queuectl/`.*

---

## 📖 Complete CLI Reference & Examples

### 1. Job Lifecycle Management

```powershell
# Check queue overview and active worker count
queuectl status

# Enqueue a simple job (Windows PowerShell format)
python -m queuectl.cli enqueue '{\"id\":\"job-1\", \"command\":\"echo Hello QueueCTL\"}'

# Enqueue a job with custom max_retries
python -m queuectl.cli enqueue '{\"id\":\"job-2\", \"command\":\"ping 127.0.0.1 -n 3\", \"max_retries\":5}'

# List pending jobs in table format
queuectl list --state pending

# Export queue records as JSON (ideal for scripts & API integrations)
queuectl list --state pending --json
```

---

### 2. Multi-Process Worker Pool

Workers run as separate OS processes and poll the database for pending jobs.

```powershell
# Start 3 concurrent worker processes (runs in foreground)
queuectl worker start --count 3

# Stop workers cleanly (press Ctrl + C in foreground terminal, or from another terminal:)
queuectl worker stop
```

*Workers automatically register their PIDs in `.queuectl/workers/` and clean up upon termination.*

---

### 3. Dead Letter Queue (DLQ) & Error Handling

When a job fails and exhausts its `max_retries`, it transitions to the **Dead Letter Queue (`dead`)** with its failure reason preserved.

```powershell
# Enqueue a job that intentionally fails
python -m queuectl.cli enqueue '{\"id\":\"job-fail\", \"command\":\"cmd /c exit 1\", \"max_retries\":1}'

# View quarantined jobs in the DLQ
queuectl dlq list

# Rescue a job from DLQ back to the Pending queue (resets attempts = 0)
queuectl dlq retry job-fail
```

---

### 4. Persistent Configuration

Runtime settings are stored persistently in the SQLite `config` table.

```powershell
# Get current configuration
queuectl config get max_retries

# Update retry threshold and backoff multiplier
queuectl config set max_retries 5
queuectl config set backoff_base 3
```

---

## 📊 CLI Command Table

| Command | Subcommand | Arguments / Options | Description |
| :--- | :--- | :--- | :--- |
| `queuectl` | `status` | — | Displays counts for all job states and active worker PIDs. |
| `queuectl` | `enqueue` | `<json_string>` | Enqueues a new job with `id`, `command`, and optional `max_retries`. |
| `queuectl` | `list` | `--state <state> [--json]` | Lists jobs by state (`pending`, `processing`, `completed`, `dead`). |
| `queuectl` | `worker start`| `--count <N>` | Spawns `N` concurrent OS worker processes to claim and run jobs. |
| `queuectl` | `worker stop` | — | Sends termination signals to all active worker processes. |
| `queuectl` | `dlq list` | — | Displays all jobs quarantined in the Dead Letter Queue (`dead`). |
| `queuectl` | `dlq retry` | `<job_id>` | Re-enqueues a dead job back to `pending` with attempts reset to 0. |
| `queuectl` | `config get` | `<key>` | Retrieves a runtime config parameter (`max_retries`, `backoff_base`). |
| `queuectl` | `config set` | `<key> <val>` | Persistently updates a runtime config parameter. |

---

## 🌐 Streamlit Interactive Web Dashboard

QueueCTL includes a real-time web UI built with **Streamlit** (`streamlit_app.py`). It provides a visual interface to monitor background job queues, inspect KPI cards, enqueue commands, and rescue Dead Letter Queue (DLQ) jobs with a single click.

```bash
# 1. Install Streamlit and Pandas
pip install streamlit pandas

# 2. Launch the Web Dashboard (use `python -m streamlit` on Windows/PowerShell)
python -m streamlit run streamlit_app.py
```

### Dashboard Features
* **KPI Metric Cards**: Real-time counters for `pending`, `processing`, `completed`, `failed`, and `dead` jobs.
* **1-Click Enqueue Form**: Enqueue any shell command with custom retry limits directly from the browser.
* **Interactive Queue Table**: Filter jobs by state and view execution attempts, commands, and timestamps.
* **Dead Letter Queue (DLQ) Rescue**: Expand failed jobs to inspect error logs and click **Rescue & Retry** to re-enqueue them into the pending queue.

---

## 🧪 Engineering Rigor & Testing

QueueCTL is backed by a thorough **6-scenario automated test suite** built with `pytest`, covering race conditions, process crashes, and retry math.

```bash
# Run the complete test suite
python -m pytest -v
```

### Verified Test Scenarios
1. **Concurrency (`test_concurrency.py`)**: Spawns multiple threads claiming jobs concurrently; verifies zero duplicate claims and exactly-once execution.
2. **Crash Recovery (`test_crash_recovery.py`)**: Simulates a `SIGKILL` crash during execution; verifies background reaping returns stale jobs (`>20s`) to `pending`.
3. **Dead Letter Queue (`test_dlq.py`)**: Verifies attempt counting, failure quarantine, and `dlq retry` counter resets.
4. **Exponential Backoff (`test_db_backoff.py`)**: Validates timestamp calculation (`backoff_base ** attempts`) and retry delay enforcement.
5. **Job Lifecycle (`test_db.py`)**: Tests full `enqueue -> claim -> processing -> completed` database state transitions.

---


## 📚 Documentation & Technical Defense

For reviewers, technical interviewers, and live defense sessions, QueueCTL includes two comprehensive reference documents:
* **[`DECISIONS.md`](./DECISIONS.md)**: Details the core architectural trade-offs, explaining why SQLite WAL mode was selected over Redis/RabbitMQ, how cross-process atomicity works (`BEGIN IMMEDIATE`), and how background heartbeats solve process crashes (`SIGKILL`).
* **[`LIVE_REVIEW_GUIDE.md`](./LIVE_REVIEW_GUIDE.md)**: A complete study guide and 60-second elevator pitch designed to assist in presenting and defending every line of code during a technical review.

---

## 📁 Project Structure

```text
flam-queuectl/
├── queuectl/
│   ├── __init__.py         # Package initialization
│   ├── cli.py              # CLI argument parsing & command handlers
│   ├── db.py               # SQLite WAL engine, atomic queries, & config
│   └── worker.py           # Multi-process worker loop, heartbeats, & reaping
├── tests/
│   ├── test_concurrency.py # Multi-threaded race condition tests
│   ├── test_crash_recovery.py # SIGKILL & stale job reaping tests
│   ├── test_db.py          # CRUD & lifecycle unit tests
│   ├── test_db_backoff.py  # Exponential backoff math tests
│   └── test_dlq.py         # DLQ routing & recovery tests
├── DECISIONS.md            # Technical architecture & trade-off rationale
├── LIVE_REVIEW_GUIDE.md    # Executive study guide & design defense notes
├── streamlit_app.py        # Streamlit interactive web dashboard
├── pyproject.toml          # Project configuration & CLI script bindings
└── README.md               # Documentation (this file)
```

---

## 📄 License
This project is open-source and licensed under the **MIT License**.
