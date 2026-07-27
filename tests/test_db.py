from queuectl import db


def test_enqueue_and_list(tmp_path, monkeypatch):
    # isolate state to a temp directory
    monkeypatch.setenv("QUEUECTL_HOME", str(tmp_path))
    db.init_db()

    db.enqueue_job("j1", "echo hi", max_retries=2)
    jobs = db.list_jobs()
    assert any(j["id"] == "j1" for j in jobs)


def test_claim_and_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("QUEUECTL_HOME", str(tmp_path))
    db.init_db()
    db.enqueue_job("j2", "echo hi", max_retries=1)

    worker_id = "test-worker"
    job = db.claim_job(worker_id)
    assert job is not None and job["state"] == "processing"
    db.mark_completed(job["id"])

    jobs = db.list_jobs("completed")
    assert any(j["id"] == "j2" for j in jobs)
