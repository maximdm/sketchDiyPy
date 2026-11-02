"""Tests for the Hybrid console runner (core/runner.py)."""

import pytest

from core.document import rect
from core.runner import run


def _types(result):
    return [o["type"] for o in result["objects"]]


def test_run_creates_objects(doc):
    out = run("rect(100, 100, 50, 30)", doc)
    assert len(out["objects"]) == 1
    o = out["objects"][0]
    assert o["type"] == "rect"
    assert o["geometry"] == {"w": 50, "h": 30}
    assert o["x"] == 100


def test_run_clear_and_loop(doc):
    out = run("clear()\nfor i in range(3):\n    rect(100, 100 + i * 40, 50, 20)", doc)
    assert _types(out).count("rect") == 3


def test_run_connect_makes_connector(doc):
    out = run(
        "a = rect(100, 100, 50, 50)\n"
        "b = rect(300, 100, 50, 50)\n"
        "connect(a, b, 'right', 'left')",
        doc,
    )
    assert _types(out).count("rect") == 2
    conn = next(o for o in out["objects"] if o["type"] == "connector")
    # connector references the two rect ids by anchor (not baked endpoints)
    assert conn["geometry"]["from"]["id"] == a_id(out, 0)
    assert conn["geometry"]["to"]["id"] == a_id(out, 1)


def a_id(out, idx):
    # rects are the non-connector objects, in insertion order
    return [o["id"] for o in out["objects"] if o["type"] == "rect"][idx]


def test_run_layout_helpers_reposition(doc):
    out = run(
        "clear()\n"
        "parts = [rect(0, 0, 20, 20) for _ in range(4)]\n"
        "row(parts, gap=10)\n",
        doc,
    )
    xs = [o["x"] for o in out["objects"] if o["type"] == "rect"]
    assert len(xs) == 4
    assert max(xs) > min(xs)  # row() spread them out


def test_run_grid_helper(doc):
    out = run(
        "clear()\n"
        "parts = [rect(0, 0, 20, 20) for _ in range(6)]\n"
        "grid(parts, 3, gap_x=10, gap_y=10)\n",
        doc,
    )
    rects = [o for o in out["objects"] if o["type"] == "rect"]
    assert len(rects) == 6
    xs = sorted({o["x"] for o in rects})
    ys = sorted({o["y"] for o in rects})
    # 3 distinct columns and 2 distinct rows
    assert len(xs) == 3
    assert len(ys) == 2


def test_run_blocks_dangerous_builtins(doc):
    with pytest.raises(RuntimeError):
        run("__import__('os')", doc)


def test_run_surfaces_exceptions_as_runtimeerror(doc):
    with pytest.raises(RuntimeError):
        run("1 / 0", doc)


def test_run_rolls_back_on_error(doc):
    # Seed one object so the doc isn't empty.
    doc.add(rect(10, 10, 5, 5))
    seed_id = doc.objects[0]["id"]
    # The script adds a second shape and then raises: the added shape (and only
    # it) must not survive. `load_dict` backfills a default layer on rollback, so
    # we assert on object identity/count rather than a full dict equality.
    with pytest.raises(RuntimeError):
        run("rect(100, 100, 50, 50)\nraise ValueError('boom')", doc)
    after = doc.to_dict()
    ids = [o["id"] for o in after["objects"]]
    assert ids == [seed_id]


def test_run_rolls_back_settings_on_error(doc):
    # Settings changes mid-script must also roll back on error.
    before_units = doc.to_dict()["units"]
    with pytest.raises(RuntimeError):
        run("doc.units = 'inch'\nraise RuntimeError('nope')", doc)
    assert doc.to_dict()["units"] == before_units
