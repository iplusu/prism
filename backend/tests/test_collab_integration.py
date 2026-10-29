"""Collaborative editing, end to end, with real processes:

  backend (uvicorn, in a thread)  <->  collab service (node server.mjs)  <->  3 y-websocket clients (node)

Covers: two clients editing concurrently, AI proposal applied while both are
connected, conflict detection against live collaborative state, read-only
viewers, offline edits + reconnection, persistence across a collab restart.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from app.db import SessionLocal
from app.models import Project
from app.projects import documents
from app.proposals import service
from conftest import drain, login, new_sample_project, read_text, save_text, set_env

COLLAB = Path(__file__).resolve().parents[2] / "collab"
pytestmark = pytest.mark.skipif(not shutil.which("node") or not (COLLAB / "node_modules").is_dir(),
                                reason="node or collab dependencies missing")
SECRET = "collab-test-secret-123456"


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_http(url, timeout=20):
    import httpx

    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"{url} did not come up")


class Clients:
    def __init__(self, args):
        self.p = subprocess.Popen(["node", str(COLLAB / "test" / "clients.mjs"), *args], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=COLLAB)
        assert self.read()["phase"] == "connected"

    def read(self, timeout=60):
        import select

        r, _, _ = select.select([self.p.stdout], [], [], timeout)
        if not r:
            self.p.kill()
            raise RuntimeError("clients timed out; stderr: " + self.p.stderr.read()[-3000:])
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError("clients exited: " + self.p.stderr.read()[-3000:])
        return json.loads(line)

    def cmd(self, c):
        self.p.stdin.write(c + "\n")
        self.p.stdin.flush()
        return self.read()

    def close(self):
        try:
            self.cmd("exit")
        except Exception:
            pass
        self.p.kill()


@pytest.fixture
def stack(tmp_path):
    bport, cport = _port(), _port()
    set_env(COLLAB_ENABLED="true", COLLAB_INTERNAL_URL=f"http://127.0.0.1:{cport}",
            COLLAB_PUBLIC_URL=f"ws://127.0.0.1:{cport}/collab", COLLAB_SHARED_SECRET=SECRET)
    from app.main import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=bport, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    _wait_http(f"http://127.0.0.1:{bport}/api/health")
    env = {**os.environ, "COLLAB_PORT": str(cport), "COLLAB_SHARED_SECRET": SECRET,
           "BACKEND_INTERNAL_URL": f"http://127.0.0.1:{bport}", "COLLAB_DATA_DIR": str(tmp_path / "collab"),
           "COLLAB_WRITE_THROUGH_MS": "200"}

    def start_collab():
        p = subprocess.Popen(["node", str(COLLAB / "server.mjs")], env=env, cwd=COLLAB,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        _wait_http(f"http://127.0.0.1:{cport}/health")
        return p

    state = {"collab": start_collab(), "start": start_collab, "cport": cport, "bport": bport}
    yield state
    state["collab"].terminate()
    state["collab"].wait(10)
    server.should_exit = True
    th.join(10)


def test_two_clients_and_ai_apply(stack):
    alice = login("alice")
    bob = login("bob")
    carol = login("carol")
    p = new_sample_project(alice, "Collab paper")
    pid = p["id"]
    alice.post(f"/api/projects/{pid}/members", json={"username": "bob", "role": "editor"})
    alice.post(f"/api/projects/{pid}/members", json={"username": "carol", "role": "viewer"})
    ta = alice.get(f"/api/projects/{pid}/collab-token").json()
    tb = bob.get(f"/api/projects/{pid}/collab-token").json()
    tv = carol.get(f"/api/projects/{pid}/collab-token").json()
    assert ta["enabled"] and tv["readonly"] is True
    f = "sections/conclusion.tex"
    cl = Clients([ta["url"], pid, f, ta["token"], tb["token"], tv["token"]])
    try:
        # 1. two clients edit simultaneously -> both edits survive, states converge
        r = cl.cmd("concurrent")
        assert r["converged"], r
        live = read_text(alice, pid, f)["text"]
        assert "edit-from-A" in live and "edit-from-B" in live  # backend sees the live collaborative state

        # 2. AI proposal applied while both clients are connected
        db = SessionLocal()
        try:
            snap = documents.snapshot(pid, message="base for AI change")
            new = snap.files[f].decode().replace("is future work.", "is left for future work.")
            prop = service.create(db, db.get(Project, pid), base_revision=snap.revision, base_files=snap.files,
                                  changes={f: new.encode()}, origin="agent", title="AI wording")
            prop_id = prop.id
        finally:
            db.close()
        drain()
        assert alice.post(f"/api/proposals/{prop_id}/apply").status_code == 200
        drain()
        assert alice.get(f"/api/proposals/{prop_id}").json()["status"] == "applied"
        r = cl.cmd("await left for future work")
        assert r["seen"], r
        assert "edit-from-A" in r["a"] and "edit-from-B" in r["b"]  # user edits were not overwritten
        assert r["a"] == r["b"]

        # 3. AI change against a stale base conflicting with a live edit -> conflict, live text kept
        db = SessionLocal()
        try:
            snap = documents.snapshot(pid, message="base 2")
            new = snap.files[f].decode().replace("Extending the analysis", "Generalising the analysis")
            prop2 = service.create(db, db.get(Project, pid), base_revision=snap.revision, base_files=snap.files,
                                   changes={f: new.encode()}, origin="agent", title="AI wording 2").id
        finally:
            db.close()
        drain()
        cur = read_text(alice, pid, f)
        assert save_text(alice, pid, f, cur["text"].replace("Extending the analysis", "Broadening the analysis"),
                         cur["sha"]).status_code == 200  # goes through the collab service
        cl.cmd("await Broadening the analysis")
        alice.post(f"/api/proposals/{prop2}/apply")
        drain()
        assert alice.get(f"/api/proposals/{prop2}").json()["status"] == "conflict"
        assert "Broadening" in cl.cmd("dump")["text"]

        # 4. viewers are read-only
        assert cl.cmd("viewer-edit % viewer-was-here")["propagated"] is False

        # 5. offline edit + reconnect
        r = cl.cmd("offline-edit % written-while-B-offline")
        assert r["missingWhileOffline"] and r["caughtUp"], r
    finally:
        cl.close()

    # 6. persistence across a collab-service restart, and write-through to disk
    stack["collab"].terminate()
    stack["collab"].wait(10)
    stack["collab"] = stack["start"]()
    text = read_text(alice, pid, f)["text"]
    assert "written-while-B-offline" in text and "edit-from-A" in text and "left for future work" in text
    from app.projects import store

    time.sleep(1)
    disk = store.read_file(pid, f).decode()
    assert "written-while-B-offline" in disk


def test_bad_tokens_rejected(stack):
    import httpx

    alice = login("alice")
    pid = new_sample_project(alice, "x")["id"]
    other = new_sample_project(alice, "y")["id"]
    tok = alice.get(f"/api/projects/{pid}/collab-token").json()["token"]
    port = stack["cport"]
    for path in (f"/collab/{other}?token={tok}", f"/collab/{pid}?token={tok[:-2]}xx", f"/collab/{pid}"):
        with socket.create_connection(("127.0.0.1", port)) as s:
            s.sendall((f"GET {path} HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            assert s.recv(100).startswith(b"HTTP/1.1 401")
    assert httpx.get(f"http://127.0.0.1:{port}/internal/projects/{pid}/texts").status_code == 403


def test_websocket_through_backend_proxy(stack):
    """Same flow as the browser when COLLAB_PUBLIC_URL is empty: ws://<web>/collab/<room>."""
    alice = login("alice")
    bob = login("bob")
    carol = login("carol")
    pid = new_sample_project(alice, "Proxy paper")["id"]
    alice.post(f"/api/projects/{pid}/members", json={"username": "bob", "role": "editor"})
    alice.post(f"/api/projects/{pid}/members", json={"username": "carol", "role": "viewer"})
    toks = [c.get(f"/api/projects/{pid}/collab-token").json()["token"] for c in (alice, bob, carol)]
    url = f"ws://127.0.0.1:{stack['bport']}/collab"
    cl = Clients([url, pid, "sections/conclusion.tex", *toks])
    try:
        r = cl.cmd("concurrent")
        assert r["converged"], r
        assert cl.cmd("viewer-edit % proxied-viewer")["propagated"] is False
    finally:
        cl.close()
    assert "edit-from-B" in read_text(alice, pid, "sections/conclusion.tex")["text"]
    # a bad token is refused through the proxy as well
    import asyncio

    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    async def bad():
        async with connect(f"{url}/{pid}?token=forged.sig") as w:
            await w.recv()

    with pytest.raises((ConnectionClosed, Exception)):
        asyncio.run(bad())
