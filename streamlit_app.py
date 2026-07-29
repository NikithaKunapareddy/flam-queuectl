import os
import shutil
import subprocess
import sys
import pandas as pd
import streamlit as st
from queuectl import db

st.set_page_config(
    page_title="QueueCTL Full-Stack Engine Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("⚡ QueueCTL Full-Stack Engine & Command Control Center")
db.init_db()

# ==============================================================================
# SIDEBAR: PERSISTENT CONFIG, DATABASE RESET & TEST SUITE RUNNER
# ==============================================================================
with st.sidebar:
    st.header("⚙️ Runtime Configuration")
    current_retries = int(db.get_config("max_retries", "3"))
    current_backoff = int(db.get_config("backoff_base", "2"))

    with st.form("config_form"):
        new_retries = st.number_input(
            "Max Retries (`max_retries`)", min_value=1, max_value=20, value=current_retries
        )
        new_backoff = st.number_input(
            "Backoff Base (`backoff_base`)",
            min_value=1,
            max_value=10,
            value=current_backoff,
        )
        save_config = st.form_submit_button("💾 Update Config", use_container_width=True)

        if save_config:
            db.set_config("max_retries", str(new_retries))
            db.set_config("backoff_base", str(new_backoff))
            st.success("Config updated in SQLite!")
            st.rerun()

    st.divider()
    st.header("🧪 Engineering Test Suite")
    if st.button("▶️ Run `pytest -v` (6 Scenarios)", use_container_width=True):
        with st.spinner("Running automated test suite..."):
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-v"],
                capture_output=True,
                text=True,
            )
            st.session_state["pytest_output"] = result.stdout + "\n" + result.stderr

    if "pytest_output" in st.session_state:
        st.code(st.session_state["pytest_output"], language="text")

    st.divider()
    st.header("🗑️ Database Management")
    if st.button("⚠️ Wipe & Reset `.queuectl` DB", use_container_width=True):
        db_dir = ".queuectl"
        if os.path.exists(db_dir):
            shutil.rmtree(db_dir, ignore_errors=True)
        db.init_db()
        st.success("Database completely reset to clean state!")
        st.rerun()

# ==============================================================================
# TOP KPI METRICS BAR (`queuectl status`)
# ==============================================================================
counts = db.counts_by_state()
col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("🟡 Pending", counts.get("pending", 0))
col2.metric("🔵 Processing", counts.get("processing", 0))
col3.metric("🟢 Completed", counts.get("completed", 0))
col4.metric("🟠 Failed", counts.get("failed", 0))
col5.metric(
    "🔴 Dead (DLQ)",
    counts.get("dead", 0),
    delta_color="inverse" if counts.get("dead", 0) > 0 else "normal",
)

# Active workers check
worker_dir = ".queuectl/workers"
active_workers = (
    len(os.listdir(worker_dir)) if os.path.exists(worker_dir) else 0
)
col6.metric("⚙️ Active Workers", active_workers)

st.divider()

# ==============================================================================
# MAIN TABS: QUEUE CONTROL | WORKER ENGINE | DEAD LETTER QUEUE
# ==============================================================================
tab1, tab2, tab3 = st.tabs(
    [
        "📋 Job Queue & Enqueue Commands",
        "⚙️ Worker Process Control Panel",
        "🚨 Dead Letter Queue (DLQ) Manager",
    ]
)

