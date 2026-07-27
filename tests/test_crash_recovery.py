import time

from queuectl import db


def test_stale_job_reaping(tmp_path, monkeypatch):
    """
    Test Scenario 4: Worker crash mid-job.
    When a worker is killed (SIGKILL) mid-job, locked_at stops being renewed.
    Once locked_at is older than STALE_LOCK_SECONDS, reap_stale_jobs() must
    revert the job to 'pending' so another worker can claim and complete it.
    """
    monkeypatch.setenv("QUEUECTL_HOME", str(tmp_path))
    db.init_db()

    # Enqueue a job
    db.enqueue_job("crash_job", "echo test", max_retries=3)

    # Claim the job as worker 1
    w1 = "worker-1"
    job = db.claim_job(w1)
    assert job is not None
    assert job["state"] == "processing"
    assert job["locked_by"] == w1

    # Simulate worker 1 crash by manually setting locked_at in the DB to 30 seconds ago
    conn = db._connect()
    stale_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30))
    conn.execute(
        "UPDATE jobs SET locked_at = ? WHERE id = ?",
        (stale_time, "crash_job"),
    )
    conn.close()

    # Call reap_stale_jobs()
    reclaimed_count = db.reap_stale_jobs()
    assert reclaimed_count == 1

    # Verify job is now pending again and unlocked
    jobs = db.list_jobs("pending")
    assert any(j["id"] == "crash_job" for j in jobs)
    reclaimed_job = next(j for j in jobs if j["id"] == "crash_job")
    assert reclaimed_job["locked_by"] is None
    assert reclaimed_job["locked_at"] is None

    # Worker 2 should now be able to claim it and mark it completed
    w2 = "worker-2"
    job2 = db.claim_job(w2)
    assert job2 is not None
    assert job2["id"] == "crash_job"
    assert job2["locked_by"] == w2

    db.mark_completed("crash_job")
    completed_jobs = db.list_jobs("completed")
    assert any(j["id"] == "crash_job" for j in completed_jobs)
