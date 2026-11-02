"""Tests for image -> path tracing (core/trace.py).

These need scikit-image (lazy-imported inside trace_from_bytes). If it is not
installed the whole module is skipped rather than failing.
"""

import io

import pytest

skimage = pytest.importorskip("skimage")
from PIL import Image, ImageDraw  # noqa: E402

from core.trace import trace_from_bytes  # noqa: E402


def _black_square_png(size=200, box=(50, 50, 150, 150)):
    img = Image.new("RGB", (size, size), "white")
    ImageDraw.Draw(img).rectangle(box, fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_trace_outline_returns_path_objects():
    paths = trace_from_bytes(_black_square_png(), mode="outline", threshold=128)
    assert paths, "expected at least one traced contour"
    for p in paths:
        assert p["type"] == "path"
        assert "d" in p["geometry"] and p["geometry"]["d"].startswith("M")
        assert p["meta"].get("traced") is True


def test_trace_respects_min_area():
    # tiny specks below min_area are dropped
    paths = trace_from_bytes(
        _black_square_png(), mode="outline", threshold=128, min_area=100000
    )
    assert paths == []


def test_trace_smooth_uses_beziers():
    paths = trace_from_bytes(
        _black_square_png(), mode="outline", threshold=128, smooth=True
    )
    assert paths
    # smoothed output uses cubic Bézier segments
    assert " C " in paths[0]["geometry"]["d"]
