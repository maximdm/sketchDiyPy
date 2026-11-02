"""3D extrusion module for sketchDiy (build123d-backed, pure-Python fallback).

Turns the 2D sketch document into extruded solids and triangle meshes for
preview/export. build123d (and its Open Cascade kernel) is imported *lazily*
inside the functions that need it, so:

  * the 2D app still starts instantly and stays lightweight, and
  * a machine without build123d installed gets a graceful, dependency-free
    fallback: a simple prism mesh (fine for a preview and STL export, though
    concave outlines and holes are approximated).

Everything here is framework-agnostic Python, matching the rest of `core/`.
"""

from __future__ import annotations

import math

# Reuse the existing 2D -> mm outline extraction so the 3D path stays in sync
# with the DXF/G-code exporters (single source of truth for cut geometry).
from core.export import _cut_polylines, _layer_visible, _to_mm


# --------------------------------------------------------------------------
# Availability + 2D outline extraction
# --------------------------------------------------------------------------

def available() -> bool:
    """True if build123d (the Open Cascade kernel) is importable."""
    try:
        import build123d  # noqa: F401
        return True
    except Exception:
        return False


def doc_cut_outlines_mm(doc, objects=None, respect_visibility=True):
    """Cuttable outlines in millimetres (y-up), ready to extrude.

    `objects` is an optional list of canonical objects to use instead of the
    whole document. Each returned outline is
    ``{"pts": [(x, y), ...], "closed": bool}``. Only closed outlines form a
    solid when extruded; open ones (plain guide lines) are still returned but
    ignored by the solid builder.
    """
    # Accept either a Document instance or a plain dict (the exporters use dicts).
    if hasattr(doc, "to_dict"):
        doc = doc.to_dict()
    by_id = {o["id"]: o for o in doc["objects"]}
    src = objects if objects is not None else doc["objects"]
    outlines = []
    for o in src:
        if respect_visibility and not _layer_visible(doc, o):
            continue
        for pl in _cut_polylines(o, doc, by_id):
            pts = [tuple(_to_mm(doc, px, py)) for px, py in pl["pts"]]
            outlines.append({"pts": pts, "closed": pl["closed"]})
    return outlines


# --------------------------------------------------------------------------
# Pure-Python fallback: a simple extruded prism per closed outline
# --------------------------------------------------------------------------

def _prism_mesh(outlines, depth):
    """Triangle soup for a set of outlines: top/bottom caps + side walls.

    Each closed outline becomes a prism of `depth` mm. Capping uses a centroid
    fan, which is exact for convex outlines and an approximation for concave
    ones (good enough for a quick preview / STL export without build123d).
    """
    positions, indices = [], []
    for ol in outlines:
        pts = ol["pts"]
        if not ol["closed"] or len(pts) < 3:
            continue
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        n = len(pts)
        base = len(positions) // 3
        # top ring (z = 0) then bottom ring (z = depth)
        for (x, y) in pts:
            positions.extend((x, y, 0.0))
        for (x, y) in pts:
            positions.extend((x, y, depth))
        # top cap (fan from vertex 0)
        for i in range(1, n - 1):
            indices.extend((base + 0, base + i, base + i + 1))
        # bottom cap (reversed winding)
        for i in range(1, n - 1):
            indices.extend((base + n + 0, base + n + i + 1, base + n + i))
        # side walls
        for i in range(n):
            j = (i + 1) % n
            t0, t1 = base + i, base + j
            b0, b1 = base + n + i, base + n + j
            indices.extend((t0, t1, b1, t0, b1, b0))
    return positions, indices


def write_stl_ascii(positions, indices, path):
    """Write an ASCII STL from a mesh (works without build123d)."""
    with open(path, "w", encoding="ascii") as f:
        f.write("solid sketchDiy\n")
        for k in range(0, len(indices), 3):
            a = positions[indices[k] * 3:indices[k] * 3 + 3]
            b = positions[indices[k + 1] * 3:indices[k + 1] * 3 + 3]
            c = positions[indices[k + 2] * 3:indices[k + 2] * 3 + 3]
            # face normal via cross product
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            f.write(
                f"facet normal {nx/ln:.6f} {ny/ln:.6f} {nz/ln:.6f}\n"
                f"  outer loop\n"
                f"    vertex {a[0]:.6f} {a[1]:.6f} {a[2]:.6f}\n"
                f"    vertex {b[0]:.6f} {b[1]:.6f} {b[2]:.6f}\n"
                f"    vertex {c[0]:.6f} {c[1]:.6f} {c[2]:.6f}\n"
                f"  endloop\nendfacet\n"
            )
        f.write("endsolid sketchDiy\n")


