"""Tests for the async 3D API routes (offloaded to a process-pool worker)."""

import asyncio
import time

import httpx
import pytest
import server
from core import part3d
from core.document import Document, path, rect

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


def test_sessions_are_isolated():
    """H2: different X-Session-Id headers get isolated documents (review.md §6.2)."""
    c = client(server.app)
    # Session "alpha" gets an object, session "beta" must not see it, and the
    # default (no header) document must be independent of both.
    a = c.post("/api/objects", json={"id": "o1", "type": "rect",
                                     "x": 0, "y": 0, "geometry": {"w": 10, "h": 10}},
              headers={"X-Session-Id": "alpha"})
    assert a.status_code == 200
    beta = c.get("/api/doc", headers={"X-Session-Id": "beta"})
    assert beta.status_code == 200
    assert all(o["id"] != "o1" for o in beta.json()["objects"])
    default = c.get("/api/doc")
    assert all(o["id"] != "o1" for o in default.json()["objects"])
    # The same session keeps its object across requests.
    alpha = c.get("/api/doc", headers={"X-Session-Id": "alpha"})
    assert any(o["id"] == "o1" for o in alpha.json()["objects"])


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


def test_3d_build_quality_is_echoed():
    c = client(server.app)
    r = c.post("/api/3d/extrude", json={"depth": 10, "quality": "low"})
    assert r.status_code == 200
    j = r.json()
    assert j.get("quality") == "low"


def test_3d_build_revolve_returns_auto_axis():
    c = client(server.app)
    # No explicit axis => the effective (auto) axis is computed and returned so the
    # viewer can draw it even for an un-set axis.
    r = c.post("/api/3d/extrude", json={"op": "revolve", "angle": 360})
    assert r.status_code == 200
    j = r.json()
    assert j["axis"] is not None


def test_3d_build_sweep_requires_an_open_path():
    c = client(server.app)
    # Swap in a doc with a closed profile but NO open path: a clear 400 either way
    # (ValueError "needs an open path" with build123d, or the build123d-missing msg).
    saved = server.store.get()
    try:
        server.store.set_default(Document())
        server.store.get().add(rect(300, 250, 200, 120))
        r = c.post("/api/3d/extrude", json={"op": "sweep"})
        assert r.status_code == 400
    finally:
        server.store.set_default(saved)


@pytest.mark.skipif(not build123d_present, reason="build123d not installed")
def test_3d_build_and_export_sweep():
    c = client(server.app)
    # Swap in a doc that has a closed profile + an open path, then sweep it.
    saved = server.store.get()
    try:
        server.store.set_default(Document())
        server.store.get().add(rect(300, 250, 200, 120))
        server.store.get().add(path(0, 0, "M0,0 L80,0 L80,120 L200,120", stroke="#000"))
        r = c.post("/api/3d/extrude", json={"op": "sweep", "quality": "medium"})
        assert r.status_code == 200
        j = r.json()
        assert j["op"] == "sweep"
        assert j["triangles"] > 0

        e = c.post("/api/3d/export",
                   json={"op": "sweep", "format": "stl", "quality": "medium"})
        assert e.status_code == 200
        assert len(e.content) > 0
    finally:
        server.store.set_default(saved)


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


# --------------------------------------------------------------------------
# Generative 3D primitives + multi-object scene merge export
# --------------------------------------------------------------------------
def test_3d_primitive_returns_mesh_for_every_kind():
    c = client(server.app)
    for kind in ("box", "sphere", "cylinder", "cone", "torus", "capsule", "pyramid"):
        r = c.post("/api/3d/primitive",
                   json={"kind": kind, "params": {}, "quality": "medium"})
        assert r.status_code == 200, kind
        j = r.json()
        assert j["kind"] == kind
        assert j["triangles"] > 0
        assert "bbox" in j and "positions" in j and "indices" in j
        assert "build123d" in j


def test_3d_primitive_rejects_unknown_kind():
    c = client(server.app)
    r = c.post("/api/3d/primitive", json={"kind": "not-a-primitive"})
    assert r.status_code == 400


def test_3d_export_scene_merge_primitives_stl():
    """Several primitives merge into one STL regardless of build123d presence."""
    c = client(server.app)
    items = [
        {"source": {"type": "primitive", "kind": "box",
                    "params": {"w": 40, "h": 40, "d": 40}, "quality": "medium"},
         "transform": {"pos": {"x": 50, "y": 0, "z": 0}, "rot": {"x": 0, "y": 0, "z": 0}, "scale": 1}},
        {"source": {"type": "primitive", "kind": "sphere",
                    "params": {"r": 25}, "quality": "medium"},
         "transform": {"pos": {"x": -50, "y": 0, "z": 10}, "rot": {"x": 0, "y": 45, "z": 0}, "scale": 1.5}},
    ]
    r = c.post("/api/3d/export", json={"format": "stl", "items": items})
    assert r.status_code == 200
    assert len(r.content) > 0


@pytest.mark.skipif(not build123d_present, reason="build123d not installed")
def test_3d_export_scene_merge_primitives_step():
    c = client(server.app)
    items = [
        {"source": {"type": "primitive", "kind": "box",
                    "params": {"w": 40, "h": 40, "d": 40}, "quality": "medium"},
         "transform": {"pos": {"x": 0, "y": 0, "z": 0}, "rot": {"x": 0, "y": 0, "z": 0}, "scale": 1}},
    ]
    r = c.post("/api/3d/export", json={"format": "step", "items": items})
    assert r.status_code == 200
    assert len(r.content) > 0


@pytest.mark.skipif(not build123d_present, reason="build123d not installed")
def test_3d_export_scene_merge_sketch_and_primitive():
    """A sketch-extrude item and a primitive item fuse into one STL."""
    c = client(server.app)
    saved = server.store.get()
    try:
        server.store.set_default(Document())
        server.store.get().add(rect(300, 250, 200, 120))
        items = [
            {"source": {"type": "sketch", "op": "extrude", "ids": None,
                        "depth": 20, "quality": "low"},
             "transform": {"pos": {"x": 0, "y": 0, "z": 0}, "rot": {"x": 0, "y": 0, "z": 0}, "scale": 1}},
            {"source": {"type": "primitive", "kind": "cylinder",
                        "params": {"r": 15, "h": 30}, "quality": "low"},
             "transform": {"pos": {"x": 100, "y": 0, "z": 0}, "rot": {"x": 0, "y": 0, "z": 0}, "scale": 1}},
        ]
        r = c.post("/api/3d/export", json={"format": "stl", "items": items})
        assert r.status_code == 200
        assert len(r.content) > 0
    finally:
        server.store.set_default(saved)

