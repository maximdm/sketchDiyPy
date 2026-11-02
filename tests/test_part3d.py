"""Tests for the 3D extrusion module (core/part3d.py).

The pure-Python paths (outline extraction, prism mesh, STL writer) run with no
extra dependencies. The build123d-backed paths are skipped automatically if
build123d isn't installed.
"""

import math
import os
import tempfile

import pytest

from core import part3d
from core.document import Document, circle, line, path, rect


# --- helpers ---------------------------------------------------------------

def _scene():
    """A small doc: a rectangle, a circle, and an open guide line."""
    doc = Document()
    doc.add(rect(300, 250, 200, 120))           # 200x120 px rectangle
    doc.add(circle(620, 250, 60))               # circle, radius 60 px
    doc.add(line(150, 520, 750, 520))           # open line (no solid)
    return doc


# --- pure-python paths (no build123d needed) -------------------------------

def test_available_flag_is_bool():
    assert isinstance(part3d.available(), bool)


def test_cut_outlines_are_in_mm_and_closed():
    doc = _scene()
    outlines = part3d.doc_cut_outlines_mm(doc)
    # rect + circle are closed; the open line is dropped (not a face).
    closed = [o for o in outlines if o["closed"]]
    assert len(closed) == 2
    # default doc is mm with px_per_unit 3.78 -> rectangle 200x120 px ~ 52.9 x 31.7 mm
    rect_out = next(o for o in closed if len(o["pts"]) == 4)  # the rect, not the circle
    circ_out = next(o for o in closed if len(o["pts"]) > 4)   # the circle (64-gon)
    xs = [p[0] for p in rect_out["pts"]]
    ys = [p[1] for p in rect_out["pts"]]
    assert abs((max(xs) - min(xs)) - 200 / 3.78) < 0.5
    assert abs((max(ys) - min(ys)) - 120 / 3.78) < 0.5
    # circle radius 60 px -> diameter ~31.75 mm
    cxs = [p[0] for p in circ_out["pts"]]
    cys = [p[1] for p in circ_out["pts"]]
    assert abs((max(cxs) - min(cxs)) - 120 / 3.78) < 0.5


def test_prism_mesh_builds_a_closed_solid():
    doc = _scene()
    outlines = part3d.doc_cut_outlines_mm(doc)
    positions, indices = part3d._prism_mesh(outlines, depth=10.0)
    # rectangle: 4 verts -> 4 side tris + 2 cap tris per ring? total 12 tris.
    assert len(indices) % 3 == 0
    assert max(indices) < len(positions) // 3
    # z spans 0..depth
    zs = positions[2::3]
    assert min(zs) == 0.0
    assert max(zs) == 10.0


def test_stl_ascii_writer_roundtrip():
    doc = _scene()
    outlines = part3d.doc_cut_outlines_mm(doc)
    positions, indices = part3d._prism_mesh(outlines, depth=5.0)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "out.stl")
        part3d.write_stl_ascii(positions, indices, path)
        txt = open(path, encoding="ascii").read()
    assert txt.startswith("solid sketchDiy")
    assert txt.rstrip().endswith("endsolid sketchDiy")
    assert txt.count("facet normal") == len(indices) // 3


def test_extrude_to_mesh_fallback_when_build123d_absent(monkeypatch):
    monkeypatch.setattr(part3d, "available", lambda: False)
    doc = _scene()
    mesh = part3d.extrude_to_mesh(doc, depth=8.0, accurate=True)
    assert mesh["accurate"] is False
    assert mesh["triangles"] > 0
    assert mesh["bbox"]["size"][2] == 8.0  # extruded thickness


def test_extrude_to_mesh_rejects_open_only():
    doc = Document()
    doc.add(line(0, 0, 100, 100))  # only an open line -> nothing to extrude
    with pytest.raises(ValueError):
        part3d.extrude_to_mesh(doc, depth=5.0, accurate=False)


# --- build123d-backed paths (skipped if not installed) ---------------------

