"""M3 tests: coordinate-convention unification (review.md §6.3 M3, docs/m3-migration.md).

Two kinds of checks:

1. **Output-contract** — pin what the *renderers* must keep producing for a fixed
   fixture scene, independent of how the schema is stored. These must stay green
   before AND after the migration: if SVG / cut-polyline output for identical
   geometry ever changes, these fail and tell us the migration broke the
   contract. The fixture is built as raw dicts (one per shape) so it does not
   depend on factory semantics.

2. **Schema / uniformity** — assert the post-M3 convention: every shape carries a
   meaningful `x,y` origin (rect/circle/text = centre; line/dimension/path =
   centroid of their geometry), and `_bbox(o)`'s centre equals `o["x"],o["y"]` for
   every type (the property Option B establishes). Also that `load_dict` migrates
   legacy objects (which stored `x,y == 0`) to the centroid automatically.
"""

from core.document import circle, dimension, line, path, rect, text
from core.export import _bbox, _cut_polylines, to_svg

from tests.fixtures_m3 import _obj, scene_current_schema


# ---- output-contract tests (must stay green through migration) --------------

def test_svg_rect_maps_center_to_top_left():
    svg = to_svg(scene_current_schema())
    # centre (100,100), w50 h30 -> top-left corner (75,85). This mapping must be
    # preserved regardless of how rect is stored internally.
    assert '<rect x="75.00" y="85.00" width="50.00" height="30.00"' in svg


def test_svg_line_endpoints_verbatim():
    svg = to_svg(scene_current_schema())
    assert '<line x1="0.00" y1="0.00" x2="80.00" y2="120.00"' in svg


def test_svg_circle_center_radius():
    svg = to_svg(scene_current_schema())
    assert '<circle cx="620.00" cy="250.00" r="60.00"' in svg


def test_svg_text_at_center():
    svg = to_svg(scene_current_schema())
    assert '<text x="450.00" y="120.00"' in svg


def test_svg_dimension_from_absolute_endpoints():
    svg = to_svg(scene_current_schema())
    # dimension between (200,340) and (400,340) with offset 26 -> the two
    # measured endpoints anchor the extension lines. They appear as x1/y1 of the
    # first and second extension lines respectively (see _dim_geometry).
    assert 'x1="200.00" y1="340.00"' in svg
    assert 'x1="400.00" y1="340.00"' in svg


def test_cut_polylines_rect_derived_from_center():
    d = scene_current_schema()
    o = _obj(d, "r1")
    polys = _cut_polylines(o, d, {})
    assert polys[0]["closed"] is True
    pts = polys[0]["pts"]
    # same four corners the SVG test asserts, derived from the centre + w/h.
    assert (75, 85) in pts and (125, 85) in pts
    assert (125, 115) in pts and (75, 115) in pts


def test_cut_polylines_line_endpoints():
    d = scene_current_schema()
    o = _obj(d, "l1")
    polys = _cut_polylines(o, d, {})
    assert polys[0]["closed"] is False
    assert polys[0]["pts"] == [(0, 0), (80, 120)]


# ---- schema / uniformity tests (post-M3 convention) ------------------------

def test_rect_and_circle_and_text_keep_center_origin():
    assert rect(100, 100, 50, 30)["x"] == 100 and rect(100, 100, 50, 30)["y"] == 100
    assert circle(620, 250, 60)["x"] == 620 and circle(620, 250, 60)["y"] == 250
    assert text(450, 120, "x")["x"] == 450 and text(450, 120, "x")["y"] == 120


def test_line_carries_centroid_origin():
    ln = line(0, 0, 80, 120)
    # midpoint of the endpoints, not 0.
    assert ln["x"] == 40 and ln["y"] == 60
    assert ln["geometry"] == {"x1": 0, "y1": 0, "x2": 80, "y2": 120}


def test_dimension_carries_centroid_origin():
    dm = dimension(200, 340, 400, 340, offset=26)
    assert dm["x"] == 300 and dm["y"] == 340
    assert dm["geometry"]["x1"] == 200 and dm["geometry"]["x2"] == 400


def test_path_carries_centroid_origin():
    p = path(0, 0, "M10,10 L60,10 L60,60 L10,60 Z")
    # bbox centre of the 10..60 square is (35,35), regardless of the passed x,y.
    assert p["x"] == 35 and p["y"] == 35
    assert p["geometry"]["d"] == "M10,10 L60,10 L60,60 L10,60 Z"


def test_load_dict_normalises_legacy_origin():
    from core.document import Document

    # A legacy object stored with x,y == 0 and absolute geometry.
    legacy = {"id": "l1", "type": "line", "x": 0, "y": 0, "rotation": 0,
              "scaleX": 1, "scaleY": 1,
              "geometry": {"x1": 0, "y1": 0, "x2": 80, "y2": 120},
              "style": {}, "meta": {}}
    d = Document()
    d.load_dict({"objects": [legacy]})
    o = d.objects[0]
    # legacy x,y == 0 is migrated to the centroid origin automatically.
    assert o["x"] == 40 and o["y"] == 60


def test_bbox_centre_matches_origin_for_every_type():
    shapes = [
        rect(100, 100, 50, 30),
        circle(620, 250, 60),
        text(450, 120, "x"),
        line(0, 0, 80, 120),
        dimension(200, 340,400, 340, offset=26),
        path(0, 0, "M10,10 L60,10 L60,60 L10,60 Z"),
    ]
    for o in shapes:
        b = _bbox(o)
        assert b is not None, f"{o['type']} has no bbox"
        assert b["cx"] == o["x"], f"{o['type']} cx {b['cx']} != x {o['x']}"
        assert b["cy"] == o["y"], f"{o['type']} cy {b['cy']} != y {o['y']}"
