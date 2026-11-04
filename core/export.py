"""Exporters: SVG (vector, for cutters/lasers) and PNG/PDF via matplotlib."""

from __future__ import annotations

import io
import math
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle, Ellipse, PathPatch, Rectangle  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402
from matplotlib.transforms import Affine2D  # noqa: E402

# --------------------------------------------------------------------------
# Dimension helpers (shared by SVG + matplotlib)
# --------------------------------------------------------------------------

_UNIT_MM = {"mm": 1.0, "cm": 10.0, "inch": 25.4}


def _layer_visible(doc: dict, obj: dict) -> bool:
    """Whether `obj` should be drawn, given the document's layer visibility.

    An object with no layer (or a layer that no longer exists) is always visible;
    only an explicitly hidden layer hides its members. Hidden layers are skipped
    by every exporter so the canvas and the output agree.
    """
    layers = doc.get("layers")
    if not layers:
        return True
    lid = (obj.get("meta") or {}).get("layer")
    for l in layers:
        if l.get("id") == lid:
            return bool(l.get("visible", True))
    return True


def _format_length(length_px: float, doc: dict) -> str:
    units = doc.get("units", "mm")
    scale = doc.get("px_per_unit", 3.78) * _UNIT_MM.get(units, 1.0)
    value = length_px / scale if scale else 0.0
    digits = 0 if value >= 100 else 1 if value >= 10 else 2
    return f"{value:.{digits}f} {units}"


def _dim_geometry(g: dict) -> dict | None:
    """Lay out a dimension: measured segment, offset line, ticks, label."""
    x1, y1, x2, y2 = (float(g[k]) for k in ("x1", "y1", "x2", "y2"))
    off = float(g.get("offset", 26))
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return None
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    ax, ay = x1 + nx * off, y1 + ny * off
    bx, by = x2 + nx * off, y2 + ny * off
    ext = 4.0 if off >= 0 else -4.0
    # 45-degree architectural tick across the dimension line
    tick = 5.0 / math.sqrt(2.0)
    tx, ty = (ux + nx) * tick, (uy + ny) * tick
    side = 10.0 if off >= 0 else -10.0
    lx, ly = (ax + bx) / 2 + nx * side, (ay + by) / 2 + ny * side
    angle = math.degrees(math.atan2(dy, dx))
    if angle > 90:
        angle -= 180
    elif angle < -90:
        angle += 180
    return {
        "length": length,
        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        "ax": ax, "ay": ay, "bx": bx, "by": by,
        "ea": (ax + nx * ext, ay + ny * ext),
        "eb": (bx + nx * ext, by + ny * ext),
        "tx": tx, "ty": ty,
        "lx": lx, "ly": ly, "angle": angle,
    }


def _dim_label(o: dict, d: dict, doc: dict) -> str:
    override = (o.get("meta") or {}).get("label")
    return override or _format_length(d["length"], doc)


# --------------------------------------------------------------------------
# Anchor helpers (shared by connectors): resolve a semantic anchor
# (center/left/right/top/bottom) of an object to a canvas point.
# --------------------------------------------------------------------------

def _path_bbox(d: str):
    """Centre + half-extents of an SVG path 'd' (numbers paired as x,y)."""
    if not d:
        return None
    from core.pathgeom import path_vertices

    verts = path_vertices(d)
    if not verts:
        return None
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    return ((minx + maxx) / 2, (miny + maxy) / 2, (maxx - minx) / 2, (maxy - miny) / 2)


def _bbox(o: dict):
    t = o["type"]
    g = o.get("geometry", {})
    sx = abs(o.get("scaleX", 1) or 1)
    sy = abs(o.get("scaleY", 1) or 1)
    rot = o.get("rotation", 0) or 0
    if t == "rect":
        return {"cx": o["x"], "cy": o["y"], "hw": g["w"] * sx / 2, "hh": g["h"] * sy / 2, "rot": rot}
    if t == "circle":
        r = g["r"] * sx
        return {"cx": o["x"], "cy": o["y"], "hw": r, "hh": r, "rot": rot}
    if t == "line":
        cx, cy = (g["x1"] + g["x2"]) / 2, (g["y1"] + g["y2"]) / 2
        return {"cx": cx, "cy": cy, "hw": abs(g["x2"] - g["x1"]) / 2,
                "hh": abs(g["y2"] - g["y1"]) / 2, "rot": 0}
    if t == "text":
        fs = g.get("fontSize", 24)
        w = max(10.0, len(g.get("text", "")) * fs * 0.6)
        return {"cx": o["x"], "cy": o["y"], "hw": w / 2, "hh": fs / 2, "rot": rot}
    if t == "dimension":
        cx, cy = (g["x1"] + g["x2"]) / 2, (g["y1"] + g["y2"]) / 2
        return {"cx": cx, "cy": cy, "hw": abs(g["x2"] - g["x1"]) / 2,
                "hh": abs(g["y2"] - g["y1"]) / 2, "rot": 0}
    if t == "path":
        bb = _path_bbox(g.get("d", ""))
        if bb:
            return {"cx": bb[0], "cy": bb[1], "hw": bb[2], "hh": bb[3], "rot": 0}
    return None


