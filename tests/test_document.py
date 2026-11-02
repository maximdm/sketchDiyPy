"""Tests for the canonical document model (core/document.py)."""

import pytest

from core.document import (
    Document,
    circle,
    connector,
    dimension,
    line,
    rect,
    text,
)


def _canonical_keys(o):
    return set(o.keys())


def test_factories_produce_canonical_schema():
    for o in (
        rect(10, 20, 30, 40, fill="#abc"),
        circle(5, 6, 7),
        line(0, 0, 10, 10),
        text(1, 2, "hi"),
        dimension(0, 0, 50, 0),
        connector("a", "left", "b", "right"),
    ):
        assert _canonical_keys(o) >= {
            "id",
            "type",
            "x",
            "y",
            "rotation",
            "scaleX",
            "scaleY",
            "geometry",
            "style",
            "meta",
        }
        assert isinstance(o["id"], str) and o["id"]


def test_rect_geometry_is_centre_based():
    o = rect(200, 150, 80, 40)
    assert o["type"] == "rect"
    assert o["x"] == 200 and o["y"] == 150
    assert o["geometry"] == {"w": 80, "h": 40}
    # style carries fill/stroke defaults
    assert o["style"]["stroke"] is not None


def test_circle_and_line_geometry():
    assert circle(3, 4, 5)["geometry"] == {"r": 5}
    ln = line(1, 2, 3, 4)
    assert ln["geometry"] == {"x1": 1, "y1": 2, "x2": 3, "y2": 4}
    # line/connector store identity transform
    assert ln["x"] == 0 and ln["rotation"] == 0


def test_connector_stores_refs_not_endpoints():
    c = connector("objA", "left", "objB", "right")
    assert c["type"] == "connector"
    assert c["geometry"]["from"] == {"id": "objA", "anchor": "left"}
    assert c["geometry"]["to"] == {"id": "objB", "anchor": "right"}


def test_document_add_find_upsert_remove():
    d = Document()
    o = rect(10, 10, 10, 10)
    d.add(o)
    assert d.find(o["id"]) is o
    # upsert updates in place
    o2 = dict(o, x=99)
    d.upsert(o2)
    assert len(d.objects) == 1
    assert d.find(o["id"])["x"] == 99
    # remove by id
    d.remove(o["id"])
    assert d.find(o["id"]) is None
    assert len(d.objects) == 0


def test_to_dict_then_load_dict_round_trip():
    d = Document()
    d.add(rect(100, 100, 50, 50))
    d.add(circle(200, 200, 25))
    d.units = "cm"
    d.width = 1200
    snapshot = d.to_dict()

    d2 = Document()
    d2.load_dict(snapshot)
    assert d2.units == "cm"
    assert d2.width == 1200
    assert len(d2.objects) == 2
    types = {o["type"] for o in d2.objects}
    assert types == {"rect", "circle"}
    # geometry preserved exactly
    r = next(o for o in d2.objects if o["type"] == "rect")
    assert r["geometry"] == {"w": 50, "h": 50} and r["x"] == 100


def test_snap_preferences_round_trip_and_default_on_missing():
    d = Document()
    assert d.snap_grid is True and d.snap_objects is True and d.snap_page is True
    d.snap_grid = False
    d.snap_objects = False
    d.snap_page = False
    snap = d.to_dict()
    assert snap["snap_grid"] is False
    assert snap["snap_objects"] is False
    assert snap["snap_page"] is False

    d2 = Document()
    d2.load_dict(snap)
    assert d2.snap_grid is False and d2.snap_objects is False and d2.snap_page is False

    # a saved project that omits the keys keeps the defaults
    d3 = Document()
    d3.load_dict({"width": 500, "objects": []})
    assert d3.snap_grid is True and d3.snap_objects is True and d3.snap_page is True


def test_load_dict_is_tolerant_of_extra_and_missing_keys():
    d = Document()
    # unknown keys ignored; missing settings keep defaults
    d.load_dict(
        {
            "width": 500,
            "objects": [rect(1, 2, 3, 4)],
            "favourite_color": "teal",  # unknown -> ignored
        }
    )
    assert d.width == 500
    assert d.height == 700  # default retained
    assert d.units == "mm"  # default retained
    assert len(d.objects) == 1


def test_document_starts_with_a_default_layer():
    d = Document()
    assert len(d.layers) == 1
    base = d.layers[0]
    assert base["id"] and base["name"] and base["visible"] is True
    snap = d.to_dict()
    assert snap["layers"] == d.layers


def test_layers_round_trip():
    d = Document()
    d.add(rect(10, 10, 10, 10))
    d.add(circle(20, 20, 5))
    # an object with no layer assigned still round-trips cleanly
    d.objects[0]["meta"] = {}
    snap = d.to_dict()

    d2 = Document()
    d2.load_dict(snap)
    assert d2.layers == d.layers
    # the layer list is preserved exactly
    assert len(d2.layers) == 1
    assert len(d2.objects) == 2


def test_load_dict_handles_missing_or_empty_layers():
    d = Document()
    d.load_dict({"objects": [rect(1, 2, 3, 4)]})
    assert len(d.layers) == 1  # a default layer is created
    # a document that ships its own single layer is respected
    d.load_dict(
        {
            "layers": [{"id": "L1", "name": "Only", "visible": True, "locked": False}],
            "objects": [rect(1, 2, 3, 4, meta={"layer": "L1"})],
        }
    )
    assert d.layers[0]["id"] == "L1"
    assert d.objects[0]["meta"]["layer"] == "L1"


def test_layer_visibility_resolution():
    from core.export import _layer_visible

    doc = {
        "layers": [
            {"id": "L1", "name": "V", "visible": True, "locked": False},
            {"id": "L2", "name": "H", "visible": False, "locked": False},
        ]
    }
    # explicit membership is respected
    assert _layer_visible(doc, {"meta": {"layer": "L1"}}) is True
    assert _layer_visible(doc, {"meta": {"layer": "L2"}}) is False
    # missing layer reference (pre-layers / stale id) defaults to visible
    assert _layer_visible(doc, {"meta": {}}) is True
    assert _layer_visible(doc, {"meta": {"layer": "ghost"}}) is True
    # a document with no layers at all is fully visible
    assert _layer_visible({"objects": []}, {"meta": {}}) is True

