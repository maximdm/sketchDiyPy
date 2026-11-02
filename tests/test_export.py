"""Tests for the exporters (core/export.py): SVG, PNG/PDF, DXF, G-code."""

import re

import pytest

from core.document import Document, path, rect
from core.export import to_dxf, to_gcode, to_png_pdf, to_svg
from core.runner import run


def _doc_with(*objs):
    d = Document()
    for o in objs:
        d.add(o)
    return d.to_dict()


def _dxf_coords(dxf):
    """Collect (x, y) pairs from DXF LWPOLYLINE vertices."""
    lines = dxf.split("\n")
    coords = []
    for i in range(len(lines) - 3):
        if lines[i] == "10":
            x = float(lines[i + 1])
            y = float(lines[i + 3])  # group 20 follows group 10
            coords.append((x, y))
    return coords


# ---- SVG -----------------------------------------------------------------
def test_to_svg_contains_shapes():
    doc = _doc_with(rect(100, 100, 50, 30, fill="#cfe8ff", stroke="#1f6feb"))
    svg = to_svg(doc)
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert "<rect" in svg
    assert "#1f6feb" in svg


def test_to_svg_resolves_connector_to_a_line():
    out = run(
        "a = rect(100, 100, 50, 50)\n"
        "b = rect(300, 100, 50, 50)\n"
        "connect(a, b, 'right', 'left')",
        Document(),
    )
    svg = to_svg(out)
    # at least one <line> for the resolved connector (plus any dimension lines)
    assert "<line" in svg


def test_to_svg_grid_is_opt_in():
    doc = _doc_with(rect(100, 100, 50, 30))
    # default: grid is NOT baked into the SVG
    assert '<g stroke="' not in to_svg(doc)
    # opt-in via the document flag bakes a hairline grid group in
    doc["export_grid"] = True
    svg = to_svg(doc)
    assert '<g stroke="#eef2f7" stroke-width="1">' in svg


def test_hidden_layer_objects_are_excluded_from_exports():
    doc = _doc_with(rect(100, 100, 50, 30, fill="#cfe8ff", stroke="#1f6feb"))
    doc["layers"] = [
        {"id": "L1", "name": "Visible", "visible": True, "locked": False},
        {"id": "L2", "name": "Hidden", "visible": False, "locked": False},
    ]
    doc["objects"][0]["meta"] = {"layer": "L2"}
    # SVG: no <rect> for the hidden object, but the background <rect> remains
    svg = to_svg(doc)
    assert "<rect" in svg  # background fill rect
    assert "#1f6feb" not in svg  # shape stroke excluded
    # PNG/PDF and DXF/G-code also skip it
    assert len(to_png_pdf(doc, "png")) > 0
    dxf = to_dxf(doc)
    assert "LWPOLYLINE" not in dxf  # no cuttable outlines emitted
    gcode = to_gcode(doc)
    # no cutting/plunge moves for the hidden object (G17 is a plane select, not a
    # cut, so match the real move verbs explicitly)
    assert "G1 X" not in gcode and "G1 Z" not in gcode


def test_visible_layer_objects_are_exported():
    doc = _doc_with(rect(100, 100, 50, 30, fill="#cfe8ff", stroke="#1f6feb"))
    doc["layers"] = [
        {"id": "L1", "name": "Visible", "visible": True, "locked": False},
    ]
    doc["objects"][0]["meta"] = {"layer": "L1"}
    assert "#1f6feb" in to_svg(doc)
    # the override parameter wins over the document flag
    assert '<g stroke="' not in to_svg(doc, with_grid=False)
    assert '<g stroke="#eef2f7" stroke-width="1">' in to_svg(doc, with_grid=True)


def test_to_png_pdf_accepts_grid_option():
    doc = _doc_with(rect(100, 100, 50, 30))
    # default export works
    assert to_png_pdf(doc, "png")[:8] == b"\x89PNG\r\n\x1a\n"
    # enabling the grid option still produces a valid image
    doc["export_grid"] = True
    png = to_png_pdf(doc, "png")
    pdf = to_png_pdf(doc, "pdf")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert pdf[:4] == b"%PDF"


# ---- PNG / PDF -----------------------------------------------------------
def test_to_png_pdf_signatures():
    doc = _doc_with(rect(100, 100, 50, 30))
    png = to_png_pdf(doc, "png")
    pdf = to_png_pdf(doc, "pdf")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert pdf[:4] == b"%PDF"


