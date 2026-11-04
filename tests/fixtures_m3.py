"""Shared fixtures for M3 (coordinate-convention) baseline + migration tests.

The scene is built as **raw canonical dicts in the current (pre-M3) schema** so
the tests pin what the *renderers* must keep producing, independent of how the
schema is later stored. See `docs/m3-migration.md`.

The two "schema snapshot" assertions in `test_m3_baseline.py`
(`test_rect_is_center_based_current_schema` / `test_line_is_absolute_current_schema`)
document today's mixed convention; they will be rewritten when M3 lands. The
*output-contract* tests below them are the regression net that must stay green.
"""

def scene_current_schema() -> dict:
    """One of every shape type, in the pre-M3 mixed-convention schema."""
    return {
        "units": "mm",
        "px_per_unit": 3.78,
        "width": 900,
        "height": 700,
        "background": "#ffffff",
        "grid_color": "#eef2f7",
        "grid_size": 20,
        "export_grid": False,
        "show_grid": True,
        "show_rulers": True,
        "theme": "light",
        "recolor_on_theme": False,
        "snap_grid": True,
        "snap_objects": True,
        "snap_page": True,
        "layers": [{"id": "layer-base", "name": "Layer 1", "visible": True,
                    "locked": False}],
        "plan": {"currency": "$", "buy": [], "steps": [], "parts": [], "notes": []},
        "objects": [
            # ---- center-based (pre-M3) ----
            {"id": "r1", "type": "rect", "x": 100, "y": 100, "rotation": 0,
             "scaleX": 1, "scaleY": 1, "geometry": {"w": 50, "h": 30},
             "style": {"fill": "#cfe8ff", "stroke": "#1f6feb", "strokeWidth": 3,
                       "fontSize": 24}, "meta": {}},
            {"id": "c1", "type": "circle", "x": 620, "y": 250, "rotation": 0,
             "scaleX": 1, "scaleY": 1, "geometry": {"r": 60},
             "style": {"fill": None, "stroke": "#d9480f", "strokeWidth": 3,
                       "fontSize": 24}, "meta": {}},
            {"id": "t1", "type": "text", "x": 450, "y": 120, "rotation": 0,
             "scaleX": 1, "scaleY": 1, "geometry": {"text": "DIY Sketch",
                       "fontSize": 40},
             "style": {"fill": None, "stroke": "#222", "strokeWidth": 1,
                       "fontSize": 40}, "meta": {}},
            # ---- absolute (pre-M3): x,y are 0, geometry carries the points ----
            {"id": "l1", "type": "line", "x": 0, "y": 0, "rotation": 0,
             "scaleX": 1, "scaleY": 1, "geometry": {"x1": 0, "y1": 0, "x2": 80,
                       "y2": 120},
             "style": {"fill": None, "stroke": "#2f9e44", "strokeWidth": 3,
                       "fontSize": 24}, "meta": {}},
            {"id": "d1", "type": "dimension", "x": 0, "y": 0, "rotation": 0,
             "scaleX": 1, "scaleY": 1, "geometry": {"x1": 200, "y1": 340,
                       "x2": 400, "y2": 340, "offset": 26},
             "style": {"fill": None, "stroke": "#1f6feb", "strokeWidth": 1.5,
                       "fontSize": 14}, "meta": {}},
            {"id": "p1", "type": "path", "x": 0, "y": 0, "rotation": 0,
             "scaleX": 1, "scaleY": 1,
             "geometry": {"d": "M10,10 L60,10 L60,60 L10,60 Z"},
             "style": {"fill": None, "stroke": "#000", "strokeWidth": 2,
                       "fontSize": 24}, "meta": {}},
        ],
    }


def _obj(doc: dict, oid: str) -> dict:
    return next(o for o in doc["objects"] if o["id"] == oid)
