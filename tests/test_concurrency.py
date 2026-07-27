import concurrent.futures

from queuectl import db


def test_concurrent_claims_no_duplicate(tmp_path, monkeypatch):
    """
    Test Scenario 3: Many jobs across multiple workers — every job runs exactly once.
    Verify that when multiple threads/workers race to call claim_job() concurrently,
    no two workers claim the same job ID.
    """
    monkeypatch.setenv("QUEUECTL_HOME", str(tmp_path))
    db.init_db()

    num_jobs = 25
    for i in range(num_jobs):
        db.enqueue_job(f"job-{i}", "echo test", max_retries=1)

    claimed_job_ids = []

    def _worker_task(worker_num):
        worker_id = f"worker-{worker_num}"
        my_claims = []
        while True:
            job = db.claim_job(worker_id)
            if job is None:
                break
            my_claims.append(job["id"])
            db.mark_completed(job["id"])
        return my_claims

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_worker_task, w) for w in range(5)]
        for f in concurrent.futures.as_completed(futures):
            claimed_job_ids.extend(f.result())

    # Verify every job was claimed exactly once
    assert len(claimed_job_ids) == num_jobs
    assert len(set(claimed_job_ids)) == num_jobs

    completed_jobs = db.list_jobs("completed")
    assert len(completed_jobs) == num_jobs
