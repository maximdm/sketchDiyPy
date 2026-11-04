"""Runs user Python for the Hybrid script console.

The console is **AST-sandboxed** (review.md §6.2 H1): before the code reaches
`exec`, `_validate_sandbox` rejects imports and any dunder attribute access
(e.g. `().__class__.__subclasses__()`), which is how a "restricted builtins"
sandbox is normally escaped. Together with a curated builtins map this closes the
obvious escapes while still allowing the console helpers (`rect`/`circle`/.../
`math`/`doc`) and ordinary Python.

It is still intended for a local, single-user DIY tool — not a sandbox for
hostile, untrusted input — but the trivial `exec`-escape is gone. A Pyodide
in-browser variant remains a future option if the project's no-CDN/offline rule
is ever relaxed.
"""

from __future__ import annotations

import ast as _ast
import copy
import math
import re

from .document import circle, connector, dimension, line, path, rect, text


# --- layout helpers (M4-style templates) ----------------------------------
# These mutate object geometry in place (rect/circle/text x,y; line endpoints)
# and return the same object list so calls can be chained. They borrow the
# *idea* of Tesserax's Row/Column/Grid layouts without the dependency: a small
# cell-based placement on top of the existing canonical schema.

def _path_bbox(d):
    """Centre + half-extents of an SVG path string (numbers paired as x,y)."""
    if not d:
        return None
    nums = [
        float(x)
        for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", d)
    ]
    if len(nums) < 2:
        return None
    xs, ys = nums[0::2], nums[1::2]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    return ((minx + maxx) / 2, (miny + maxy) / 2, (maxx - minx) / 2, (maxy - miny) / 2)


def _obj_size(o):
    t = o["type"]
    g = o.get("geometry", {})
    sx = abs(o.get("scaleX", 1) or 1)
    sy = abs(o.get("scaleY", 1) or 1)
    if t == "rect":
        return g["w"] * sx, g["h"] * sy
    if t == "circle":
        r = g["r"] * sx
        return 2 * r, 2 * r
    if t == "line":
        return abs(g["x2"] - g["x1"]), abs(g["y2"] - g["y1"])
    if t == "text":
        return max(10.0, len(g.get("text", "")) * g.get("fontSize", 24) * 0.6), g.get(
            "fontSize", 24
        )
    if t == "path":
        bb = _path_bbox(g.get("d", ""))
        if bb:
            return bb[2] * 2, bb[3] * 2
    if t == "dimension":
        return abs(g["x2"] - g["x1"]), abs(g["y2"] - g["y1"])
    return 50.0, 50.0


def _obj_move_center(o, cx, cy):
    t = o["type"]
    g = o.get("geometry", {})
    if t in ("rect", "circle", "text"):
        o["x"], o["y"] = cx, cy
    elif t == "line":
        mx, my = (g["x1"] + g["x2"]) / 2, (g["y1"] + g["y2"]) / 2
        dx, dy = cx - mx, cy - my
        g["x1"] += dx; g["y1"] += dy; g["x2"] += dx; g["y2"] += dy
    # path / dimension / connector: position is derived; left in place


def row(objs, gap=20, x0=0, y0=0, center_y=True):
    """Lay objects left-to-right; `gap` is the gap between them in px."""
    objs = list(objs)
    cursor = x0
    for o in objs:
        w, h = _obj_size(o)
        _obj_move_center(o, cursor + w / 2, y0 if center_y else y0 + h / 2)
        cursor += w + gap
    return objs


def column(objs, gap=20, x0=0, y0=0, center_x=True):
    """Lay objects top-to-bottom; `gap` is the gap between them in px."""
    objs = list(objs)
    cursor = y0
    for o in objs:
        w, h = _obj_size(o)
        _obj_move_center(o, x0 if center_x else x0 + w / 2, cursor + h / 2)
        cursor += h + gap
    return objs


def grid(objs, cols, gap_x=20, gap_y=20, x0=0, y0=0):
    """Lay objects in a grid of `cols` columns (row-major)."""
    objs = list(objs)
    cols = max(1, int(cols))
    rows = (len(objs) + cols - 1) // cols
    col_w = [0.0] * cols
    row_h = [0.0] * rows
    for i, o in enumerate(objs):
        r, c = divmod(i, cols)
        w, h = _obj_size(o)
        col_w[c] = max(col_w[c], w)
        row_h[r] = max(row_h[r], h)
    for i, o in enumerate(objs):
        r, c = divmod(i, cols)
        cx = x0 + sum(col_w[:c]) + c * gap_x + col_w[c] / 2
        cy = y0 + sum(row_h[:r]) + r * gap_y + row_h[r] / 2
        _obj_move_center(o, cx, cy)
    return objs


def _names_in_target(t) -> set:
    """All simple names bound by an assignment / loop target."""
    return {n.id for n in _ast.walk(t) if isinstance(n, _ast.Name)}


