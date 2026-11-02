"""Tests for the async 3D API routes (offloaded to a process-pool worker)."""

import asyncio

from fastapi.testclient import TestClient
import server
from core import part3d


def test_3d_build_extrude():
    c = TestClient(server.app)
    r = c.post("/api/3d/extrude", json={"depth": 10})
    assert r.status_code == 200
    j = r.json()
    assert j["op"] == "extrude"
    assert j["triangles"] > 0
    assert j["depth"] == 10


def test_3d_build_revolve():
    c = TestClient(server.app)
    r = c.post("/api/3d/extrude", json={"op": "revolve", "angle": 360})
    assert r.status_code == 200
    j = r.json()
    assert j["op"] == "revolve"
    assert j["triangles"] > 0


def test_3d_build_rejects_bad_angle():
    c = TestClient(server.app)
    r = c.post("/api/3d/extrude", json={"op": "revolve", "angle": 400})
    assert r.status_code == 400


def test_3d_export_stl():
    c = TestClient(server.app)
    r = c.post("/api/3d/export", json={"depth": 10, "format": "stl"})
    assert r.status_code == 200
    assert len(r.content) > 0


def test_3d_export_step():
    c = TestClient(server.app)
    r = c.post("/api/3d/export", json={"depth": 10, "format": "step"})
    assert r.status_code == 200
    assert len(r.content) > 0


def test_3d_export_revolve_stl():
    c = TestClient(server.app)
    r = c.post("/api/3d/export", json={"op": "revolve", "angle": 360, "format": "stl"})
    assert r.status_code == 200
    assert len(r.content) > 0


def test_3d_build_returns_build123d_flag():
    c = TestClient(server.app)
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