def sanitize_mesh(positions, indices):
    """Clean a triangle soup, returning (positions, indices, info).

    With trimesh installed, degenerate/duplicate/unreferenced geometry is dropped
    and face winding is made consistent — this noticeably improves the *fallback*
    (no-build123d) meshes, which are built by hand and can carry zero-area
    triangles. Without trimesh the input is returned unchanged so callers never
    need to branch. `info` is a dict: {"available", "repaired", "removed",
    "watertight"}.
    """
    info = {"available": False, "repaired": False, "removed": 0, "watertight": None}
    try:
        import numpy as np
        import trimesh
    except Exception:
        return positions, indices, info
    info["available"] = True
    try:
        verts = np.asarray(positions, dtype=float).reshape(-1, 3)
        faces = np.asarray(indices, dtype=int).reshape(-1, 3)
    except Exception:
        return positions, indices, info
    if len(faces) == 0:
        return positions, indices, info
    before = len(faces)
    try:
        m = trimesh.Trimesh(vertices=verts, faces=faces, process=True, validate=True)
    except Exception:
        return positions, indices, info
    removed = before - len(m.faces)
    info["removed"] = int(removed)
    info["repaired"] = removed > 0
    try:
        info["watertight"] = bool(m.is_watertight)
    except Exception:
        info["watertight"] = None
    return m.vertices.flatten().tolist(), m.faces.flatten().tolist(), info


# --------------------------------------------------------------------------
# build123d-backed solid building + accurate tessellation
# --------------------------------------------------------------------------

def _point_in_polygon(pt, poly):
    x, y = pt
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


