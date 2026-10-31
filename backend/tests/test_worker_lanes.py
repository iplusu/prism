"""A long AI task must not hold up a compile: job kinds are taken by separate worker threads."""

import threading
import time

from app import worker
from app.db import SessionLocal
from app.models import Job
from conftest import new_sample_project


def test_lanes_cover_every_job_kind_once():
    kinds = [k for lane in worker.LANES for k in lane]
    assert sorted(kinds) == ["agent", "apply_proposal", "compile", "verify_proposal"]


def test_compile_finishes_while_an_agent_job_is_still_running(alice, monkeypatch):
    pid = new_sample_project(alice)["id"]
    release, started = threading.Event(), threading.Event()
    real_handle = worker.handle

    def handle(job):
        if job.kind == "agent":  # stands in for a task that takes minutes
            started.set()
            release.wait(120)
            return
        real_handle(job)

    monkeypatch.setattr(worker, "handle", handle)
    db = SessionLocal()
    try:
        db.add(Job(kind="agent", project_id=pid, ref_id="no-such-task"))
        db.commit()
    finally:
        db.close()

    stop = threading.Event()
    monkeypatch.setattr(worker, "_stop", stop)
    threads = [threading.Thread(target=worker._lane_loop, args=(lane, 0.05), daemon=True) for lane in worker.LANES]
    for t in threads:
        t.start()
    try:
        assert started.wait(30), "the agent job was never picked up"
        r = alice.post(f"/api/projects/{pid}/builds")
        assert r.status_code in (200, 201, 202), r.text
        bid = r.json()["id"]
        status = ""
        for _ in range(240):
            status = alice.get(f"/api/builds/{bid}").json()["status"]
            if status not in ("queued", "running"):
                break
            time.sleep(0.5)
        assert status == "succeeded", status
        assert not release.is_set()  # the agent job was still running when the compile finished
    finally:
        release.set()
        stop.set()
        for t in threads:
            t.join(30)