build123d_present = part3d.available()
pytestmark = pytest.mark.skipif(
    not build123d_present, reason="build123d not installed"
)


def test_solid_from_outlines_rect_with_hole_volume():
    # Build outlines directly in mm: a 50x30 plate with a 10mm-diameter hole.
    import build123d

    rect_pts = [(0, 0), (50, 0), (50, 30), (0, 30)]
    hole_pts = [
        (25 + 5 * math.cos(2 * math.pi * i / 48),
         15 + 5 * math.sin(2 * math.pi * i / 48))
        for i in range(48)
    ]
    outlines = [
        {"pts": rect_pts, "closed": True},
        {"pts": hole_pts, "closed": True},
    ]
    solid = part3d.solid_from_outlines(outlines, depth=10.0)
    # 50*30*10 - pi*5^2*10 = 15000 - 785.4 = 14214.6
    assert abs(solid.volume - (15000 - math.pi * 25 * 10)) < 5.0


def test_extrude_to_mesh_accurate_from_doc():
    doc = _scene()
    mesh = part3d.extrude_to_mesh(doc, depth=10.0, accurate=True)
    assert mesh["accurate"] is True
    assert mesh["triangles"] > 0
    assert mesh["bbox"]["size"][2] == 10.0


def test_export_step_and_stl_files():
    doc = _scene()
    outlines = part3d.doc_cut_outlines_mm(doc)
    solid = part3d.solid_from_outlines(outlines, depth=10.0)
    with tempfile.TemporaryDirectory() as d:
        stl = os.path.join(d, "m.stl")
        step = os.path.join(d, "m.step")
        part3d.write_stl(solid, stl)
        part3d.write_step(solid, step)
        assert os.path.getsize(stl) > 0
        assert os.path.getsize(step) > 0


# --- revolve -----------------------------------------------------------------

def test_revolve_surface_mesh_fallback_needs_no_build123d():
    outlines = [{"pts": [(0, 0), (20, 0), (20, 40), (0, 40)], "closed": True}]
    pos, idx = part3d._revolve_surface_mesh(outlines, 360, 0, 16)
    assert len(idx) % 3 == 0
    assert len(pos) >= 12  # at least the swept quads


def test_revolve_to_mesh_rejects_open_only():
    doc = Document()
    doc.add(line(0, 0, 100, 100))
    with pytest.raises(ValueError):
        part3d.revolve_to_mesh(doc, accurate=False)


@pytest.mark.skipif(not build123d_present, reason="build123d not installed")
def test_solid_from_revolution_cylinder_volume():
    # 1 x 10 (x) x 10 (y) rectangle revolved 360° about x=0 -> annular
    # cylinder, outer r=3, inner r=1, h=10 => volume pi*(9-1)*10 = 80*pi.
    outlines = [{"pts": [(1, 0), (3, 0), (3, 10), (1, 10)], "closed": True}]
    solid = part3d.solid_from_revolution(outlines, 360, 0)
    assert abs(solid.volume - (math.pi * 8 * 10)) < 3.0


@pytest.mark.skipif(not build123d_present, reason="build123d not installed")
def test_revolve_to_mesh_accurate_from_doc():
    doc = _scene()
    mesh = part3d.revolve_to_mesh(doc, None, 360, None, True)
    assert mesh["op"] == "revolve"
    assert mesh["accurate"] is True
    assert mesh["triangles"] > 0


@pytest.mark.skipif(not build123d_present, reason="build123d not installed")
def test_revolve_to_mesh_returns_effective_auto_axis():
    doc = _scene()
    # No explicit axis => the backend default (min profile X - 1e-3) is computed
    # and returned so the viewer can draw it.
    mesh = part3d.revolve_to_mesh(doc, None, 360, None, True, "medium")
    assert mesh["axis"] is not None
    assert mesh["quality"] == "medium"