def anchor_point(o: dict, anchor: str = "center") -> dict | None:
    b = _bbox(o)
    if not b:
        return None
    rad = math.radians(b["rot"])
    cos, sin = math.cos(rad), math.sin(rad)
    lx = ly = 0.0
    if anchor == "left":
        lx = -b["hw"]
    elif anchor == "right":
        lx = b["hw"]
    elif anchor == "top":
        ly = -b["hh"]
    elif anchor == "bottom":
        ly = b["hh"]
    return {"x": b["cx"] + lx * cos - ly * sin, "y": b["cy"] + lx * sin + ly * cos}


def _resolve_ref(ref: dict, by_id: dict) -> dict | None:
    if not ref:
        return None
    o = by_id.get(ref.get("id"))
    if not o:
        return None
    return anchor_point(o, ref.get("anchor", "center"))


# --------------------------------------------------------------------------
# SVG export
# --------------------------------------------------------------------------


def _svg_obj(o: dict, doc: dict, by_id: dict | None = None) -> str:
    t = o["type"]
    s = o.get("style", {})
    fill = s.get("fill") or "none"
    stroke = s.get("stroke") or "none"
    sw = s.get("strokeWidth", 1)
    rot = o.get("rotation", 0)
    g = o.get("geometry", {})

    if t == "rect":
        w = g["w"] * o.get("scaleX", 1)
        h = g["h"] * o.get("scaleY", 1)
        x0 = o["x"] - w / 2
        y0 = o["y"] - h / 2
        tr = f' transform="rotate({rot} {o["x"]} {o["y"]})"' if rot else ""
        return (
            f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{tr}/>'
        )

    if t == "circle":
        rx = g["r"] * abs(o.get("scaleX", 1))
        ry = g["r"] * abs(o.get("scaleY", 1))
        tr = f' transform="rotate({rot} {o["x"]} {o["y"]})"' if rot else ""
        if abs(rx - ry) > 1e-9:  # non-uniform scaling makes an ellipse
            return (
                f'<ellipse cx="{o["x"]:.2f}" cy="{o["y"]:.2f}" rx="{rx:.2f}" '
                f'ry="{ry:.2f}" fill="{fill}" stroke="{stroke}" '
                f'stroke-width="{sw}"{tr}/>'
            )
        return (
            f'<circle cx="{o["x"]:.2f}" cy="{o["y"]:.2f}" r="{rx:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{tr}/>'
        )

    if t == "line":
        tr = f' transform="rotate({rot} 0 0)"' if rot else ""
        return (
            f'<line x1="{g["x1"]:.2f}" y1="{g["y1"]:.2f}" x2="{g["x2"]:.2f}" '
            f'y2="{g["y2"]:.2f}" stroke="{stroke}" stroke-width="{sw}"{tr}/>'
        )

    if t == "path":
        tr = f' transform="rotate({rot} {o["x"]} {o["y"]})"' if rot else ""
        return (
            f'<path d="{g["d"]}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw}"{tr}/>'
        )

    if t == "dimension":
        d = _dim_geometry(g)
        if not d:
            return ""
        label = _dim_label(o, d, doc)
        ax, ay = d["ea"]
        bx, by = d["eb"]
        return "\n".join([
            f'<g stroke="{stroke}" stroke-width="{sw}" fill="none" '
            f'stroke-linecap="round">',
            f'  <line x1="{d["x1"]:.2f}" y1="{d["y1"]:.2f}" '
            f'x2="{ax:.2f}" y2="{ay:.2f}"/>',
            f'  <line x1="{d["x2"]:.2f}" y1="{d["y2"]:.2f}" '
            f'x2="{bx:.2f}" y2="{by:.2f}"/>',
            f'  <line x1="{d["ax"]:.2f}" y1="{d["ay"]:.2f}" '
            f'x2="{d["bx"]:.2f}" y2="{d["by"]:.2f}"/>',
            f'  <line x1="{d["ax"] - d["tx"]:.2f}" y1="{d["ay"] - d["ty"]:.2f}" '
            f'x2="{d["ax"] + d["tx"]:.2f}" y2="{d["ay"] + d["ty"]:.2f}"/>',
            f'  <line x1="{d["bx"] - d["tx"]:.2f}" y1="{d["by"] - d["ty"]:.2f}" '
            f'x2="{d["bx"] + d["tx"]:.2f}" y2="{d["by"] + d["ty"]:.2f}"/>',
            f'  <text x="{d["lx"]:.2f}" y="{d["ly"]:.2f}" '
            f'font-size="{s.get("fontSize", 14)}" fill="{stroke}" stroke="none" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'transform="rotate({d["angle"]:.2f} {d["lx"]:.2f} {d["ly"]:.2f})">'
            f'{label}</text>',
            "</g>",
        ])

    if t == "text":
        fs = g.get("fontSize", 24)
        tr = f' transform="rotate({rot} {o["x"]} {o["y"]})"' if rot else ""
        return (
            f'<text x="{o["x"]:.2f}" y="{o["y"]:.2f}" font-size="{fs}" '
            f'fill="{stroke}" text-anchor="middle" '
            f'alignment-baseline="middle">{g.get("text", "")}</text>'
        )

    if t == "connector":
        if not by_id:
            return ""
        p0 = _resolve_ref(g.get("from"), by_id)
        p1 = _resolve_ref(g.get("to"), by_id)
        if not p0 or not p1:
            return ""
        return (
            f'<line x1="{p0["x"]:.2f}" y1="{p0["y"]:.2f}" '
            f'x2="{p1["x"]:.2f}" y2="{p1["y"]:.2f}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>'
        )

    return ""


