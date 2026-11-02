"""Tests for the async 3D API routes (offloaded to a process-pool worker)."""

import asyncio
import time

import httpx
import pytest
import server
from core import part3d

# STEP and Revolve export require the Open Cascade kernel (build123d). The base CI
# job runs without it, so those export tests are skipped there; the accurate path
# is still exercised by the dedicated test-3d CI job (which installs build123d).
build123d_present = part3d.available()


def client(app):
    """Sync ASGI test client built on httpx.

    We avoid starlette.testclient here: recent Starlette's TestClient requires the
    `httpx2` shim, whereas driving the app through httpx.ASGITransport works on
    any current Starlette/httpx and needs no extra package. httpx.ASGITransport is
    async-only, so each call spins up an AsyncClient via asyncio.run; the returned
    httpx.Response still exposes .status_code / .json() / .content synchronously.
    """

    class _ASGIClient:
        def __init__(self, app):
            self.app = app

        def _request(self, method, url, **kwargs):
            async def go():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.app),
                    base_url="http://test",
                ) as ac:
                    return await ac.request(method, url, **kwargs)

            return asyncio.run(go())

        def post(self, url, **kwargs):
            return self._request("POST", url, **kwargs)

        def get(self, url, **kwargs):
            return self._request("GET", url, **kwargs)

    return _ASGIClient(app)


def _slow_job():
    """Picklable helper used to exercise the worker job timeout."""
    time.sleep(2.0)
    return "done"


def test_3d_build_extrude():
    c = client(server.app)
    r = c.post("/api/3d/extrude", json={"depth": 10})
    assert r.status_code == 200
    j = r.json()
    assert j["op"] == "extrude"
    assert j["triangles"] > 0
    assert j["depth"] == 10


def test_3d_build_revolve():
    c = client(server.app)
    r = c.post("/api/3d/extrude", json={"op": "revolve", "angle": 360})
    assert r.status_code == 200
    j = r.json()
    assert j["op"] == "revolve"
    assert j["triangles"] > 0
    # Revolve meshes report the sweep angle and the axis that was used.
    assert j["angle"] == 360
    assert "axis" in j


def test_3d_build_rejects_bad_angle():
    c = client(server.app)
    r = c.post("/api/3d/extrude", json={"op": "revolve", "angle": 400})
    assert r.status_code == 400


def test_3d_build_revolve_accepts_axis():
    c = client(server.app)
    r = c.post("/api/3d/extrude", json={"op": "revolve", "angle": 360, "axis": 100})
    assert r.status_code == 200
    j = r.json()
    assert j["op"] == "revolve"
    assert j["triangles"] > 0
    assert j["axis"] == 100


def test_3d_build_rejects_bad_axis():
    c = client(server.app)
    r = c.post("/api/3d/extrude", json={"op": "revolve", "angle": 360, "axis": "abc"})
    assert r.status_code == 400


@pytest.mark.skipif(not build123d_present, reason="build123d not installed")
def test_3d_export_revolve_stl_accepts_axis():
    c = client(server.app)
    r = c.post("/api/3d/export", json={"op": "revolve", "angle": 360, "axis": 50, "format": "stl"})
    assert r.status_code == 200
    assert len(r.content) > 0


def test_3d_export_stl():
    c = client(server.app)
    r = c.post("/api/3d/export", json={"depth": 10, "format": "stl"})
    assert r.status_code == 200
    assert len(r.content) > 0


@pytest.mark.skipif(not build123d_present, reason="build123d not installed")
def test_3d_export_step():
    c = client(server.app)
    r = c.post("/api/3d/export", json={"depth": 10, "format": "step"})
    assert r.status_code == 200
    assert len(r.content) > 0


@pytest.mark.skipif(not build123d_present, reason="build123d not installed")
def test_3d_export_revolve_stl():
    c = client(server.app)
    r = c.post("/api/3d/export", json={"op": "revolve", "angle": 360, "format": "stl"})
    assert r.status_code == 200
    assert len(r.content) > 0


def test_3d_build_returns_build123d_flag():
    c = client(server.app)
    r = c.post("/api/3d/extrude", json={"depth": 10})
    assert r.status_code == 200
    j = r.json()
    assert "build123d" in j
    assert j["build123d"] is part3d.available()


def test_worker_offloads_to_process_pool():
    """Heavy 3D work runs in a real worker *process*, not the event loop thread."""
    from core import worker3d
    from core.document import Document, rect

    d = Document()
    d.add(rect(300, 250, 200, 120))
    mesh = asyncio.run(
        worker3d.run(part3d.extrude_to_mesh, d.to_dict(), None, 10.0, True)
    )
    assert mesh["triangles"] > 0
    # A process pool was created and used (or a thread fallback if multiprocessing
    # is unavailable — either way the request completes off the event loop).
    assert worker3d.executor_kind() in ("process", "thread")


def test_worker_job_timeout_is_not_retried():
    """A job that exceeds the timeout raises JobTimeoutError instead of hanging
    the event-loop thread (it must NOT be retried in-process)."""
    from core import worker3d

    # _slow_job is module-level (picklable) so this exercises the process pool;
    # on a thread-fallback machine the timeout still applies via wait_for.
    with pytest.raises(worker3d.JobTimeoutError):
        asyncio.run(worker3d.run(_slow_job, timeout=0.2))


def test_worker_shutdown_is_safe_and_idempotent():
    """shutdown() tears down the pool without error and can be called repeatedly."""
    from core import worker3d

    # Ensure a pool exists first.
    worker3d.get_executor()
    worker3d.shutdown()
    # Calling again must be safe (idempotent teardown).
    worker3d.shutdown()
    # A subsequent job recreates the pool (or uses the thread fallback).
    worker3d.get_executor()
    assert worker3d.executor_kind() in ("process", "thread")

