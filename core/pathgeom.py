"""Pure SVG path vertex extraction (no matplotlib / no GUI deps).

Shared by the document model (`core/document.py`) and the exporters
(`core/export.py`) so a path's bounding-box / centroid math agrees everywhere
(review.md §6.3 M3). This is a faithful port of the vertex collection in
`core/export.py:_parse_path` (only the coordinate vertices are kept; the
matplotlib path `codes` are irrelevant for bbox/centroid and intentionally
dropped here so this module stays import-light for the console / runner).
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(
    r"[MLHVCSQTAZmlhvcsqtaz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
)


def path_vertices(d: str) -> list[tuple[float, float]]:
    """Coordinate vertices of an SVG path `d`, incl. curve control points.

    Mirrors `core/export.py:_parse_path`'s vertex list exactly so callers that
    compute a bbox or centre from it produce identical results to the exporter's
    path handling.
    """
    if not d:
        return []
    tokens = _TOKEN_RE.findall(d)
    verts: list[tuple[float, float]] = []
    cx = cy = start_x = start_y = 0.0
    prev_ctrl: tuple[float, float] | None = None
    prev_curve = False  # was the previous command a cubic/quadratic curve?
    cmd: str | None = None
    i = 0
    n = len(tokens)

    def num() -> float:
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    while i < n:
        t = tokens[i]
        if t in "MLHVCSQTAZmlhvcsqtaz":
            cmd = t
            i += 1
            rel = cmd.islower()
            c = cmd.upper()
        else:
            rel = cmd.islower()
            c = cmd.upper()

        if c == "M":
            x, y = num(), num()
            if rel:
                x += cx
                y += cy
            cx, cy = x, y
            start_x, start_y = x, y
            verts.append((cx, cy))
            cmd = "l" if rel else "L"  # subsequent pairs are lineto
            prev_curve = False
        elif c == "L":
            x, y = num(), num()
            if rel:
                x += cx
                y += cy
            cx, cy = x, y
            verts.append((cx, cy))
            prev_curve = False
        elif c == "H":
            x = num()
            if rel:
                x += cx
            cx = x
            verts.append((cx, cy))
            prev_curve = False
        elif c == "V":
            y = num()
            if rel:
                y += cy
            cy = y
            verts.append((cx, cy))
            prev_curve = False
        elif c == "C":
            x1, y1, x2, y2, x, y = (num(), num(), num(), num(), num(), num())
            if rel:
                x1 += cx; y1 += cy; x2 += cx; y2 += cy; x += cx; y += cy
            verts += [(x1, y1), (x2, y2), (x, y)]
            cx, cy = x, y
            prev_ctrl = (x2, y2)
            prev_curve = True
        elif c == "S":
            x2, y2, x, y = num(), num(), num(), num()
            if rel:
                x2 += cx; y2 += cy; x += cx; y += cy
            if prev_ctrl is not None and prev_curve:
                rx, ry = 2 * cx - prev_ctrl[0], 2 * cy - prev_ctrl[1]
            else:
                rx, ry = cx, cy
            verts += [(rx, ry), (x2, y2), (x, y)]
            cx, cy = x, y
            prev_ctrl = (x2, y2)
            prev_curve = True
        elif c == "Q":
            x1, y1, x, y = num(), num(), num(), num()
            if rel:
                x1 += cx; y1 += cy; x += cx; y += cy
            verts += [(x1, y1), (x, y)]
            cx, cy = x, y
            prev_ctrl = (x1, y1)
            prev_curve = True
        elif c == "T":
            x, y = num(), num()
            if rel:
                x += cx
                y += cy
            if prev_ctrl is not None and prev_curve:
                rx, ry = 2 * cx - prev_ctrl[0], 2 * cy - prev_ctrl[1]
            else:
                rx, ry = cx, cy
            verts += [(rx, ry), (x, y)]
            cx, cy = x, y
            prev_ctrl = (rx, ry)
            prev_curve = True
        elif c == "A":
            # Arc: skip rx, ry, x-rotation, large-arc, sweep, then read endpoint.
            num(); num(); num(); num(); num()
            x, y = num(), num()
            if rel:
                x += cx
                y += cy
            cx, cy = x, y
            verts.append((cx, cy))
            prev_curve = False
        elif c == "Z":
            cx, cy = start_x, start_y
            verts.append((cx, cy))
            prev_curve = False
    return verts


def path_center(bbox_verts: list[tuple[float, float]] | None = None,
                d: str | None = None) -> tuple[float, float] | None:
    """Centre of a path's vertex bounding box (used as its `x,y` origin)."""
    verts = bbox_verts if bbox_verts is not None else path_vertices(d or "")
    if not verts:
        return None
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