def _svg_grid_lines(doc: dict) -> str:
    """Render the drafting grid as a group of hairlines behind the artwork.

    Only used when the export option is enabled; mirrors the on-screen CSS grid
    (same colour + cell size)."""
    w, h = doc["width"], doc["height"]
    size = max(4, min(200, int(doc.get("grid_size", 20) or 20)))
    color = doc.get("grid_color") or "#eef2f7"
    lines = [f'<g stroke="{color}" stroke-width="1">']
    for i in range(int(w // size) + 1):
        x = i * size
        lines.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}"/>')
    for j in range(int(h // size) + 1):
        y = j * size
        lines.append(f'<line x1="0" y1="{y}" x2="{w}" y2="{y}"/>')
    lines.append("</g>")
    return "\n".join(lines)


def to_svg(doc: dict, with_grid: bool | None = None) -> str:
    w, h = doc["width"], doc["height"]
    bg = doc["background"]
    grid = with_grid if with_grid is not None else bool(doc.get("export_grid", False))
    by_id = {o["id"]: o for o in doc["objects"]}
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">',
        f'<rect width="{w}" height="{h}" fill="{bg}"/>',
    ]
    if grid:
        out.append(_svg_grid_lines(doc))
    for o in doc["objects"]:
        if _layer_visible(doc, o):
            out.append(_svg_obj(o, doc, by_id))
    out.append("</svg>")
    return "\n".join(out)


# --------------------------------------------------------------------------
# PNG / PDF export (matplotlib) -- this is the "showcase" the user asked for
# --------------------------------------------------------------------------

_CMD = set("MLHVCSQTAZmlhvcsqtaz")


def _parse_path(d: str):
    """Minimal SVG path 'd' parser -> (vertices, codes) for MPath."""
    tokens = re.findall(
        r"[MLHVCSQTAZmlhvcsqtaz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", d
    )
    verts, codes = [], []
    cx = cy = start_x = start_y = 0.0
    prev_ctrl = None
    cmd = None
    i = 0

    def num():
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    while i < len(tokens):
        t = tokens[i]
        if t in _CMD:
            cmd = t
            i += 1
            rel = cmd.islower()
            c = cmd.upper()
        else:
            # implicit repeat of previous command
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
            codes.append(MPath.MOVETO)
            cmd = "l" if rel else "L"  # subsequent pairs are lineto
        elif c == "L":
            x, y = num(), num()
            if rel:
                x += cx
                y += cy
            cx, cy = x, y
            verts.append((cx, cy))
            codes.append(MPath.LINETO)
        elif c == "H":
            x = num()
            if rel:
                x += cx
            cx = x
            verts.append((cx, cy))
            codes.append(MPath.LINETO)
        elif c == "V":
            y = num()
            if rel:
                y += cy
            cy = y
            verts.append((cx, cy))
            codes.append(MPath.LINETO)
        elif c == "C":
            x1, y1, x2, y2, x, y = (num(), num(), num(), num(), num(), num())
            if rel:
                x1 += cx; y1 += cy; x2 += cx; y2 += cy; x += cx; y += cy
            verts += [(x1, y1), (x2, y2), (x, y)]
            codes += [MPath.CURVE4, MPath.CURVE4, MPath.CURVE4]
            cx, cy = x, y
            prev_ctrl = (x2, y2)
        elif c == "S":
            x2, y2, x, y = num(), num(), num(), num()
            if rel:
                x2 += cx; y2 += cy; x += cx; y += cy
            if prev_ctrl and codes and codes[-1] == MPath.CURVE4:
                rx, ry = 2 * cx - prev_ctrl[0], 2 * cy - prev_ctrl[1]
            else:
                rx, ry = cx, cy
            verts += [(rx, ry), (x2, y2), (x, y)]
            codes += [MPath.CURVE4, MPath.CURVE4, MPath.CURVE4]
            cx, cy = x, y
            prev_ctrl = (x2, y2)
        elif c == "Q":
            x1, y1, x, y = num(), num(), num(), num()
            if rel:
                x1 += cx; y1 += cy; x += cx; y += cy
            verts += [(x1, y1), (x, y)]
            codes += [MPath.CURVE3, MPath.CURVE3]
            cx, cy = x, y
            prev_ctrl = (x1, y1)
        elif c == "T":
            x, y = num(), num()
            if rel:
                x += cx; y += cy
            if prev_ctrl and codes and codes[-1] == MPath.CURVE3:
                rx, ry = 2 * cx - prev_ctrl[0], 2 * cy - prev_ctrl[1]
            else:
                rx, ry = cx, cy
            verts += [(rx, ry), (x, y)]
            codes += [MPath.CURVE3, MPath.CURVE3]
            cx, cy = x, y
            prev_ctrl = (rx, ry)
        elif c == "Z":
            cx, cy = start_x, start_y
            verts.append((cx, cy))
            codes.append(MPath.CLOSEPOLY)

    return verts, codes


def _mpl_obj(ax, o: dict, doc: dict, by_id: dict | None = None):
    t = o["type"]
    s = o.get("style", {})
    fill = s.get("fill") or "none"
    stroke = s.get("stroke") or "none"
    sw = s.get("strokeWidth", 1)
    rot = o.get("rotation", 0)
    g = o.get("geometry", {})

    if t == "rect":
        w = g["w"] * o.get("scaleX", 1)
        h = g["h"] * o.get("scaleY", 1)
        x0 = o["x"] - w / 2
        y0 = o["y"] - h / 2
        tr = Affine2D().rotate_deg_around(o["x"], o["y"], rot) + ax.transData
        ax.add_patch(
            Rectangle((x0, y0), w, h, facecolor=fill, edgecolor=stroke,
                      linewidth=sw, transform=tr)
        )
    elif t == "circle":
        rx = g["r"] * abs(o.get("scaleX", 1))
        ry = g["r"] * abs(o.get("scaleY", 1))
        if abs(rx - ry) > 1e-9:
            patch = Ellipse((o["x"], o["y"]), 2 * rx, 2 * ry,
                            facecolor=fill, edgecolor=stroke, linewidth=sw)
        else:
            patch = Circle((o["x"], o["y"]), rx, facecolor=fill,
                           edgecolor=stroke, linewidth=sw)
        if rot:
            patch.set_transform(
                Affine2D().rotate_deg_around(o["x"], o["y"], rot) + ax.transData
            )
        ax.add_patch(patch)
    elif t == "line":
        ax.add_line(
            Line2D([g["x1"], g["x2"]], [g["y1"], g["y2"]],
                   color=stroke, linewidth=sw)
        )
    elif t == "dimension":
        d = _dim_geometry(g)
        if not d:
            return
        segments = [
            ((d["x1"], d["y1"]), d["ea"]),
            ((d["x2"], d["y2"]), d["eb"]),
            ((d["ax"], d["ay"]), (d["bx"], d["by"])),
            ((d["ax"] - d["tx"], d["ay"] - d["ty"]),
             (d["ax"] + d["tx"], d["ay"] + d["ty"])),
            ((d["bx"] - d["tx"], d["by"] - d["ty"]),
             (d["bx"] + d["tx"], d["by"] + d["ty"])),
        ]
        for (x0, y0), (x1, y1) in segments:
            ax.add_line(
                Line2D([x0, x1], [y0, y1], color=stroke, linewidth=sw,
                       solid_capstyle="round")
            )
        tr = (Affine2D().rotate_deg_around(d["lx"], d["ly"], d["angle"])
              + ax.transData)
        ax.text(d["lx"], d["ly"], _dim_label(o, d, doc),
                fontsize=s.get("fontSize", 14), color=stroke,
                ha="center", va="center", transform=tr)
    elif t == "path":
        try:
            verts, codes = _parse_path(g["d"])
            tr = (Affine2D().rotate_deg_around(o["x"], o["y"], rot)
                  + ax.transData)
            ax.add_patch(
                PathPatch(MPath(verts, codes), facecolor=fill,
                          edgecolor=stroke, linewidth=sw, transform=tr)
            )
        except Exception:
            pass  # SVG export still works; matplotlib just skips it
    elif t == "text":
        fs = g.get("fontSize", 24)
        tr = (Affine2D().rotate_deg_around(o["x"], o["y"], rot)
              + ax.transData)
        ax.text(o["x"], o["y"], g.get("text", ""), fontsize=fs,
                color=stroke, ha="center", va="center", transform=tr)
    elif t == "connector":
        if not by_id:
            return
        p0 = _resolve_ref(g.get("from"), by_id)
        p1 = _resolve_ref(g.get("to"), by_id)
        if not p0 or not p1:
            return
        ax.add_line(
            Line2D([p0["x"], p1["x"]], [p0["y"], p1["y"]],
                   color=stroke, linewidth=sw)
        )


def _mpl_grid(ax, doc: dict):
    """Draw the drafting grid as hairlines behind the artwork (export option)."""
    w, h = doc["width"], doc["height"]
    size = max(4, min(200, int(doc.get("grid_size", 20) or 20)))
    color = doc.get("grid_color") or "#eef2f7"
    for i in range(int(w // size) + 1):
        x = i * size
        ax.add_line(Line2D([x, x], [0, h], color=color, linewidth=0.75, zorder=1))
    for j in range(int(h // size) + 1):
        y = j * size
        ax.add_line(Line2D([0, w], [y, y], color=color, linewidth=0.75, zorder=1))


def to_png_pdf(doc: dict, fmt: str = "png", with_grid: bool | None = None) -> bytes:
    w, h = doc["width"], doc["height"]
    fig = plt.figure(figsize=(w / 96, h / 96), dpi=96)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.invert_yaxis()
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), w, h, facecolor=doc["background"],
                           edgecolor="none", zorder=0))

    grid = with_grid if with_grid is not None else bool(doc.get("export_grid", False))
    if grid:
        _mpl_grid(ax, doc)

    by_id = {o["id"]: o for o in doc["objects"]}
    for o in doc["objects"]:
        if _layer_visible(doc, o):
            _mpl_obj(ax, o, doc, by_id)

    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=96)
    plt.close(fig)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Maker export: DXF (CAD/CNC/laser) and G-code (basic profiling).
# Both output *physical* units (mm) and flip Y to CAD's y-up convention, unlike
# the pixel-space SVG/PNG/PDF exporters.
# --------------------------------------------------------------------------

def _scale_mm(doc: dict) -> float:
    """Pixels per millimetre for this document (px_per_unit * units-per-mm)."""
    units = doc.get("units", "mm")
    px_per_unit = doc.get("px_per_unit", 3.78) or 3.78
    return px_per_unit * _UNIT_MM.get(units, 1.0)


def _to_mm(doc, x, y):
    s = _scale_mm(doc)
    return x / s, (doc["height"] - y) / s  # flip Y to y-up


def _path_polylines(d: str, steps: int = 16):
    """Approximate a path 'd' as polylines (curves sampled to line segments)."""
    verts, codes = _parse_path(d) if d else ([], [])
    polys, cur, closed = [], None, False
    j = 0
    while j < len(codes):
        c, v = codes[j], verts[j]
        if c == MPath.MOVETO:
            if cur and len(cur) > 1:
                polys.append((cur, closed))
            cur, closed = [(v[0], v[1])], False
        elif c == MPath.LINETO:
            if cur is not None:
                cur.append((v[0], v[1]))
        elif c == MPath.CURVE4:
            p0 = cur[-1]
            p1, p2, p3 = verts[j], verts[j + 1], verts[j + 2]
            for k in range(1, steps + 1):
                t = k / steps
                mt = 1 - t
                x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
                y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
                cur.append((x, y))
            j += 2
        elif c == MPath.CURVE3:
            p0 = cur[-1]
            p1, p2 = verts[j], verts[j + 1]
            for k in range(1, steps + 1):
                t = k / steps
                mt = 1 - t
                x = mt**2 * p0[0] + 2 * mt * t * p1[0] + t**2 * p2[0]
                y = mt**2 * p0[1] + 2 * mt * t * p1[1] + t**2 * p2[1]
                cur.append((x, y))
            j += 1
        elif c == MPath.CLOSEPOLY:
            closed = True
            if cur:
                cur.append(cur[0])
        j += 1
    if cur and len(cur) > 1:
        polys.append((cur, closed))
    return polys


def _cut_polylines(o: dict, doc: dict, by_id: dict, circle_seg: int = 64):
    """Cuttable outlines for one object: list of {pts:[(x,y)..], closed:bool}."""
    t = o["type"]
    g = o.get("geometry", {})
    sx = abs(o.get("scaleX", 1) or 1)
    sy = abs(o.get("scaleY", 1) or 1)
    if t == "rect":
        w, h = g["w"] * sx, g["h"] * sy
        x0, y0, x1, y1 = o["x"] - w / 2, o["y"] - h / 2, o["x"] + w / 2, o["y"] + h / 2
        return [{"pts": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "closed": True}]
    if t == "circle":
        r = g["r"] * sx
        pts = [
            (o["x"] + r * math.cos(2 * math.pi * i / circle_seg),
             o["y"] + r * math.sin(2 * math.pi * i / circle_seg))
            for i in range(circle_seg)
        ]
        return [{"pts": pts, "closed": True}]
    if t == "line":
        return [{"pts": [(g["x1"], g["y1"]), (g["x2"], g["y2"])], "closed": False}]
    if t == "path":
        return [{"pts": p, "closed": c} for p, c in _path_polylines(g.get("d", ""))]
    if t == "connector" and by_id:
        p0 = _resolve_ref(g.get("from"), by_id)
        p1 = _resolve_ref(g.get("to"), by_id)
        if p0 and p1:
            return [{"pts": [(p0["x"], p0["y"]), (p1["x"], p1["y"])], "closed": False}]
    return []


def _dxf_grid_entities(doc: dict):
    """Drafting grid as LWPOLYLINEs on the 'GRID' layer (mm, y-up) for DXF export."""
    s = _scale_mm(doc)
    if s <= 0:
        return []
    w_mm, h_mm = doc["width"] / s, doc["height"] / s
    size_mm = max(0.5, min(2000.0, (doc.get("grid_size", 20) or 20) / s))
    ents = []
    x = 0.0
    while x <= w_mm + 1e-6:
        ents += ["0", "LWPOLYLINE", "8", "GRID", "90", "2", "70", "0",
                 "10", repr(round(x, 4)), "20", "0.0",
                 "10", repr(round(x, 4)), "20", repr(round(h_mm, 4))]
        x += size_mm
    y = 0.0
    while y <= h_mm + 1e-6:
        ents += ["0", "LWPOLYLINE", "8", "GRID", "90", "2", "70", "0",
                 "10", "0.0", "20", repr(round(y, 4)),
                 "10", repr(round(w_mm, 4)), "20", repr(round(y, 4))]
        y += size_mm
    return ents


def _to_dxf_ezdxf(doc: dict, circle_seg: int = 64, with_grid: bool | None = None):
    """Optional ezdxf-backed DXF writer (review.md §6.3 M1).

    Used only when `ezdxf` is installed; falls back to the hand-rolled writer
    otherwise. Returns the DXF text, or ``None`` if ezdxf is unavailable or the
    write failed (so the caller can fall back safely).
    """
    try:
        import ezdxf
    except Exception:  # noqa: BLE001 - optional dependency
        return None
    try:
        from io import BytesIO

        insunits = {"mm": 4, "cm": 5, "inch": 1}.get(doc.get("units", "mm"), 4)
        dxf = ezdxf.new("R2000")
        dxf.header["$INSUNITS"] = insunits
        msp = dxf.modelspace()
        by_id = {o["id"]: o for o in doc["objects"]}
        if with_grid is None:
            with_grid = bool(doc.get("export_grid", False))
        if with_grid:
            dxf.layers.add("GRID", color=7)
        for o in doc["objects"]:
            if not _layer_visible(doc, o):
                continue
            for pl in _cut_polylines(o, doc, by_id, circle_seg):
                pts = [
                    tuple(round(v, 4) for v in _to_mm(doc, px, py))
                    for px, py in pl["pts"]
                ]
                if len(pts) < 2:
                    continue
                msp.add_lwpolyline(
                    pts, dxfattribs={"layer": "0"}, close=bool(pl["closed"])
                )
        if with_grid:
            s = _scale_mm(doc)
            if s > 0:
                w_mm, h_mm = doc["width"] / s, doc["height"] / s
                size_mm = max(0.5, min(2000.0, (doc.get("grid_size", 20) or 20) / s))
                x = 0.0
                while x <= w_mm + 1e-6:
                    msp.add_lwpolyline(
                        [(x, 0.0), (x, h_mm)], dxfattribs={"layer": "GRID"}
                    )
                    x += size_mm
                y = 0.0
                while y <= h_mm + 1e-6:
                    msp.add_lwpolyline(
                        [(0.0, y), (w_mm, y)], dxfattribs={"layer": "GRID"}
                    )
                    y += size_mm
        buf = BytesIO()
        dxf.write(buf)
        return buf.getvalue().decode("ascii", errors="replace")
    except Exception:  # noqa: BLE001 - if ezdxf path fails, fall back
        return None


def to_dxf(doc: dict, circle_seg: int = 64, with_grid: bool | None = None,
           use_ezdxf: bool = True) -> str:
    """Minimal AutoCAD R2000 DXF (LWPOLYLINE entities) in millimetres.

    When `ezdxf` is installed and `use_ezdxf` is true, a more robust writer is
    used (review.md §6.3 M1); otherwise the built-in hand-rolled writer runs.
    """
    if use_ezdxf:
        alt = _to_dxf_ezdxf(doc, circle_seg, with_grid)
        if alt is not None:
            return alt
    insunits = {"mm": 4, "cm": 5, "inch": 1}.get(doc.get("units", "mm"), 4)
    grid = with_grid if with_grid is not None else bool(doc.get("export_grid", False))
    by_id = {o["id"]: o for o in doc["objects"]}
    layer_defs = [
        "0", "LAYER", "2", "0", "70", "0", "62", "7", "6", "CONTINUOUS",
    ]
    if grid:
        layer_defs += [
            "0", "LAYER", "2", "GRID", "70", "0", "62", "7", "6", "CONTINUOUS",
        ]
    out = [
        "0", "SECTION", "2", "HEADER",
        "9", "$ACADVER", "1", "AC1015",
        "9", "$INSUNITS", "70", str(insunits),
        "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER", "70", str(len(layer_defs) // 10),
        *layer_defs,
        "0", "ENDTAB", "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
    ]
    for o in doc["objects"]:
        if not _layer_visible(doc, o):
            continue
        for pl in _cut_polylines(o, doc, by_id, circle_seg):
            pts = [tuple(round(v, 4) for v in _to_mm(doc, px, py)) for px, py in pl["pts"]]
            if len(pts) < 2:
                continue
            out.append("0"); out.append("LWPOLYLINE")
            out.append("8"); out.append("0")
            out.append("90"); out.append(str(len(pts)))
            out.append("70"); out.append("1" if pl["closed"] else "0")
            for (X, Y) in pts:
                out.append("10"); out.append(repr(X))
                out.append("20"); out.append(repr(Y))
    if grid:
        out += _dxf_grid_entities(doc)
    out += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(out)


def _gcode_grid(doc: dict, safe_z: float) -> list:
    """Drafting grid as rapid (G0, no plunge) reference moves at safe Z.

    These are purely for positioning reference on the machine; the tool never
    lowers into the material for them, so the grid is never cut.
    """
    s = _scale_mm(doc)
    if s <= 0:
        return []
    w, h = doc["width"], doc["height"]
    size_mm = max(0.5, min(2000.0, (doc.get("grid_size", 20) or 20) / s))
    size_px = size_mm * s
    lines = ["; grid reference (rapid, no plunge)"]
    x = 0.0
    while x <= w + 1e-6:
        p0 = _to_mm(doc, x, 0)
        p1 = _to_mm(doc, x, h)
        lines.append(f"G0 X{p0[0]:.3f} Y{p0[1]:.3f}")
        lines.append(f"G0 X{p1[0]:.3f} Y{p1[1]:.3f}")
        x += size_px
    y = 0.0
    while y <= h + 1e-6:
        p0 = _to_mm(doc, 0, y)
        p1 = _to_mm(doc, w, y)
        lines.append(f"G0 X{p0[0]:.3f} Y{p0[1]:.3f}")
        lines.append(f"G0 X{p1[0]:.3f} Y{p1[1]:.3f}")
        y += size_px
    return lines


def _polygon_signed_area(pts):
    """Signed area of a closed polygon (positive => counter-clockwise)."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def _point_in_polygon(p, poly):
    """Ray-casting point-in-polygon test (p = (x, y))."""
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / (y2 - y1) + x1
        ):
            inside = not inside
    return inside


def _offset_closed_polygon(pts, r):
    """Offset a closed polygon's vertices outward (r > 0) or inward (r < 0).

    Uses the per-vertex edge-normal bisector, oriented away from the centroid so
    it works for either winding. Robust for convex outlines; concave ones can
    self-intersect at tight notches — acceptable for a first-pass tool
    compensation (review.md §6.2 H3).
    """
    if len(pts) < 3 or r == 0:
        return pts
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    out = []
    for i in range(n):
        prev = pts[(i - 1) % n]
        cur = pts[i]
        nxt = pts[(i + 1) % n]
        e1 = (cur[0] - prev[0], cur[1] - prev[1])
        e2 = (nxt[0] - cur[0], nxt[1] - cur[1])
        # edge normals (rotate +90: (dx, dy) -> (-dy, dx)), averaged at the vertex
        n1 = (-e1[1], e1[0])
        n2 = (-e2[1], e2[0])
        nx, ny = n1[0] + n2[0], n1[1] + n2[1]
        ln = math.hypot(nx, ny)
        if ln < 1e-9:
            out.append(cur)
            continue
        nx, ny = nx / ln, ny / ln
        # flip the bisector normal to point away from the centroid (outward)
        if (nx * (cur[0] - cx) + ny * (cur[1] - cy)) < 0:
            nx, ny = -nx, -ny
        out.append((cur[0] + nx * r, cur[1] + ny * r))
    return out


def to_gcode(doc: dict, feed: float = 300.0, plunge: float = 100.0,
             depth: float = -3.0, safe_z: float = 5.0, circle_seg: int = 64,
             with_grid: bool | None = None, tool: float = 0.0,
             pass_depth: float | None = None) -> str:
    """Basic profiling G-code: rapid to each outline, plunge, cut the perimeter.

    With the grid-export option on, the drafting grid is appended as a non-cutting
    rapid (G0) reference pass at the safe Z height.

    Maker improvements (review.md §6.2 H3):
      * ``tool`` — end-mill **diameter** in mm. Closed outlines are offset by
        half the diameter so the cut edge, not the centreline, matches the drawn
        shape; holes (closed outlines inside another) are offset inward.
        ``tool=0`` (default) keeps the legacy centreline behaviour.
      * ``pass_depth`` — maximum Z step per roughing pass. When the total
        ``depth`` exceeds it, the perimeter is cut in several stepped passes
        instead of one deep plunge. Defaults to a single pass (``|depth|``).
    """
    grid = with_grid if with_grid is not None else bool(doc.get("export_grid", False))
    by_id = {o["id"]: o for o in doc["objects"]}

    # Collect visible cut polylines in millimetres up front so we can detect
    # holes (for inward tool compensation) before emitting.
    outlines = []
    for o in doc["objects"]:
        if not _layer_visible(doc, o):
            continue
        for pl in _cut_polylines(o, doc, by_id, circle_seg):
            pts = [_to_mm(doc, px, py) for px, py in pl["pts"]]
            if len(pts) < 2:
                continue
            outlines.append({"pts": pts, "closed": pl["closed"]})

    # Hole detection: a closed outline whose centroid lies inside another closed
    # outline is treated as a hole and offset inward.
    r = abs(tool) / 2.0
    if r > 0:
        closed = [ol for ol in outlines if ol["closed"] and len(ol["pts"]) >= 3]
        for ol in closed:
            cx = sum(p[0] for p in ol["pts"]) / len(ol["pts"])
            cy = sum(p[1] for p in ol["pts"]) / len(ol["pts"])
            is_hole = any(
                other is not ol
                and other["closed"]
                and _point_in_polygon((cx, cy), other["pts"])
                for other in closed
            )
            ol["pts"] = _offset_closed_polygon(ol["pts"], -r if is_hole else r)

    L = [
        "; sketchDiy G-code export (mm, absolute)",
        "G21 ; millimetres",
        "G90 ; absolute positioning",
        "G17 ; XY plane",
        f"G0 Z{safe_z:.3f}",
    ]
    cut_depth = abs(depth)
    pd = pass_depth if pass_depth and pass_depth > 0 else cut_depth
    n_passes = max(1, math.ceil(cut_depth / pd)) if cut_depth > 0 else 1
    for ol in outlines:
        pts = ol["pts"]
        if len(pts) < 2:
            continue
        x0, y0 = pts[0]
        L.append(f"G0 X{x0:.3f} Y{y0:.3f}")
        for k in range(1, n_passes + 1):
            z = -min(k * pd, cut_depth)
            L.append(f"G1 Z{z:.3f} F{plunge:.1f}")
            seq = pts[1:] + (pts[:1] if ol["closed"] else [])
            for (x, y) in seq:
                L.append(f"G1 X{x:.3f} Y{y:.3f} F{feed:.1f}")
            L.append(f"G0 Z{safe_z:.3f}")
    if grid:
        L += _gcode_grid(doc, safe_z)
    L.append(f"G0 Z{safe_z:.3f}")
    L.append("M2 ; end of program")
    return "\n".join(L)