def test_revolve_surface_mesh_uses_quality_segment_count():
    # Higher quality -> more swept segments -> more triangles for the fallback.
    outlines = [{"pts": [(0, 0), (20, 0), (20, 40), (0, 40)], "closed": True}]
    _, lo = part3d._revolve_surface_mesh(outlines, 360, 0, 12)
    _, hi = part3d._revolve_surface_mesh(outlines, 360, 0, 96)
    assert len(hi) > len(lo)


def test_quality_params_mapping():
    assert part3d._quality_params("low") == (0.4, 12)
    assert part3d._quality_params("medium") == (0.15, 48)
    assert part3d._quality_params("high") == (0.04, 96)
    # unknown values fall back to medium
    assert part3d._quality_params("bogus") == (0.15, 48)


def _sweep_scene():
    """A closed profile (rect) plus an open path (the sweep rail)."""
    doc = Document()
    doc.add(rect(300, 250, 200, 120))
    doc.add(path(0, 0, "M0,0 L80,0 L80,120 L200,120", stroke="#000"))
    return doc


@pytest.mark.skipif(not build123d_present, reason="build123d not installed")
def test_solid_from_sweep_volume_sane():
    # Square 200x120 px profile (~52.9 x 31.7 mm) swept along an L-path. The bulk
    # of the volume tracks the swept length; we assert it's a positive solid body
    # (degeneracy would yield ~0).
    outlines = [
        {"pts": [(0, 0), (10, 0), (10, 10), (0, 10)], "closed": True},
        {"pts": [(0, 0), (20, 0), (20, 30), (40, 30)], "closed": False},
    ]
    solid = part3d.solid_from_sweep(outlines, None, "medium")
    assert solid.volume > 1000  # well-formed swept body, not degenerate


@pytest.mark.skipif(not build123d_present, reason="build123d not installed")
def test_sweep_to_mesh_from_doc():
    doc = _sweep_scene()
    mesh = part3d.sweep_to_mesh(doc, None, True, "high")
    assert mesh["op"] == "sweep"
    assert mesh["quality"] == "high"
    assert mesh["triangles"] > 0


def test_sweep_to_mesh_rejects_profile_only():
    # No open path -> clear error rather than a degenerate solid.
    doc = Document()
    doc.add(rect(300, 250, 200, 120))
    with pytest.raises(ValueError):
        part3d.sweep_to_mesh(doc, None, False)


def test_sweep_to_mesh_rejects_path_only():
    # No closed profile -> clear error.
    doc = Document()
    doc.add(path(0, 0, "M0,0 L80,0 L80,120", stroke="#000"))
    with pytest.raises(ValueError):
        part3d.sweep_to_mesh(doc, None, False)


# --- trimesh mesh sanitation (fallback path) ---------------------------------

def test_sanitize_mesh_cleans_degenerate_and_flags_watertight():
    pytest.importorskip("trimesh")
    # A closed tetrahedron: 4 triangles.
    v = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
    faces = [(0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 3, 2)]
    pos = [c for p in v for c in p]
    idx = [i for f in faces for i in f]
    # Append a duplicate triangle and a zero-area (degenerate) triangle.
    dirty = list(idx) + idx[:3] + [0, 0, 0]
    out_pos, out_idx, info = part3d.sanitize_mesh(pos, dirty)
    assert info["available"] is True
    assert info["removed"] >= 2
    assert info["watertight"] is True
    assert len(out_idx) // 3 == 4


def test_sanitize_mesh_passthrough_without_trimesh():
    # With trimesh importable, force the import to fail and confirm a clean
    # passthrough so callers never need to branch.
    import sys
    from unittest.mock import patch

    with patch.dict(sys.modules, {"trimesh": None, "numpy": None}):
        pos = [0, 0, 0, 1, 0, 0, 0, 1, 0]
        idx = [0, 1, 2]
        out_pos, out_idx, info = part3d.sanitize_mesh(pos, idx)
    assert info["available"] is False
    assert out_pos == pos and out_idx == idx