# ---- DXF -----------------------------------------------------------------
def test_to_dxf_structure():
    doc = _doc_with(rect(100, 100, 50, 30))
    dxf = to_dxf(doc)
    for marker in ("SECTION", "ENTITIES", "LWPOLYLINE", "EOF", "AC1015"):
        assert marker in dxf
    # millimetre units
    assert "$INSUNITS" in dxf
    assert "70\n4" in dxf  # mm = 4


def test_to_dxf_coordinates_are_mm_and_y_up():
    # 20x20 rect centred at (200, 200); px_per_unit = 3.78 (mm), height = 700
    doc = _doc_with(rect(200, 200, 20, 20))
    dxf = to_dxf(doc)
    coords = _dxf_coords(dxf)
    assert len(coords) == 4
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    # mm scale: 190/3.78 ~= 50.26, not pixel-scale (~190)
    assert 49 < min(xs) < 51
    assert max(xs) < 60
    # y-up flip: smaller canvas y -> larger mm y
    assert max(ys) > min(ys)


def test_to_dxf_connector_resolves_to_polyline():
    out = run(
        "a = rect(100, 100, 50, 50)\n"
        "b = rect(300, 100, 50, 50)\n"
        "connect(a, b, 'right', 'left')",
        Document(),
    )
    dxf = to_dxf(out)
    # 2 rects (closed) + 1 connector (open) = 3 polylines
    assert dxf.count("LWPOLYLINE") == 3


def test_to_dxf_handles_paths():
    doc = _doc_with(path(0, 0, "M0 0 L100 0 L100 100 L0 100 Z", stroke="#222"))
    assert "LWPOLYLINE" in to_dxf(doc)


def test_to_gcode_grid_is_opt_in():
    doc = _doc_with(rect(100, 100, 50, 30))
    # default: no grid comment, no grid rapids
    g0 = to_gcode(doc)
    assert "; grid reference" not in g0
    # opt-in via the document flag adds grid rapids (G0 only, no Z plunge)
    doc["export_grid"] = True
    g1 = to_gcode(doc)
    assert "; grid reference" in g1
    # every grid move is a rapid (G0) at safe Z with no plunge into the material
    grid_lines = g1.split("; grid reference")[1].splitlines()
    rapids = [ln.strip() for ln in grid_lines if ln.strip().startswith("G0 X")]
    assert len(rapids) > 4  # many grid lines, not just the cut rapids
    assert all(("Z" not in ln) and ("F" not in ln) for ln in rapids)
    # there must be no cutting feed (G1) anywhere in the grid reference section
    assert not any(ln.strip().startswith("G1") for ln in grid_lines)
    # the override parameter wins over the document flag
    assert "; grid reference" not in to_gcode(doc, with_grid=False)
    assert "; grid reference" in to_gcode(doc, with_grid=True)


def test_to_dxf_grid_is_opt_in():
    doc = _doc_with(rect(100, 100, 50, 30))
    # default: no GRID layer, one polyline (the rect)
    dxf_off = to_dxf(doc)
    assert "GRID" not in dxf_off
    assert dxf_off.count("LWPOLYLINE") == 1
    # opt-in via the document flag adds a GRID layer + many grid lines
    doc["export_grid"] = True
    dxf_on = to_dxf(doc)
    assert "GRID" in dxf_on
    assert dxf_on.count("LWPOLYLINE") > 1
    # the override parameter wins over the document flag
    assert "GRID" not in to_dxf(doc, with_grid=False)
    assert "GRID" in to_dxf(doc, with_grid=True)


# ---- G-code --------------------------------------------------------------
def test_to_gcode_structure_and_mm():
    doc = _doc_with(rect(200, 200, 20, 20))
    g = to_gcode(doc)
    for token in ("G21", "G90", "G0", "G1", "M2"):
        assert token in g
    # plunge to the configured depth
    assert "Z-3.000" in g
    # X coordinates are millimetres, not pixels
    xs = [float(v) for v in re.findall(r"X([-\d.]+)", g)]
    assert xs and max(abs(v) for v in xs) < 250


def test_to_gcode_connector_is_cut():
    out = run(
        "a = rect(100, 100, 50, 50)\n"
        "b = rect(300, 100, 50, 50)\n"
        "connect(a, b, 'right', 'left')",
        Document(),
    )
    g = to_gcode(out)
    assert "G1" in g  # cutter moves (includes the connector)
    assert "M2" in g


def test_to_gcode_params_are_honoured():
    doc = _doc_with(rect(200, 200, 20, 20))
    g = to_gcode(doc, feed=600, plunge=250, depth=-5, safe_z=8)
    assert "Z-5.000" in g  # depth
    assert "F600.0" in g  # feed on cuts
    assert "F250.0" in g  # plunge rate
    assert "Z8.000" in g  # safe Z