def _signed_area(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def _centroid(pts):
    if not pts:
        return (0.0, 0.0)
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _wire_from_pts(build123d, pts):
    V, Edge, Wire = build123d.Vector, build123d.Edge, build123d.Wire
    n = len(pts)
    edges = [
        Edge.make_line(V(pts[k][0], pts[k][1], 0.0),
                       V(pts[(k + 1) % n][0], pts[(k + 1) % n][1], 0.0))
        for k in range(n)
    ]
    return Wire(edges)


def _classify(closed_pts):
    """Classify closed outlines as top-level solid vs. hole by containment.

    Returns a list of dicts with 'pts' and 'parent' (index of the smallest
    containing outline, or None). One nesting level: a hole sits directly inside
    a solid.
    """
    info = []
    for pts in closed_pts:
        info.append({
            "pts": pts,
            "c": _centroid(pts),
            "area": abs(_signed_area(pts)),
            "parent": None,
        })
    for i, a in enumerate(info):
        for j, b in enumerate(info):
            if i == j:
                continue
            if _point_in_polygon(a["c"], b["pts"]) and b["area"] > a["area"]:
                if a["parent"] is None or b["area"] < info[a["parent"]]["area"]:
                    a["parent"] = j
    return info


def _closed_outlines(outlines):
    """Filter + de-duplicate outlines into a list of point lists (closed only)."""
    closed = []
    for o in outlines:
        pts = o["pts"]
        if not o["closed"] or len(pts) < 3:
            continue
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        closed.append(pts)
    return closed


def _build_faces(build123d, info):
    """Yield build123d Face objects (outer + immediate holes) for top-level solids."""
    from build123d import Face  # noqa: F401
    for i, a in enumerate(info):
        if a["parent"] is not None:
            continue
        hole_wires = [
            _wire_from_pts(build123d, b["pts"])
            for j, b in enumerate(info) if b["parent"] == i
        ]
        face = Face(_wire_from_pts(build123d, a["pts"]))
        if hole_wires:
            face = face.make_holes(hole_wires)
        yield face


def solid_from_outlines(outlines, depth):
    """Build an extruded solid (Part) from mm outlines, holes included.

    Returns a build123d Part, or raises ValueError/RuntimeError if nothing
    extrudable / build123d missing.
    """
    if not available():
        raise RuntimeError("build123d is not installed")
    import build123d
    from build123d import BuildPart, extrude  # noqa: F401

    closed = _closed_outlines(outlines)
    if not closed:
        raise ValueError("no closed outline to extrude")
    info = _classify(closed)

    parts = []
    for face in _build_faces(build123d, info):
        try:
            with BuildPart() as bp:
                extrude(face, amount=depth)
            parts.append(bp.part)
        except Exception:
            # Skip a degenerate/contaminated outline rather than fail the job.
            continue
    if not parts:
        raise ValueError("could not build a solid from the selected shapes")
    solid = parts[0]
    for s in parts[1:]:
        solid = solid + s  # fuse
    return solid


def solid_from_revolution(outlines, angle_deg=360.0, axis_x=None):
    """Build a solid of revolution by spinning closed outlines about the model's
    Y (vertical) axis. `axis_x` is the axis position along X; defaults to the
    minimum X of the profiles so the result is a solid body rather than a shell.
    """
    if not available():
        raise RuntimeError("build123d is not installed")
    import build123d
    from build123d import BuildPart, revolve, Axis  # noqa: F401

    closed = _closed_outlines(outlines)
    if not closed:
        raise ValueError("no closed profile to revolve")
    if axis_x is None:
        # Put the axis just outside the profile's left edge. Revolving a profile
        # that *touches* the axis is degenerate in Open Cascade, so nudge by a
        # sub-millimetre amount — invisible, but yields a valid (near-solid) body.
        axis_x = min(p[0] for poly in closed for p in poly) - 1e-3
    info = _classify(closed)
    axis = Axis((axis_x, 0.0, 0.0), (0.0, 1.0, 0.0))

    parts = []
    for face in _build_faces(build123d, info):
        try:
            with BuildPart() as bp:
                revolve(face, axis=axis, revolution_arc=angle_deg)
            parts.append(bp.part)
        except Exception:
            continue
    if not parts:
        raise ValueError("could not build a revolution from the selected profile")
    solid = parts[0]
    for s in parts[1:]:
        solid = solid + s
    return solid


def tessellate_solid(solid, tolerance=0.1):
    """Return (positions, indices) for a build123d solid."""
    verts, tris = solid.tessellate(tolerance)
    positions = []
    for v in verts:
        positions.extend((v.X, v.Y, v.Z))
    indices = []
    for t in tris:
        indices.extend((t[0], t[1], t[2]))
    return positions, indices


def write_stl(solid, path):
    import build123d
    build123d.export_stl(solid, path)


def write_step(solid, path):
    import build123d
    build123d.export_step(solid, path)


def build_solid(outlines, op="extrude", depth=10.0, angle_deg=360.0, axis_x=None):
    """Build a solid (Part) from mm outlines for the given operation.

    Returns a build123d Part, or raises ValueError/RuntimeError. Pure-Python
    callers (no build123d) get a RuntimeError — the worker falls back to the
    prism mesh for STL in that case.
    """
    if op == "revolve":
        return solid_from_revolution(outlines, angle_deg, axis_x)
    return solid_from_outlines(outlines, depth)


def export_solid(outlines, op, depth, angle_deg, fmt, path, axis_x=None):
    """Build the solid and write it to `path` as STL or STEP.

    Intended to run *inside* the process-pool worker so the Open Cascade work
    never blocks the server. When build123d is missing it falls back to a simple
    prism STL (extrude only); STEP and Revolve require build123d and raise a
    RuntimeError that the server maps to HTTP 400.
    """
    if not available():
        if fmt == "step":
            raise RuntimeError(
                "STEP export needs build123d (pip install build123d)"
            )
        if op == "revolve":
            raise RuntimeError(
                "Revolve needs build123d (pip install build123d)"
            )
        # Pure-Python fallback: a simple prism mesh written as ASCII STL.
        positions, indices = _prism_mesh(outlines, depth)
        positions, indices, _ = sanitize_mesh(positions, indices)
        write_stl_ascii(positions, indices, path)
        return

    solid = build_solid(outlines, op, depth, angle_deg, axis_x)
    if fmt == "step":
        write_step(solid, path)
    else:
        write_stl(solid, path)


# --------------------------------------------------------------------------
# Combined entry points used by the server
# --------------------------------------------------------------------------

def _bbox(positions):
    if not positions:
        return {"min": [0, 0, 0], "max": [0, 0, 0], "size": [0, 0, 0]}
    xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
    mn = [min(xs), min(ys), min(zs)]
    mx = [max(xs), max(ys), max(zs)]
    return {"min": mn, "max": mx, "size": [mx[i] - mn[i] for i in range(3)]}


def extrude_to_mesh(doc, objects=None, depth=10.0, accurate=True):
    """Return a preview mesh dict for the doc/selection.

    Uses build123d tessellation when available (accurate curved surfaces);
    otherwise falls back to a simple prism mesh. Always returns a mesh so the
    UI can preview even without build123d installed.
    """
    respect = objects is None
    outlines = doc_cut_outlines_mm(doc, objects, respect_visibility=respect)
    if not any(o["closed"] and len(o["pts"]) >= 3 for o in outlines):
        raise ValueError(
            "Select at least one closed shape to extrude (rect, circle, or a "
            "closed path)."
        )

    repair = None
    if accurate and available():
        try:
            solid = solid_from_outlines(outlines, depth)
            positions, indices = tessellate_solid(solid)
            used_accurate = True
        except Exception:
            positions, indices = _prism_mesh(outlines, depth)
            positions, indices, repair = sanitize_mesh(positions, indices)
            used_accurate = False
    else:
        positions, indices = _prism_mesh(outlines, depth)
        positions, indices, repair = sanitize_mesh(positions, indices)
        used_accurate = False

    return {
        "positions": positions,
        "indices": indices,
        "bbox": _bbox(positions),
        "accurate": used_accurate,
        "triangles": len(indices) // 3,
        "depth": depth,
        "op": "extrude",
        "build123d": available(),
        "trimesh": bool(repair and repair["available"]),
        "repaired": repair["removed"] if repair else 0,
        "watertight": repair["watertight"] if repair else None,
    }


def _revolve_surface_mesh(outlines, angle_deg=360.0, axis_x=None, segments=48):
    """Approximate a surface of revolution as a triangle mesh (no build123d).

    Each closed outline is treated as a profile; every profile edge is swept
    through `angle_deg` around the Y axis at `axis_x`. Partial angles get flat
    end caps. This is an approximation (a shell, not a watertight solid) used
    only when build123d is unavailable.
    """
    closed = _closed_outlines(outlines)
    if not closed:
        return [], []
    if axis_x is None:
        axis_x = min(p[0] for poly in closed for p in poly)
    positions, indices = [], []
    theta0, theta1 = 0.0, math.radians(angle_deg)
    steps = max(2, int(segments * max(1.0, angle_deg / 360.0)))
    dth = (theta1 - theta0) / steps if steps else 0.0

    def pt(r, h, t):
        return (axis_x + r * math.cos(t), h, r * math.sin(t))

    for poly in closed:
        prof = [(p[0] - axis_x, p[1]) for p in poly]
        edges = list(zip(prof, prof[1:] + prof[:1]))
        for (ra, ha), (rb, hb) in edges:
            base = len(positions) // 3
            for s in range(steps):
                t0 = theta0 + s * dth
                t1 = t0 + dth
                positions.extend(pt(ra, ha, t0))
                positions.extend(pt(rb, hb, t0))
                positions.extend(pt(rb, hb, t1))
                positions.extend(pt(ra, ha, t1))
                indices.extend((base, base + 1, base + 2, base, base + 2, base + 3))
                base += 4
    # End caps for partial revolutions.
    if angle_deg < 359.999:
        for poly in closed:
            prof = [(p[0] - axis_x, p[1]) for p in poly]
            m = len(prof)
            for t, order in ((theta0, 1), (theta1, -1)):
                base = len(positions) // 3
                for (r, h) in prof:
                    positions.extend(pt(r, h, t))
                for i in range(1, m - 1):
                    if order > 0:
                        indices.extend((base, base + i, base + i + 1))
                    else:
                        indices.extend((base, base + i + 1, base + i))
    return positions, indices


def revolve_to_mesh(doc, objects=None, angle_deg=360.0, axis_x=None, accurate=True):
    """Return a preview mesh dict for revolving the doc/selection (op="revolve").

    Uses build123d when available (accurate surface), otherwise falls back to a
    surface-of-revolution approximation.
    """
    respect = objects is None
    outlines = doc_cut_outlines_mm(doc, objects, respect_visibility=respect)
    closed = [o for o in outlines if o["closed"] and len(o["pts"]) >= 3]
    if not closed:
        raise ValueError("select a closed profile to revolve")

    repair = None
    if accurate and available():
        try:
            solid = solid_from_revolution(outlines, angle_deg, axis_x)
            positions, indices = tessellate_solid(solid)
            used_accurate = True
        except Exception:
            positions, indices = _revolve_surface_mesh(outlines, angle_deg, axis_x, 48)
            positions, indices, repair = sanitize_mesh(positions, indices)
            used_accurate = False
    else:
        positions, indices = _revolve_surface_mesh(outlines, angle_deg, axis_x, 48)
        positions, indices, repair = sanitize_mesh(positions, indices)
        used_accurate = False

    return {
        "positions": positions,
        "indices": indices,
        "bbox": _bbox(positions),
        "accurate": used_accurate,
        "triangles": len(indices) // 3,
        "depth": angle_deg,
        "op": "revolve",
        "build123d": available(),
        "trimesh": bool(repair and repair["available"]),
        "repaired": repair["removed"] if repair else 0,
        "watertight": repair["watertight"] if repair else None,
    }
