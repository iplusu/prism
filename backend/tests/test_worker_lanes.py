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


def _queue(kind, project_id):
    db = SessionLocal()
    try:
        j = Job(kind=kind, project_id=project_id, ref_id=f"ref-{project_id}")
        db.add(j)
        db.commit()
        return j.id
    finally:
        db.close()


def _claim(kinds):
    from app import jobs

    db = SessionLocal()
    try:
        j = jobs.claim(db, kinds)
        return j.id if j else None
    finally:
        db.close()


def _finish_all():
    db = SessionLocal()
    try:
        for j in db.query(Job).filter(Job.status.in_(("queued", "running"))).all():
            j.status = "succeeded"
        db.commit()
    finally:
        db.close()


def test_each_project_has_its_own_queue():
    _finish_all()
    a1, a2 = _queue("agent", "proj-a"), _queue("agent", "proj-a")
    b1 = _queue("agent", "proj-b")
    assert _claim(["agent"]) == a1  # oldest first
    # project A's second job waits for its first; project B is not held up by A
    assert _claim(["agent"]) == b1
    assert _claim(["agent"]) is None
    db = SessionLocal()
    try:
        db.get(Job, a1).status = "succeeded"
        db.commit()
    finally:
        db.close()
    assert _claim(["agent"]) == a2  # A's next job runs once its first one is done
    _finish_all()


def test_a_projects_other_lane_is_not_held_up():
    _finish_all()
    agent = _queue("agent", "proj-c")
    compile_ = _queue("compile", "proj-c")
    assert _claim(["agent"]) == agent
    assert _claim(["compile"]) == compile_  # same project, different lane
    _finish_all()


def test_two_projects_agent_jobs_run_at_the_same_time(alice, monkeypatch):
    _finish_all()
    release = threading.Event()
    running, lock, peak = set(), threading.Lock(), [0]

    def handle(job):
        with lock:
            running.add(job.project_id)
            peak[0] = max(peak[0], len(running))
        release.wait(60)
        with lock:
            running.discard(job.project_id)

    monkeypatch.setattr(worker, "handle", handle)
    ids = [_queue("agent", "proj-d"), _queue("agent", "proj-d"), _queue("agent", "proj-e")]
    stop = threading.Event()
    monkeypatch.setattr(worker, "_stop", stop)
    threads = [threading.Thread(target=worker._lane_loop, args=(["agent"], 0.05), daemon=True) for _ in range(3)]
    for t in threads:
        t.start()
    try:
        for _ in range(100):
            if peak[0] >= 2:
                break
            time.sleep(0.1)
        time.sleep(1)  # give the third thread the chance to wrongly take project D's second job
        assert peak[0] == 2, f"expected exactly projects D and E at once, got {peak[0]}"
        db = SessionLocal()
        try:
            states = {j.id: j.status for j in db.query(Job).filter(Job.id.in_(ids)).all()}
        finally:
            db.close()
        assert list(states.values()).count("running") == 2 and list(states.values()).count("queued") == 1
    finally:
        release.set()
        for _ in range(100):
            db = SessionLocal()
            try:
                if not db.query(Job).filter(Job.id.in_(ids), Job.status != "succeeded").count():
                    break
            finally:
                db.close()
            time.sleep(0.2)
        stop.set()
        for t in threads:
            t.join(30)
