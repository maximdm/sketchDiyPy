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


def _quality_params(quality="medium"):
    """Map a quality name to (tessellation tolerance mm, path segments)."""
    q = (quality or "medium").lower()
    if q == "low":
        return 0.4, 12
    if q == "high":
        return 0.04, 96
    return 0.15, 48  # medium


def _first_open_path(outlines):
    """Return the points of the first open (non-closed) outline, or None."""
    for o in outlines:
        if not o.get("closed") and len(o.get("pts", [])) >= 2:
            return o["pts"]
    return None


def solid_from_sweep(outlines, path_pts=None, quality="medium"):
    """Sweep the closed profiles along an open path (a curve) to make a solid.

    `outlines` is the same sketch outline list used everywhere else; it must
    contain at least one closed profile and (unless `path_pts` is given) exactly
    one open path, which becomes the sweep rail. build123d's `sweep` aligns the
    profile normal with the path tangent, which is degenerate for in-plane (2D)
    paths; to get a correct cross-section we rotate only the *path* so its start
    tangent points along +Z (the profile's normal), sweep, then rotate the
    resulting solid back. Profiles and path are in mm, XY plane. Requires
    build123d; there is no pure-Python fallback.
    """
    if not available():
        raise RuntimeError("build123d is not installed")
    import build123d
    from build123d import BuildPart, sweep, Edge, Vector, Axis

    profiles = list(_build_faces(build123d, _classify(_closed_outlines(outlines))))
    if not profiles:
        raise ValueError("no closed profile to sweep")
    if path_pts is None:
        path_pts = _first_open_path(outlines)
    if not path_pts or len(path_pts) < 2:
        raise ValueError(
            "sweep needs an open path (a non-closed sketch line/path) of 2+ points"
        )
    pts = [Vector(p[0], p[1], 0.0) for p in path_pts]
    if len(pts) >= 3:
        path = Edge.make_spline(pts)
    else:
        path = Edge.make_line(pts[0], pts[1])

    def rot_a_to_b(a, b):
        a = a.normalized(); b = b.normalized()
        cr = a.cross(b)
        if cr.length < 1e-9:
            return (Axis(Vector(0, 0, 0), Vector(0, 0, 1)),
                    0.0 if a.dot(b) > 0 else 180.0)
        cr = cr.normalized()
        angle = math.degrees(math.acos(max(-1.0, min(1.0, a.dot(b)))))
        return (Axis(Vector(0, 0, 0), Vector(cr.X, cr.Y, cr.Z)), angle)

    start_t = path.tangent_at(0.0)
    rax, rang = rot_a_to_b(start_t, Vector(0, 0, 1))
    raxI, rangI = rot_a_to_b(Vector(0, 0, 1), start_t)
    pathR = path.rotate(rax, rang)

    parts = []
    for face in profiles:
        try:
            with BuildPart() as bp:
                sweep(face, pathR)
            parts.append(bp.part.rotate(raxI, rangI))
        except Exception:
            continue
    if not parts:
        raise ValueError("could not sweep the profile along the path")
    solid = parts[0]
    for s in parts[1:]:
        solid = solid + s
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


def build_solid(outlines, op="extrude", depth=10.0, angle_deg=360.0, axis_x=None,
                quality="medium"):
    """Build a solid (Part) from mm outlines for the given operation.

    Returns a build123d Part, or raises ValueError/RuntimeError. Pure-Python
    callers (no build123d) get a RuntimeError — the worker falls back to the
    prism mesh for STL in that case.
    """
    if op == "revolve":
        return solid_from_revolution(outlines, angle_deg, axis_x)
    if op == "sweep":
        return solid_from_sweep(outlines, None, quality)
    return solid_from_outlines(outlines, depth)


