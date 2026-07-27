from queuectl import db


def test_dlq_retry_resets_attempts(tmp_path, monkeypatch):
    monkeypatch.setenv("QUEUECTL_HOME", str(tmp_path))
    db.init_db()

    db.enqueue_job("d1", "false", max_retries=1)

    # simulate failure beyond retries
    db.mark_failed("d1", "f")

    j = next(r for r in db.list_jobs() if r["id"] == "d1")
    assert j["state"] in ("dead", "failed")

    # if it's not yet dead, call mark_failed to ensure it moves
    if j["state"] != "dead":
        db.mark_failed("d1", "f2")

    # now DLQ retry
    db.dlq_retry("d1")
    j2 = next(r for r in db.list_jobs() if r["id"] == "d1")
    assert j2["state"] == "pending"
    assert j2["attempts"] == 0
