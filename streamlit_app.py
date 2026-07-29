import json
import pandas as pd
import streamlit as st
from queuectl import db

st.set_page_config(
    page_title="QueueCTL Background Job Monitor", page_icon="⚡", layout="wide"
)

st.title("⚡ QueueCTL Background Job Monitor & DLQ Manager")
db.init_db()

# Top KPI Metric Cards
counts = db.counts_by_state()
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Pending Jobs", counts.get("pending", 0))
col2.metric("Processing", counts.get("processing", 0))
col3.metric("Completed", counts.get("completed", 0))
col4.metric("Failed", counts.get("failed", 0))
col5.metric(
    "Dead (DLQ)",
    counts.get("dead", 0),
    delta_color="inverse" if counts.get("dead", 0) > 0 else "normal",
)

st.divider()

# Left Column: Enqueue Form | Right Column: Active Jobs Table
left, right = st.columns([1, 2])

with left:
    st.subheader("➕ Enqueue New Job")
    with st.form("enqueue_form"):
        job_id = st.text_input("Job ID", value="job-demo-1")
        command = st.text_input("Shell Command", value="echo Hello from Streamlit")
        max_retries = st.number_input(
            "Max Retries", min_value=1, max_value=10, value=3
        )
        submit = st.form_submit_button("Enqueue Job", use_container_width=True)

        if submit and job_id and command:
            db.enqueue_job(job_id, command, max_retries)
            st.success(f"Enqueued `{job_id}` successfully!")
            st.rerun()

with right:
    st.subheader("📋 Queue Status")
    state_filter = st.selectbox(
        "Filter by State", ["pending", "processing", "completed", "dead", "all"]
    )

    jobs = (
        db.list_jobs(state_filter)
        if state_filter != "all"
        else db.list_jobs("pending")
        + db.list_jobs("processing")
        + db.list_jobs("completed")
        + db.list_jobs("dead")
    )

    if jobs:
        df = pd.DataFrame(jobs)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No jobs found for this state.")

# Dead Letter Queue (DLQ) Management Section
st.divider()
st.subheader("🚨 Dead Letter Queue (DLQ) Quarantine")
dead_jobs = db.list_jobs("dead")

if not dead_jobs:
    st.success("No failed jobs in Dead Letter Queue!")
else:
    for djob in dead_jobs:
        with st.expander(f"❌ {djob['id']} — Failed: {djob['last_error']}"):
            st.code(
                f"Command: {djob['command']}\nAttempts: {djob['attempts']}/{djob['max_retries']}"
            )
            if st.button(f"🔄 Rescue & Retry {djob['id']}", key=djob["id"]):
                db.dlq_retry(djob["id"])
                st.success(f"Rescued {djob['id']} back to Pending queue!")
                st.rerun()