def _collect_defined_names(tree: _ast.Module) -> set:
    """Names bound by assignments, loops, and function defs in the script.

    Used so the AST gate can permit calls to user-defined functions / lambdas
    without also permitting calls to dangerous builtins (`os`, `eval`, ...).
    """
    names: set = set()
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, _ast.Assign):
            for t in node.targets:
                names |= _names_in_target(t)
        elif isinstance(node, _ast.For):
            names |= _names_in_target(node.target)
        elif isinstance(node, _ast.AugAssign):
            names |= _names_in_target(node.target)
        elif isinstance(node, (_ast.ListComp, _ast.SetComp, _ast.DictComp)):
            for gen in node.generators:
                names |= _names_in_target(gen.target)
    return names


def _validate_sandbox(code: str, allowed_names: set) -> None:
    """Reject dangerous Python *before* it reaches `exec` (review.md §6.2 H1).

    Closes the classic "restricted builtins" escape
    (`().__class__.__bases__[0].__subclasses__()`) by forbidding imports and any
    dunder attribute access, and refuses calls to names that aren't part of the
    console env or defined by the user's own script.
    """
    try:
        tree = _ast.parse(code, "<console>", "exec")
    except SyntaxError as e:  # noqa: BLE001 - surface to the user
        raise RuntimeError(f"SyntaxError: {e}") from e

    defined = _collect_defined_names(tree)
    problems: list = []

    class _Visitor(_ast.NodeVisitor):
        def visit_Import(self, node):           # noqa: N802
            problems.append("imports are not allowed")
        def visit_ImportFrom(self, node):        # noqa: N802
            problems.append("imports are not allowed")
        def visit_Global(self, node):            # noqa: N802
            problems.append("global is not allowed")
        def visit_Nonlocal(self, node):          # noqa: N802
            problems.append("nonlocal is not allowed")
        def visit_Attribute(self, node):         # noqa: N802
            if node.attr.startswith("_"):
                problems.append(
                    f"access to '{node.attr}' is not allowed (no dunder access)"
                )
            self.generic_visit(node)
        def visit_Call(self, node):              # noqa: N802
            f = node.func
            if isinstance(f, _ast.Name) and (
                f.id not in allowed_names and f.id not in defined
            ):
                problems.append(f"call to '{f.id}' is not allowed")
            self.generic_visit(node)

    _Visitor().visit(tree)
    if problems:
        raise RuntimeError(f"blocked: {problems[0]}")


def run(code: str, doc) -> dict:
    """Execute `code` with the document in scope; return the updated doc dict.

    Raises RuntimeError with a friendly message on failure.
    """
    safe_builtins = {
        "print": print,
        "range": range, "len": len, "list": list, "dict": dict,
        "tuple": tuple, "set": set, "min": min, "max": max, "abs": abs,
        "round": round, "int": int, "float": float, "str": str, "bool": bool,
        "sum": sum, "enumerate": enumerate, "zip": zip, "sorted": sorted,
        "map": map, "filter": filter, "reversed": reversed,
    }
    env = {
        "__builtins__": safe_builtins,
        "math": math,
        "doc": doc,
        # convenience: create + add in one call
        "rect": lambda x, y, w, h, **kw: doc.add(rect(x, y, w, h, **kw)),
        "circle": lambda x, y, r, **kw: doc.add(circle(x, y, r, **kw)),
        "line": lambda x1, y1, x2, y2, **kw: doc.add(line(x1, y1, x2, y2, **kw)),
        "path": lambda x, y, d, **kw: doc.add(path(x, y, d, **kw)),
        "text": lambda x, y, s, **kw: doc.add(text(x, y, s, **kw)),
        "dimension": lambda x1, y1, x2, y2, **kw: doc.add(
            dimension(x1, y1, x2, y2, **kw)
        ),
        # Connect two objects by semantic anchor (center/left/right/top/bottom).
        # Accepts object dicts (returns from rect/circle/...) or raw ids.
        "connect": lambda a, b, fa="center", ta="center": doc.add(
            connector(
                a["id"] if isinstance(a, dict) else a, fa,
                b["id"] if isinstance(b, dict) else b, ta,
            )
        ),
        # M4-style layout helpers: pass a list of objects (the values returned by
        # the shape helpers) and they are repositioned in place.
        "row": row,
        "column": column,
        "grid": grid,
        "add": doc.add,
        "clear": doc.clear,
    }
    # AST-sandbox the script before it can do anything (review.md §6.2 H1): this
    # is the gate that closes the `().__class__.__subclasses__()` style escape.
    allowed_names = set(env.keys()) | set(safe_builtins.keys())
    _validate_sandbox(code, allowed_names)
    # Transactional: snapshot the doc first so a script that errors part-way
    # through (after adding some shapes) can't leave the document half-mutated.
    # `to_dict()` shares the live `objects` list by reference, so we deep-copy:
    # otherwise the script's own `add()` calls would mutate the snapshot too,
    # and rollback would restore a partially-changed document. On success we
    # return the live doc; on failure we roll back to the pre-run snapshot.
    snapshot = copy.deepcopy(doc.to_dict())
    try:
        exec(compile(code, "<console>", "exec"), env)
    except Exception as e:  # noqa: BLE001 - surface to the user
        doc.load_dict(snapshot)
        raise RuntimeError(f"{type(e).__name__}: {e}") from e
    return doc.to_dict()