def export_solid(outlines, op, depth, angle_deg, fmt, path, axis_x=None,
                 quality="medium"):
    """Build the solid and write it to `path` as STL or STEP.

    Intended to run *inside* the process-pool worker so the Open Cascade work
    never blocks the server. When build123d is missing it falls back to a simple
    prism STL (extrude only); STEP, Revolve and Sweep require build123d and raise
    a RuntimeError that the server maps to HTTP 400.
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
        if op == "sweep":
            raise RuntimeError(
                "Sweep needs build123d (pip install build123d)"
            )
        # Pure-Python fallback: a simple prism mesh written as ASCII STL.
        positions, indices = _prism_mesh(outlines, depth)
        positions, indices, _ = sanitize_mesh(positions, indices)
        write_stl_ascii(positions, indices, path)
        return

    solid = build_solid(outlines, op, depth, angle_deg, axis_x, quality)
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


def extrude_to_mesh(doc, objects=None, depth=10.0, accurate=True, quality="medium"):
    """Return a preview mesh dict for the doc/selection.

    Uses build123d tessellation when available (accurate curved surfaces);
    otherwise falls back to a simple prism mesh. Always returns a mesh so the
    UI can preview even without build123d installed. `quality` controls the
    tessellation tolerance ("low"/"medium"/"high").
    """
    respect = objects is None
    outlines = doc_cut_outlines_mm(doc, objects, respect_visibility=respect)
    if not any(o["closed"] and len(o["pts"]) >= 3 for o in outlines):
        raise ValueError(
            "Select at least one closed shape to extrude (rect, circle, or a "
            "closed path)."
        )

    tol, _ = _quality_params(quality)
    repair = None
    if accurate and available():
        try:
            solid = solid_from_outlines(outlines, depth)
            positions, indices = tessellate_solid(solid, tol)
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
        "quality": quality,
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


def revolve_to_mesh(doc, objects=None, angle_deg=360.0, axis_x=None, accurate=True,
                    quality="medium"):
    """Return a preview mesh dict for revolving the doc/selection (op="revolve").

    Uses build123d when available (accurate surface), otherwise falls back to a
    surface-of-revolution approximation. When `axis_x` is None the effective
    auto axis (min profile X − 1e-3, the value the kernel actually uses) is
    computed and returned in the result so the viewer can draw it.
    """
    respect = objects is None
    outlines = doc_cut_outlines_mm(doc, objects, respect_visibility=respect)
    closed = [o for o in outlines if o["closed"] and len(o["pts"]) >= 3]
    if not closed:
        raise ValueError("select a closed profile to revolve")

    # Compute the effective axis the kernel will use, so the viewer can draw it
    # even for an "auto" axis the user didn't specify explicitly.
    effective_axis = axis_x
    if effective_axis is None:
        closed_pts = _closed_outlines(outlines)
        if closed_pts:
            effective_axis = min(p[0] for poly in closed_pts for p in poly) - 1e-3

    tol, segs = _quality_params(quality)
    repair = None
    if accurate and available():
        try:
            solid = solid_from_revolution(outlines, angle_deg, axis_x)
            positions, indices = tessellate_solid(solid, tol)
            used_accurate = True
        except Exception:
            positions, indices = _revolve_surface_mesh(outlines, angle_deg, axis_x, segs)
            positions, indices, repair = sanitize_mesh(positions, indices)
            used_accurate = False
    else:
        positions, indices = _revolve_surface_mesh(outlines, angle_deg, axis_x, segs)
        positions, indices, repair = sanitize_mesh(positions, indices)
        used_accurate = False

    return {
        "positions": positions,
        "indices": indices,
        "bbox": _bbox(positions),
        "accurate": used_accurate,
        "triangles": len(indices) // 3,
        # `angle` is the revolution sweep; `axis` is the spin-axis X (mm) actually
        # used: the explicit value when given, otherwise the computed auto axis.
        "angle": angle_deg,
        "axis": effective_axis,
        "op": "revolve",
        "quality": quality,
        "build123d": available(),
        "trimesh": bool(repair and repair["available"]),
        "repaired": repair["removed"] if repair else 0,
        "watertight": repair["watertight"] if repair else None,
    }


def sweep_to_mesh(doc, objects=None, accurate=True, quality="medium"):
    """Return a preview mesh dict for sweeping the doc/selection (op="sweep").

    The selection must contain a closed profile plus an open path (the rail).
    Builds a build123d solid and tessellates it — there is no pure-Python
    fallback, so a clear error is returned when build123d is absent or the
    selection is incomplete.
    """
    respect = objects is None
    outlines = doc_cut_outlines_mm(doc, objects, respect_visibility=respect)
    closed = [o for o in outlines if o["closed"] and len(o["pts"]) >= 3]
    path = _first_open_path(outlines)
    if not closed:
        raise ValueError(
            "Sweep needs a closed profile to sweep (a rect, circle, or closed path)."
        )
    if not path or len(path) < 2:
        raise ValueError(
            "Sweep needs an open path (a line/path), not just a closed shape."
        )
    if not available():
        raise RuntimeError("Sweep needs build123d (pip install build123d)")

    tol, _ = _quality_params(quality)
    try:
        solid = solid_from_sweep(outlines, None, quality)
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001 - Open Cascade / sweep failures
        raise ValueError(f"sweep failed: {e}") from e

    try:
        positions, indices = tessellate_solid(solid, tol)
        used_accurate = True
    except Exception:  # noqa: BLE001 - should not normally happen post-success
        raise ValueError("could not tessellate the swept solid")

    return {
        "positions": positions,
        "indices": indices,
        "bbox": _bbox(positions),
        "accurate": used_accurate,
        "triangles": len(indices) // 3,
        "op": "sweep",
        "quality": quality,
        "build123d": available(),
        "trimesh": False,
        "repaired": 0,
        "watertight": None,
    }


# --------------------------------------------------------------------------
# Generative 3D primitives
# --------------------------------------------------------------------------
# A library of standalone solids the user can drop straight into the 3D scene
# without drawing a 2D profile first. Each has pure-Python mesh generation (so
# a preview + STL export work with no extra dependencies) and, when build123d is
# present, an exact CSG solid for accurate tessellation and STEP export.

PRIMITIVE_KINDS = ("box", "sphere", "cylinder", "cone", "torus", "capsule",
                   "pyramid")
PRIMITIVE_DEFAULTS = {
    "box": {"w": 40.0, "h": 40.0, "d": 40.0},
    "sphere": {"r": 25.0},
    "cylinder": {"r": 20.0, "h": 40.0},
    "cone": {"r": 20.0, "h": 40.0},
    "torus": {"R": 30.0, "r": 10.0},
    "capsule": {"r": 15.0, "h": 30.0},
    "pyramid": {"w": 40.0, "h": 40.0},
}
# Frontend rendering hints: ordered (param, label) pairs.
PRIMITIVE_PARAM_SPECS = {
    "box": [("w", "Width"), ("h", "Height"), ("d", "Depth")],
    "sphere": [("r", "Radius")],
    "cylinder": [("r", "Radius"), ("h", "Height")],
    "cone": [("r", "Radius"), ("h", "Height")],
    "torus": [("R", "Major R"), ("r", "Minor R")],
    "capsule": [("r", "Radius"), ("h", "Height")],
    "pyramid": [("w", "Base"), ("h", "Height")],
}


def _seg(quality, base=32):
    q = (quality or "medium").lower()
    if q == "low":
        return max(8, base // 2)
    if q == "high":
        return base * 2
    return base


def _box(w, h, d):
    x, y, z = w / 2, h / 2, d / 2
    v = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z),
         (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    faces = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
             (0, 5, 1), (0, 4, 5), (2, 6, 7), (2, 7, 3),
             (3, 7, 4), (3, 4, 0), (1, 5, 6), (1, 6, 2)]
    positions = [c for p in v for c in p]
    indices = [i for f in faces for i in f]
    return positions, indices


def _sphere(r, seg):
    positions, indices = [], []
    stack = seg
    for i in range(stack + 1):
        phi = math.pi * i / stack
        for j in range(seg + 1):
            theta = 2 * math.pi * j / seg
            positions.extend((r * math.sin(phi) * math.cos(theta),
                              r * math.sin(phi) * math.sin(theta),
                              r * math.cos(phi)))
    idx = lambda i, j: i * (seg + 1) + j
    for i in range(stack):
        for j in range(seg):
            a, b, c, d = idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)
            indices.extend((a, b, d, b, c, d))
    return positions, indices


def _cylinder(r, h, seg):
    z0, z1 = -h / 2, h / 2
    positions, indices = [], []
    for j in range(seg):
        a0, a1 = 2 * math.pi * j / seg, 2 * math.pi * (j + 1) / seg
        x0, y0, x1, y1 = r * math.cos(a0), r * math.sin(a0), r * math.cos(a1), r * math.sin(a1)
        base = len(positions) // 3
        positions.extend((x0, y0, z0, x1, y1, z0, x1, y1, z1, x0, y0, z1))
        indices.extend((base, base + 1, base + 2, base, base + 2, base + 3))
    ctop = len(positions) // 3
    positions.extend((0, 0, z1))
    for j in range(seg):
        a0, a1 = 2 * math.pi * j / seg, 2 * math.pi * (j + 1) / seg
        base = len(positions) // 3
        positions.extend((r * math.cos(a0), r * math.sin(a0), z1,
                          r * math.cos(a1), r * math.sin(a1), z1))
        indices.extend((ctop, base, base + 1))
    cbot = len(positions) // 3
    positions.extend((0, 0, z0))
    for j in range(seg):
        a0, a1 = 2 * math.pi * j / seg, 2 * math.pi * (j + 1) / seg
        base = len(positions) // 3
        positions.extend((r * math.cos(a0), r * math.sin(a0), z0,
                          r * math.cos(a1), r * math.sin(a1), z0))
        indices.extend((cbot, base + 1, base))
    return positions, indices


def _cone(r, h, seg):
    z0, z1 = -h / 2, h / 2
    positions, indices = [], []
    for j in range(seg):
        a0, a1 = 2 * math.pi * j / seg, 2 * math.pi * (j + 1) / seg
        x0, y0, x1, y1 = r * math.cos(a0), r * math.sin(a0), r * math.cos(a1), r * math.sin(a1)
        base = len(positions) // 3
        positions.extend((x0, y0, z0, x1, y1, z0, 0, 0, z1))
        indices.extend((base, base + 1, base + 2))
    cbot = len(positions) // 3
    positions.extend((0, 0, z0))
    for j in range(seg):
        a0, a1 = 2 * math.pi * j / seg, 2 * math.pi * (j + 1) / seg
        base = len(positions) // 3
        positions.extend((r * math.cos(a0), r * math.sin(a0), z0,
                          r * math.cos(a1), r * math.sin(a1), z0))
        indices.extend((cbot, base + 1, base))
    return positions, indices


def _torus(R, r, segU, segV):
    positions, indices = [], []
    for i in range(segU):
        u = 2 * math.pi * i / segU
        for k in range(segV):
            v = 2 * math.pi * k / segV
            positions.extend(((R + r * math.cos(v)) * math.cos(u),
                              (R + r * math.cos(v)) * math.sin(u),
                              r * math.sin(v)))
    idx = lambda i, k: i * segV + k
    for i in range(segU):
        for k in range(segV):
            a = idx(i, k)
            b = idx((i + 1) % segU, k)
            c = idx((i + 1) % segU, (k + 1) % segV)
            d = idx(i, (k + 1) % segV)
            indices.extend((a, b, d, b, c, d))
    return positions, indices


def _capsule(r, h, seg):
    positions, indices = [], []

    def hemisphere(cz, sign):
        stack = max(4, seg // 2)
        start = len(positions) // 3
        for i in range(stack + 1):
            phi = math.pi / 2 * i / stack
            zoff = sign * r * math.sin(phi)
            ring = r * math.cos(phi)
            for j in range(seg):
                a = 2 * math.pi * j / seg
                positions.extend((ring * math.cos(a), ring * math.sin(a), cz + zoff))
        idx = lambda i, j: start + i * seg + j
        for i in range(stack):
            for j in range(seg):
                a, b, c, d = idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)
                if sign > 0:
                    indices.extend((a, b, d, b, c, d))
                else:
                    indices.extend((a, d, b, b, d, c))

    zc0, zc1 = -h / 2, h / 2
    for j in range(seg):
        a0, a1 = 2 * math.pi * j / seg, 2 * math.pi * (j + 1) / seg
        x0, y0, x1, y1 = r * math.cos(a0), r * math.sin(a0), r * math.cos(a1), r * math.sin(a1)
        base = len(positions) // 3
        positions.extend((x0, y0, zc0, x1, y1, zc0, x1, y1, zc1, x0, y0, zc1))
        indices.extend((base, base + 1, base + 2, base, base + 2, base + 3))
    hemisphere(zc1, 1)
    hemisphere(zc0, -1)
    return positions, indices


def _pyramid(w, h):
    x = w / 2
    base = [(-x, -x, -h / 2), (x, -x, -h / 2), (x, x, -h / 2), (-x, x, -h / 2)]
    apex = (0, 0, h / 2)
    positions, indices = [], []
    b = [len(positions) // 3 + i for i, p in enumerate(base)]
    positions.extend(apex)
    apex_i = len(positions) // 3 - 1
    indices.extend((b[0], b[2], b[1], b[0], b[3], b[2]))
    for i in range(4):
        j = (i + 1) % 4
        indices.extend((b[i], apex_i, b[j]))
    return positions, indices


def primitive_mesh(kind, params=None, quality="medium"):
    """Return a preview mesh dict for a generative 3D primitive.

    Uses build123d tessellation when available (accurate curved surfaces),
    otherwise the pure-Python mesh generator. Always returns a mesh dict in the
    same shape as the extrude/revolve routes so the 3D viewer can render it.
    """
    if kind not in PRIMITIVE_KINDS:
        raise ValueError(f"unknown primitive '{kind}'")
    p = dict(PRIMITIVE_DEFAULTS.get(kind, {}))
    if params:
        p.update({k: float(v) for k, v in params.items() if k in p})
    seg = _seg(quality)
    if kind == "box":
        pos, idx = _box(p["w"], p["h"], p["d"])
    elif kind == "sphere":
        pos, idx = _sphere(p["r"], max(8, seg))
    elif kind == "cylinder":
        pos, idx = _cylinder(p["r"], p["h"], max(8, seg))
    elif kind == "cone":
        pos, idx = _cone(p["r"], p["h"], max(8, seg))
    elif kind == "torus":
        pos, idx = _torus(p["R"], p["r"], max(8, seg), max(6, seg // 2))
    elif kind == "capsule":
        pos, idx = _capsule(p["r"], p["h"], max(8, seg))
    elif kind == "pyramid":
        pos, idx = _pyramid(p["w"], p["h"])
    else:
        raise ValueError(f"unknown primitive '{kind}'")

    used_accurate = False
    if available():
        try:
            solid = primitive_to_solid(kind, p)
            pos2, idx2 = tessellate_solid(solid, _quality_params(quality)[0])
            if pos2:
                pos, idx = pos2, idx2
                used_accurate = True
        except Exception:
            pass

    return {
        "positions": pos,
        "indices": idx,
        "bbox": _bbox(pos),
        "accurate": used_accurate,
        "triangles": len(idx) // 3,
        "op": "primitive",
        "kind": kind,
        "params": p,
        "build123d": available(),
        "trimesh": False,
        "repaired": 0,
        "watertight": None,
    }


def primitive_to_solid(kind, params):
    """Build an exact build123d solid for the primitive (STEP export, accurate).

    Raises RuntimeError if build123d is unavailable; the caller falls back to the
    pure-Python mesh. The pyramid is stitched from its 5 corner faces because
    build123d has no dedicated square-pyramid primitive.
    """
    if not available():
        raise RuntimeError("build123d is not installed")
    from build123d import (Box, Sphere, Cylinder, Cone, Torus, Vector,
                           Shell, Wire, Face, Solid)
    p = dict(PRIMITIVE_DEFAULTS.get(kind, {}))
    p.update({k: float(v) for k, v in (params or {}).items() if k in p})
    if kind == "box":
        return Box(p["w"], p["h"], p["d"])
    if kind == "sphere":
        return Sphere(p["r"])
    if kind == "cylinder":
        return Cylinder(p["r"], p["h"])
    if kind == "cone":
        return Cone(p["r"], 0.0, p["h"])
    if kind == "torus":
        return Torus(p["R"], p["r"])
    if kind == "capsule":
        r, h = p["r"], p["h"]
        return (Cylinder(r, h)
                + Sphere(r).translate(Vector(0, 0, h / 2))
                + Sphere(r).translate(Vector(0, 0, -h / 2)))
    if kind == "pyramid":
        w, h = p["w"], p["h"]
        x = w / 2
        verts = [Vector(-x, -x, -h / 2), Vector(x, -x, -h / 2),
                 Vector(x, x, -h / 2), Vector(-x, x, -h / 2), Vector(0, 0, h / 2)]
        apex = verts[4]
        faces = [Face(Wire.make_polygon([verts[0], verts[1], verts[2], verts[3], verts[0]]))]
        for i in range(4):
            j = (i + 1) % 4
            faces.append(Face(Wire.make_polygon([verts[i], verts[j], apex, verts[i]])))
        return Solid(Shell(faces))
    raise ValueError(f"unknown primitive '{kind}'")


# --------------------------------------------------------------------------
# Scene export: merge several items (primitives + 2D-builds) into one file
# --------------------------------------------------------------------------

def _resolve_objs(doc, ids):
    """Resolve object ids to a list of canonical objects (or None = whole doc)."""
    if hasattr(doc, "to_dict"):
        doc = doc.to_dict()
    if not ids:
        return None
    by_id = {o["id"]: o for o in doc["objects"]}
    objs = [by_id[i] for i in ids if i in by_id]
    return objs or None


def _apply_transform(positions, transform):
    """Apply a per-item transform (scale, euler XYZ rotation deg, translation mm)."""
    if not transform:
        return positions
    pos = transform.get("pos") or {"x": 0, "y": 0, "z": 0}
    rot = transform.get("rot") or {"x": 0, "y": 0, "z": 0}
    sc = transform.get("scale", 1) or 1
    if isinstance(sc, dict):
        sxs, sys_, szs = sc.get("x", 1), sc.get("y", 1), sc.get("z", 1)
    else:
        sxs = sys_ = szs = sc
    rx, ry, rz = math.radians(rot.get("x", 0)), math.radians(rot.get("y", 0)), math.radians(rot.get("z", 0))
    cx, sxr = math.cos(rx), math.sin(rx)
    cy, syr = math.cos(ry), math.sin(ry)
    cz, szr = math.cos(rz), math.sin(rz)
    px, py, pz = pos.get("x", 0), pos.get("y", 0), pos.get("z", 0)
    out = []
    for i in range(0, len(positions), 3):
        x, y, z = positions[i] * sxs, positions[i + 1] * sys_, positions[i + 2] * szs
        y, z = y * cx - z * sxr, y * sxr + z * cx          # rot X
        x, z = x * cy + z * syr, -x * syr + z * cy          # rot Y
        x, y = x * cz - y * szr, x * szr + y * cz           # rot Z
        out.extend((x + px, y + py, z + pz))
    return out


def scene_item_mesh(doc, item):
    """Return (positions, indices) for one scene item, transformed into place."""
    src = item.get("source", {})
    quality = src.get("quality", "medium")
    if src.get("type") == "primitive":
        m = primitive_mesh(src.get("kind"), src.get("params", {}), quality)
    else:
        op = (src.get("op") or "extrude").lower()
        objs = _resolve_objs(doc, src.get("ids"))
        if op == "revolve":
            m = revolve_to_mesh(doc, objs, src.get("angle", 360), src.get("axis"),
                                True, quality)
        elif op == "sweep":
            m = sweep_to_mesh(doc, objs, True, quality)
        else:
            m = extrude_to_mesh(doc, objs, src.get("depth", 10), True, quality)
    positions = list(m["positions"])
    # Centre the geometry on its own centroid so per-item transforms rotate about
    # the item's centre — matching exactly how the viewer places each item.
    if positions:
        n = len(positions) // 3
        c = [sum(positions[i * 3 + k] for i in range(n)) / n for k in range(3)]
        positions = [positions[i] - c[i % 3] for i in range(len(positions))]
    pos = _apply_transform(positions, item.get("transform"))
    return pos, m["indices"]


def merge_scene_to_stl(doc, items, path):
    """Write all scene items as one merged ASCII STL (always available)."""
    positions, indices = [], []
    for it in items:
        pos, idx = scene_item_mesh(doc, it)
        base = len(positions) // 3
        positions.extend(pos)
        indices.extend(base + i for i in idx)
    positions, indices, _ = sanitize_mesh(positions, indices)
    write_stl_ascii(positions, indices, path)
    return {"triangles": len(indices) // 3}


def _transform_solid(solid, transform):
    """Apply a scene-item transform to a build123d solid (for STEP export)."""
    if not transform:
        return solid
    from build123d import Vector, Axis
    pos = transform.get("pos") or {"x": 0, "y": 0, "z": 0}
    rot = transform.get("rot") or {"x": 0, "y": 0, "z": 0}
    sc = transform.get("scale", 1) or 1
    try:
        if sc and sc != 1:
            if isinstance(sc, dict):
                if sc.get("x", 1) != 1 or sc.get("y", 1) != 1 or sc.get("z", 1) != 1:
                    solid = solid.scale((sc.get("x", 1), sc.get("y", 1), sc.get("z", 1)))
            else:
                solid = solid.scale(sc)
    except Exception:
        pass
    if rot.get("z", 0):
        solid = solid.rotate(Axis(Vector(0, 0, 0), Vector(0, 0, 1)), rot["z"])
    if rot.get("y", 0):
        solid = solid.rotate(Axis(Vector(0, 0, 0), Vector(0, 1, 0)), rot["y"])
    if rot.get("x", 0):
        solid = solid.rotate(Axis(Vector(0, 0, 0), Vector(1, 0, 0)), rot["x"])
    px, py, pz = pos.get("x", 0), pos.get("y", 0), pos.get("z", 0)
    if px or py or pz:
        solid = solid.translate(Vector(px, py, pz))
    return solid


def merge_scene_to_step(doc, items, path):
    """Build + fuse every scene item's solid and export as STEP (needs build123d)."""
    if not available():
        raise RuntimeError("STEP export needs build123d (pip install build123d)")
    solids = []
    for it in items:
        src = it.get("source", {})
        if src.get("type") == "primitive":
            s = primitive_to_solid(src.get("kind"), src.get("params", {}))
        else:
            op = (src.get("op") or "extrude").lower()
            objs = _resolve_objs(doc, src.get("ids"))
            outlines = doc_cut_outlines_mm(doc, objs, respect_visibility=objs is None)
            s = build_solid(outlines, op, src.get("depth", 10), src.get("angle", 360),
                           src.get("axis"), src.get("quality", "medium"))
        solids.append(_transform_solid(s, it.get("transform")))
    fused = solids[0]
    for s in solids[1:]:
        fused = fused + s
    write_step(fused, path)