# ------------------------------------------------------------------------------
# TAB 1: JOB QUEUE & ENQUEUE
# ------------------------------------------------------------------------------
with tab1:
    st.subheader("⚡ Quick-Fill Demo Command Enqueue")
    st.write(
        "Click a button below to instantly enqueue the standard test suite jobs:"
    )
    d1, d2, d3 = st.columns(3)

    with d1:
        if st.button("➕ Enqueue `job-hello` (echo Hello)", use_container_width=True):
            db.enqueue_job("job-hello", "echo Hello from QueueCTL", 3)
            st.success("Enqueued `job-hello`!")
            st.rerun()
    with d2:
        if st.button("➕ Enqueue `job-sleep` (ping delay)", use_container_width=True):
            db.enqueue_job("job-sleep", "ping 127.0.0.1 -n 3", 3)
            st.success("Enqueued `job-sleep`!")
            st.rerun()
    with d3:
        if st.button(
            "➕ Enqueue `job-fail` (cmd /c exit 1)", use_container_width=True
        ):
            db.enqueue_job("job-fail", "cmd /c exit 1", 1)
            st.success("Enqueued `job-fail`!")
            st.rerun()

    st.divider()

    left, right = st.columns([1, 2])
    with left:
        st.subheader("📝 Custom Job Enqueue Form")
        with st.form("custom_enqueue_form"):
            custom_id = st.text_input("Job ID", value="demo-custom-job")
            custom_cmd = st.text_input(
                "Shell Command", value="echo Hello Custom Job"
            )
            custom_retries = st.number_input(
                "Max Retries", min_value=1, max_value=10, value=3
            )
            submit_custom = st.form_submit_button(
                "Enqueue Custom Job", use_container_width=True
            )

            if submit_custom and custom_id and custom_cmd:
                db.enqueue_job(custom_id, custom_cmd, custom_retries)
                st.success(f"Enqueued `{custom_id}` successfully!")
                st.rerun()

    with right:
        st.subheader("📋 SQLite Jobs List (`queuectl list`)")
        state_filter = st.selectbox(
            "Filter by Job State",
            ["pending", "processing", "completed", "failed", "dead", "all"],
            index=0,
        )

        all_jobs = (
            db.list_jobs(state_filter)
            if state_filter != "all"
            else db.list_jobs("pending")
            + db.list_jobs("processing")
            + db.list_jobs("completed")
            + db.list_jobs("failed")
            + db.list_jobs("dead")
        )

        if all_jobs:
            df = pd.DataFrame(all_jobs)
            st.dataframe(df, use_container_width=True)
        else:
            st.info(f"No jobs found with state: `{state_filter}`")

# ------------------------------------------------------------------------------
# TAB 2: WORKER PROCESS CONTROL PANEL (`worker start` / `worker stop`)
# ------------------------------------------------------------------------------
with tab2:
    st.subheader("⚙️ Multi-Process Worker Engine Management")
    st.write(
        "Control OS background worker processes directly from the browser interface."
    )

    w1, w2 = st.columns([1, 1])

    with w1:
        st.markdown("#### ▶️ Start Worker Engine (`worker start`)")
        worker_count = st.slider("Number of Worker Processes to Spawn", 1, 4, 1)
        if st.button(
            f"🚀 Start {worker_count} Worker Process(es)",
            use_container_width=True,
            type="primary",
        ):
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "queuectl.cli",
                    "worker",
                    "start",
                    "--count",
                    str(worker_count),
                ],
                creationflags=(
                    subprocess.CREATE_NEW_CONSOLE
                    if os.name == "nt"
                    else 0
                ),
            )
            st.success(
                f"Spawned {worker_count} worker(s) in background! Watch the Queue table update as jobs complete."
            )
            st.rerun()

    with w2:
        st.markdown("#### ⏹️ Stop All Workers (`worker stop`)")
        st.write(
            "Send graceful termination signals to all active background worker PIDs."
        )
        if st.button("🛑 Stop All Active Workers", use_container_width=True):
            subprocess.run(
                [sys.executable, "-m", "queuectl.cli", "worker", "stop"]
            )
            st.success("Sent termination signals to workers.")
            st.rerun()

    st.divider()
    st.subheader("💡 Worker Execution Instructions")
    st.info(
        "On Windows, clicking **Start Worker Process(es)** spawns a clean background worker window that automatically processes jobs in the pending queue and exits when done. Refresh or interact with the page to see job states transition from `pending` -> `processing` -> `completed`!"
    )

# ------------------------------------------------------------------------------
# TAB 3: DEAD LETTER QUEUE (DLQ) MANAGER (`dlq list` & `dlq retry`)
# ------------------------------------------------------------------------------
with tab3:
    st.subheader("🚨 Dead Letter Queue (DLQ) Quarantine Area")
    st.write(
        "Jobs that exceed their `max_retries` are quarantined here to prevent infinite crash loops."
    )

    dead_jobs = db.list_jobs("dead")

    if not dead_jobs:
        st.success("✅ Zero failed jobs in Dead Letter Queue!")
    else:
        for djob in dead_jobs:
            with st.expander(
                f"❌ {djob['id']} — Failed: {djob['last_error']}", expanded=True
            ):
                st.code(
                    f"Command: {djob['command']}\nAttempts: {djob['attempts']}/{djob['max_retries']}\nCreated At: {djob['created_at']}\nUpdated At: {djob['updated_at']}"
                )
                if st.button(
                    f"🔄 Rescue & Retry `{djob['id']}` (`dlq retry`)",
                    key=f"retry_{djob['id']}",
                    use_container_width=True,
                ):
                    db.dlq_retry(djob["id"])
                    st.success(
                        f"Rescued `{djob['id']}` back to Pending queue with attempts reset to 0/1!"
                    )
                    st.rerun()
