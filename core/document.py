"""Canonical document model for sketchDiy.

A Document is a scene of `objects`. Each object uses a small, rendering-agnostic
schema so it can be (a) edited in the Fabric.js canvas on the frontend and
(b) rendered/exported by matplotlib or serialized to SVG on the backend.
"""

from __future__ import annotations

import uuid


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _default_layer() -> dict:
    """The single layer every new document starts with."""
    return {"id": "layer-base", "name": "Layer 1", "visible": True, "locked": False}


def _obj(typ, x, y, geometry, kw) -> dict:
    return {
        "id": _id(),
        "type": typ,
        "x": x,            # object origin in canvas px (center for rect/circle/text)
        "y": y,
        "rotation": kw.get("rotation", 0),
        "scaleX": kw.get("scaleX", 1),
        "scaleY": kw.get("scaleY", 1),
        "geometry": geometry,
        "style": {
            "fill": kw.get("fill", None),
            "stroke": kw.get("stroke", "#222222"),
            "strokeWidth": kw.get("strokeWidth", 2),
            "fontSize": kw.get("fontSize", 24),
        },
        "meta": kw.get("meta", {}),
    }


# ---- factory helpers (used by the script console / API) -------------------

def rect(x, y, w, h, **kw):
    return _obj("rect", x, y, {"w": w, "h": h}, kw)


def circle(x, y, r, **kw):
    return _obj("circle", x, y, {"r": r}, kw)


def line(x1, y1, x2, y2, **kw):
    return _obj("line", 0, 0, {"x1": x1, "y1": y1, "x2": x2, "y2": y2}, kw)


def path(x, y, d, **kw):
    return _obj("path", x, y, {"d": d}, kw)


def text(x, y, s, **kw):
    kw.setdefault("fontSize", kw.pop("fontSize", 24))
    return _obj("text", x, y, {"text": s, "fontSize": kw.get("fontSize", 24)}, kw)


def dimension(x1, y1, x2, y2, offset=26, **kw):
    """Measured dimension line between two points.

    `offset` is the perpendicular distance (px) of the dimension line from the
    measured segment; its sign picks the side. The label is rendered from the
    document's units at display/export time.
    """
    kw.setdefault("stroke", "#1f6feb")
    kw.setdefault("strokeWidth", 1.5)
    kw.setdefault("fontSize", 14)
    return _obj(
        "dimension", 0, 0,
        {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "offset": offset},
        kw,
    )


def connector(from_id, from_anchor, to_id, to_anchor, **kw):
    """A line that stays attached to two objects' anchors.

    `from_anchor` / `to_anchor` are semantic positions: `center`, `left`,
    `right`, `top`, `bottom` (resolved against the referenced object's bounds).
    The geometry stores the referenced ids, so the line follows the objects when
    they move.
    """
    kw.setdefault("stroke", "#495057")
    kw.setdefault("strokeWidth", 2)
    return _obj(
        "connector", 0, 0,
        {
            "from": {"id": from_id, "anchor": from_anchor},
            "to": {"id": to_id, "anchor": to_anchor},
        },
        kw,
    )


class Document:
    """In-memory document. Persistence/undo come in a later phase."""

    def __init__(self):
        self.units = "mm"
        # ~96dpi: 1 mm ~= 3.78 px. Lets the UI show real-world sizes.
        self.px_per_unit = 3.78
        self.width = 900
        self.height = 700
        self.background = "#ffffff"
        self.grid_color = "#eef2f7"
        self.grid_size = 20
        self.export_grid = False
        self.show_grid = True
        self.show_rulers = True
        self.theme = "light"
        self.recolor_on_theme = False
        self.snap_grid = True
        self.snap_objects = True
        self.snap_page = True
        self.layers = [_default_layer()]
        self.objects = []

    def to_dict(self):
        return {
            "units": self.units,
            "px_per_unit": self.px_per_unit,
            "width": self.width,
            "height": self.height,
            "background": self.background,
            "grid_color": self.grid_color,
            "grid_size": self.grid_size,
            "export_grid": self.export_grid,
            "show_grid": self.show_grid,
            "show_rulers": self.show_rulers,
            "theme": self.theme,
            "recolor_on_theme": self.recolor_on_theme,
            "snap_grid": self.snap_grid,
            "snap_objects": self.snap_objects,
            "snap_page": self.snap_page,
            "layers": self.layers,
            "objects": self.objects,
        }

    def load_dict(self, d: dict):
        """Replace the document contents from a previously saved dict.

        Unknown keys are ignored so older project files stay loadable; missing
        settings fall back to the current defaults.
        """
        self.units = d.get("units", self.units)
        self.px_per_unit = d.get("px_per_unit", self.px_per_unit)
        self.width = d.get("width", self.width)
        self.height = d.get("height", self.height)
        self.background = d.get("background", self.background)
        self.grid_color = d.get("grid_color", self.grid_color)
        self.grid_size = d.get("grid_size", self.grid_size)
        self.export_grid = d.get("export_grid", self.export_grid)
        self.show_grid = d.get("show_grid", self.show_grid)
        self.show_rulers = d.get("show_rulers", self.show_rulers)
        self.theme = d.get("theme", self.theme)
        self.recolor_on_theme = d.get("recolor_on_theme", self.recolor_on_theme)
        self.snap_grid = d.get("snap_grid", self.snap_grid)
        self.snap_objects = d.get("snap_objects", self.snap_objects)
        self.snap_page = d.get("snap_page", self.snap_page)
        layers = d.get("layers") or []
        # Always keep at least one layer; tolerate empty/missing layer lists from
        # older project files.
        self.layers = layers if layers else [_default_layer()]
        self.objects = d.get("objects", []) or []

    def add(self, obj):
        self.objects.append(obj)
        return obj

    def clear(self):
        self.objects = []

    def find(self, oid):
        for o in self.objects:
            if o["id"] == oid:
                return o
        return None

    def upsert(self, obj):
        existing = self.find(obj["id"])
        if existing:
            existing.update(obj)
        else:
            self.objects.append(obj)
        return obj

    def remove(self, oid):
        self.objects = [o for o in self.objects if o["id"] != oid]
