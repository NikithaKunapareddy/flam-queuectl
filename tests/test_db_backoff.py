import time

from queuectl import db


def test_mark_failed_and_backoff(tmp_path, monkeypatch):
    monkeypatch.setenv("QUEUECTL_HOME", str(tmp_path))
    db.init_db()

    # enqueue with max_retries=3
    db.enqueue_job("b1", "false", max_retries=3)

    # claim and mark failed once
    w = "w1"
    job = db.claim_job(w)
    assert job is not None
    db.mark_failed(job["id"], "first error")

    rows = db.list_jobs()
    j = next(r for r in rows if r["id"] == "b1")
    assert j["state"] == "failed"
    assert j["attempts"] == 1
    assert j["next_retry_at"] is not None

    # simulate waiting past next_retry_at and claim again
    # backoff may be fractional; sleep a small amount to ensure time passes
    time.sleep(0.1)
    _ = db.reap_stale_jobs()
    # attempt to claim after backoff (may return None if next_retry_at still in future)
    # mark failed twice more to exhaust retries
    # we directly call mark_failed to simulate multiple failures
    db.mark_failed("b1", "second error")
    db.mark_failed("b1", "third error")

    j2 = next(r for r in db.list_jobs() if r["id"] == "b1")
    assert j2["state"] == "dead"
    assert j2["attempts"] >= 3
