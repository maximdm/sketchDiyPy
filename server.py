"""FastAPI server for sketchDiy.

Endpoints:
  GET  /                 -> index.html
  GET  /api/doc          -> current document (JSON)
  POST /api/objects      -> add/replace an object (body = canonical object)
  PUT  /api/objects/{id}-> replace object by id
  DELETE /api/objects/{id}-> remove object by id
  POST /api/clear        -> remove all objects
  POST /api/run          -> run a Python snippet (Hybrid console)
  POST /api/trace        -> trace an uploaded image into `path` objects (M2)
  PUT  /api/settings     -> update units / scale / size
  GET  /api/export/svg   -> image/svg+xml
  GET  /api/export/png   -> image/png
  GET  /api/export/pdf   -> application/pdf
   GET  /api/export/dxf   -> application/dxf (M3)
   GET  /api/export/gcode -> text/plain (M3)
   GET  /api/projects     -> list saved project files (M5)
   POST /api/project/save -> save current doc to a named project (M5)
   POST /api/project/load -> load a named project into the doc (M5)
   DELETE /api/project/{name} -> remove a saved project (M5)
   POST /api/doc          -> full document replace (persist / undo / restore) (M5)
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from core import part3d, worker3d
from core.document import Document, circle, dimension, line, rect, text
from core.store import store, doc  # single-document store (review.md §6.2 H2)
from core.export import to_dxf, to_gcode, to_png_pdf, to_svg
from core.runner import run


@asynccontextmanager
async def _lifespan(app):  # noqa: A002 - FastAPI lifespan signature
    # Nothing to set up at start. On shutdown, release the 3D worker pool so the
    # Open Cascade kernel loaded in each worker process isn't orphaned.
    try:
        yield
    finally:
        worker3d.shutdown()


app = FastAPI(title="sketchDiy", lifespan=_lifespan)


@app.middleware("http")
async def no_cache(request: Request, call_next):
    # Always revalidate assets so local edits to app.js / index.html /
    # tailwind.css are picked up immediately. Without this, a browser that cached
    # the page during development shows stale code and "changes do nothing".
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


def current_doc(session_id: str | None = Header(default=None, alias="X-Session-Id")) -> Document:
    """Resolve the document for the active session (review.md §6.2 H2).

    Without an `X-Session-Id` header the shared default document is used, which
    preserves the original single-document behaviour and the existing tests. A
    session id routes every document-touching endpoint to an isolated document
    keyed in `store`, so multiple browser tabs / users no longer share one doc.
    """
    return store.get(session_id)


# `doc` is the default session document from core/store (review.md §6.2 H2).
# a little starter scene so the canvas isn't empty
doc.add(rect(300, 250, 200, 120, fill="#cfe8ff", stroke="#1f6feb", strokeWidth=3,
             meta={"label": "board"}))
doc.add(circle(620, 250, 60, fill=None, stroke="#d9480f", strokeWidth=3))
doc.add(line(150, 520, 750, 520, stroke="#2f9e44", strokeWidth=3))
doc.add(text(450, 120, "DIY Sketch", stroke="#222", fontSize=40))
doc.add(dimension(200, 340, 400, 340, offset=26))


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/api/doc")
def get_doc(doc: Document = Depends(current_doc)):
    return doc.to_dict()


@app.post("/api/objects")
def post_object(obj: dict, doc: Document = Depends(current_doc)):
    if "id" not in obj:
        raise HTTPException(400, "object requires an 'id'")
    doc.upsert(obj)
    return {"ok": True, "id": obj["id"]}


@app.put("/api/objects/{oid}")
def put_object(oid: str, obj: dict, doc: Document = Depends(current_doc)):
    if doc.find(oid) is None:
        raise HTTPException(404, "object not found")
    obj["id"] = oid
    doc.upsert(obj)
    return {"ok": True}


@app.delete("/api/objects/{oid}")
def delete_object(oid: str, doc: Document = Depends(current_doc)):
    if doc.find(oid) is None:
        raise HTTPException(404, "object not found")
    doc.remove(oid)
    # Cascade: drop any connectors that referenced the deleted object.
    doc.objects = [
        o for o in doc.objects
        if not (o.get("type") == "connector"
                and (o.get("geometry", {}).get("from", {}).get("id") == oid
                     or o.get("geometry", {}).get("to", {}).get("id") == oid))
    ]
    return {"ok": True}


@app.post("/api/clear")
def clear_objects(doc: Document = Depends(current_doc)):
    doc.clear()
    return {"ok": True}


@app.post("/api/run")
def run_code(payload: dict, doc: Document = Depends(current_doc)):
    code = payload.get("code", "")
    try:
        updated = run(code, doc)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return updated


@app.post("/api/trace")
async def trace_image(
    file: UploadFile = File(...),
    mode: str = Form("outline"),
    threshold: int = Form(128),
    invert: bool = Form(False),
    remove_bg: bool = Form(False),
    simplify: float = Form(1.5),
    min_area: float = Form(10.0),
    smooth: bool = Form(False),
    doc: Document = Depends(current_doc),
):
    """Trace an uploaded image into `path` objects.

    Read-only: the objects are *returned*, not added to the document, so the
    frontend can preview/commit them however it likes.
    """
    # heavy imaging deps are imported lazily so server start stays fast
    from core.trace import trace_from_bytes

    data = await file.read()
    if not data:
        raise HTTPException(400, "empty image upload")
    try:
        objects = trace_from_bytes(
            data,
            mode=mode,
            threshold=threshold,
            invert=invert,
            simplify=simplify,
            min_area=min_area,
            smooth=smooth,
            remove_bg=remove_bg,
            max_width=doc.width,
            max_height=doc.height,
        )
    except RuntimeError as e:  # e.g. rembg not installed
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001 - bad image, bad params, ...
        raise HTTPException(400, f"could not trace image: {e}") from e
    return {"objects": objects, "count": len(objects)}


@app.put("/api/settings")
def update_settings(payload: dict, doc: Document = Depends(current_doc)):
    for key in ("units", "px_per_unit", "width", "height", "background",
                "grid_color", "grid_size", "export_grid", "show_grid",
                "show_rulers", "theme", "recolor_on_theme", "snap_grid",
                "snap_objects", "snap_page", "layers", "plan"):
        if key in payload:
            setattr(doc, key, payload[key])
    return doc.to_dict()


@app.post("/api/doc")
def replace_doc(payload: dict, doc: Document = Depends(current_doc)):
    """Full document replace (used by the frontend to persist/undo/restore)."""
    doc.load_dict(payload)
    return doc.to_dict()


@app.get("/api/export/svg")
def export_svg(doc: Document = Depends(current_doc)):
    return Response(content=to_svg(doc.to_dict()), media_type="image/svg+xml")


@app.get("/api/export/png")
def export_png(doc: Document = Depends(current_doc)):
    return Response(content=to_png_pdf(doc.to_dict(), "png"),
                    media_type="image/png")


@app.get("/api/export/pdf")
def export_pdf(doc: Document = Depends(current_doc)):
    return Response(content=to_png_pdf(doc.to_dict(), "pdf"),
                    media_type="application/pdf")


@app.get("/api/export/dxf")
def export_dxf(doc: Document = Depends(current_doc)):
    """AutoCAD DXF (R2000) of cuttable outlines, in millimetres."""
    return Response(content=to_dxf(doc.to_dict()), media_type="application/dxf")


@app.get("/api/export/gcode")
def export_gcode(
    feed: float = 300.0,
    plunge: float = 100.0,
    depth: float = -3.0,
    safe: float = 5.0,
    tool: float = 0.0,
    pass_depth: float = 0.0,
    doc: Document = Depends(current_doc),
):
    """Basic profiling G-code (mm) with optional feed/plunge/depth/safe params.

    `tool` is the end-mill diameter (mm) — closed outlines are offset by half of
    it (holes inward) so the cut edge matches the drawing. `pass_depth` is the
    maximum Z step per roughing pass (0/omitted = single pass).
    """
    return Response(
        content=to_gcode(doc.to_dict(), feed=feed, plunge=plunge,
                         depth=depth, safe_z=safe, tool=tool,
                         pass_depth=pass_depth or None),
        media_type="text/plain",
    )


# --------------------------------------------------------------------------
# 3D: extrude the 2D sketch into solids (build123d-backed, optional dep).
# The 2D app stays lightweight — build123d is only touched on these routes.
# --------------------------------------------------------------------------

def _objects_for_ids(doc, ids):
    """Resolve a list of object ids (or None for the whole doc) to objects."""
    if not ids:
        return None  # caller treats None as "use the whole document"
    by_id = {o["id"]: o for o in doc.objects}
    objs = [by_id[i] for i in ids if i in by_id]
    if not objs:
        raise HTTPException(400, "none of the supplied ids exist in the document")
    return objs


@app.post("/api/3d/extrude")
async def build_3d(payload: dict, doc: Document = Depends(current_doc)):
    """Build a preview mesh from the doc/selection.

    Body: {"ids": [..] | null, "op": "extrude" | "revolve",
    "depth": <mm>, "angle": <deg>}. Returns triangle-soup JSON (positions +
    indices) for the frontend to render, plus a bounding box and accuracy flag.
    The heavy geometry work runs in a pool of worker *processes* (falling back to
    a thread) so the server stays responsive even for large/boolean-heavy models
    and a kernel crash can't take the server down.
    """
    ids = payload.get("ids") or None
    op = (payload.get("op") or "extrude").lower()
    if op not in ("extrude", "revolve", "sweep"):
        raise HTTPException(400, "op must be 'extrude', 'revolve' or 'sweep'")
    quality = payload.get("quality", "medium")
    try:
        depth = float(payload.get("depth", 10.0))
        angle = float(payload.get("angle", 360.0))
    except (TypeError, ValueError):
        raise HTTPException(400, "depth/angle must be numbers")
    axis = payload.get("axis", None)
    if axis is not None:
        try:
            axis = float(axis)
        except (TypeError, ValueError):
            raise HTTPException(400, "axis must be a number (mm)")
    if op == "revolve":
        if angle <= 0 or angle > 360:
            raise HTTPException(400, "angle must be 0 < angle <= 360")
    elif op == "extrude":
        if depth <= 0:
            raise HTTPException(400, "depth must be > 0")
    try:
        objs = _objects_for_ids(doc, ids)
    except HTTPException:
        raise
    try:
        if op == "revolve":
            mesh = await worker3d.run(
                part3d.revolve_to_mesh, doc.to_dict(), objs, angle, axis, True, quality
            )
        elif op == "sweep":
            mesh = await worker3d.run(
                part3d.sweep_to_mesh, doc.to_dict(), objs, True, quality
            )
        else:
            mesh = await worker3d.run(
                part3d.extrude_to_mesh, doc.to_dict(), objs, depth, True, quality
            )
    except worker3d.JobTimeoutError as e:
        raise HTTPException(504, str(e)) from e
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from e
    # build123d flag is computed inside the worker and returned on the mesh.
    mesh["build123d"] = mesh.get("build123d", False)
    return mesh


@app.post("/api/3d/primitive")
async def primitive_3d(payload: dict):
    """Return a preview mesh for a generative 3D primitive (box/sphere/...).

    Body: {"kind": "box" | "sphere" | "cylinder" | "cone" | "torus" | "capsule"
    | "pyramid", "params": {..}, "quality": "low" | "medium" | "high"}. Returns
    the same triangle-soup JSON as the extrude route so the viewer renders it.
    """
    kind = (payload.get("kind") or "").lower()
    if kind not in part3d.PRIMITIVE_KINDS:
        raise HTTPException(400, f"unknown primitive '{kind}'")
    quality = payload.get("quality", "medium")
    params = payload.get("params") or {}
    try:
        mesh = await worker3d.run(part3d.primitive_mesh, kind, params, quality)
    except worker3d.JobTimeoutError as e:
        raise HTTPException(504, str(e)) from e
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from e
    # build123d flag is computed inside the worker and returned on the mesh.
    mesh["build123d"] = mesh.get("build123d", False)
    return mesh


@app.post("/api/3d/export")
async def export_3d(payload: dict, doc: Document = Depends(current_doc)):
    """Export the solid as STL (always) or STEP (needs build123d).

    Body: {"ids": [..] | null, "op": "extrude" | "revolve", "depth": <mm>,
    "angle": <deg>, "format": "stl" | "step"}. STL works with or without
    build123d (accurate tessellation when present, else a simple prism).
    STEP requires build123d's Open Cascade kernel. The build + file write run in
    a pool of worker *processes* (falling back to a thread) so the server never
    blocks on Open Cascade.
    """
    fmt = (payload.get("format") or "stl").lower()
    if fmt not in ("stl", "step"):
        raise HTTPException(400, "format must be 'stl' or 'step'")
    # Scene merge: a list of {source, transform} items -> one merged file.
    items = payload.get("items")
    if items is not None:
        suffix = "stl" if fmt == "stl" else "step"
        media = "model/stl" if fmt == "stl" else "application/step"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix="." + suffix)
        tmp.close()
        path = Path(tmp.name)
        try:
            if fmt == "stl":
                await worker3d.run(
                    part3d.merge_scene_to_stl, doc.to_dict(), items, str(path)
                )
            else:
                await worker3d.run(
                    part3d.merge_scene_to_step, doc.to_dict(), items, str(path)
                )
        except worker3d.JobTimeoutError as e:
            path.unlink(missing_ok=True)
            raise HTTPException(504, str(e)) from e
        except (ValueError, RuntimeError) as e:
            path.unlink(missing_ok=True)
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001
            path.unlink(missing_ok=True)
            raise HTTPException(500, f"3D export failed: {e}") from e
        return FileResponse(
            path,
            media_type=media,
            filename=f"scene.{suffix}",
            background=BackgroundTask(path.unlink, missing_ok=True),
        )
    ids = payload.get("ids") or None
    op = (payload.get("op") or "extrude").lower()
    if op not in ("extrude", "revolve", "sweep"):
        raise HTTPException(400, "op must be 'extrude', 'revolve' or 'sweep'")
    quality = payload.get("quality", "medium")
    try:
        depth = float(payload.get("depth", 10.0))
        angle = float(payload.get("angle", 360.0))
    except (TypeError, ValueError):
        raise HTTPException(400, "depth/angle must be numbers")
    axis = payload.get("axis", None)
    if axis is not None:
        try:
            axis = float(axis)
        except (TypeError, ValueError):
            raise HTTPException(400, "axis must be a number (mm)")
    if op == "revolve":
        if angle <= 0 or angle > 360:
            raise HTTPException(400, "angle must be 0 < angle <= 360")
    elif op == "extrude":
        if depth <= 0:
            raise HTTPException(400, "depth must be > 0")
    if op == "sweep" and fmt == "step":
        # Sweep solids are fine to export as STEP too; nothing special to guard.
        pass

    try:
        objs = _objects_for_ids(doc, ids)
    except HTTPException:
        raise
    respect = objs is None
    outlines = part3d.doc_cut_outlines_mm(doc, objs, respect_visibility=respect)
    if op == "sweep":
        if not any(o["closed"] and len(o["pts"]) >= 3 for o in outlines):
            raise HTTPException(400, "sweep needs a closed profile")
        if not any(not o.get("closed") and len(o.get("pts", [])) >= 2 for o in outlines):
            raise HTTPException(
                400, "sweep needs an open path (a line/path), not just a closed shape"
            )
    elif not any(o["closed"] and len(o["pts"]) >= 3 for o in outlines):
        raise HTTPException(400, "select at least one closed shape to extrude/revolve")

    suffix = "stl" if fmt == "stl" else "step"
    media = "model/stl" if fmt == "stl" else "application/step"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix="." + suffix)
    tmp.close()
    path = Path(tmp.name)

    try:
        # Whole build + write happens in a worker process: the kernel work stays
        # off the event loop and a crash is contained to that process. The worker
        # also handles the pure-Python prism STL fallback when build123d is absent.
        await worker3d.run(
            part3d.export_solid, outlines, op, depth, angle, fmt, str(path), axis, quality
        )
    except worker3d.JobTimeoutError as e:
        path.unlink(missing_ok=True)
        raise HTTPException(504, str(e)) from e
    except (ValueError, RuntimeError) as e:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001 - OCP/IO errors
        path.unlink(missing_ok=True)
        raise HTTPException(500, f"3D export failed: {e}") from e

    return FileResponse(
        path,
        media_type=media,
        filename=f"sketch.{suffix}",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


# --------------------------------------------------------------------------
# M5: project persistence (named project files in ./projects, plus the
# frontend's localStorage autosave). The in-memory doc is the source of truth;
# these endpoints snapshot/restore it as JSON.
# --------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

PROJECTS_DIR = Path(__file__).parent / "projects"
PROJECTS_DIR.mkdir(exist_ok=True)


def _project_path(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_ .")
    safe = safe.strip().strip(".") or "untitled"
    return PROJECTS_DIR / f"{safe}.json"


@app.get("/api/projects")
def list_projects():
    files = sorted(PROJECTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    return [
        {"name": p.stem, "saved_at": int(p.stat().st_mtime)}
        for p in files
    ]


@app.post("/api/project/save")
def save_project(payload: dict, doc: Document = Depends(current_doc)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "project name required")
    data = dict(doc.to_dict())
    data["name"] = name
    data["saved_at"] = int(time.time())
    _project_path(name).write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True, "name": name}


@app.post("/api/project/load")
def load_project(payload: dict, doc: Document = Depends(current_doc)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "project name required")
    path = _project_path(name)
    if not path.exists():
        raise HTTPException(404, "project not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - corrupt file
        raise HTTPException(400, f"could not read project: {e}") from e
    doc.load_dict(data)
    return doc.to_dict()


@app.delete("/api/project/{name}")
def delete_project(name: str):
    path = _project_path(name)
    if path.exists():
        path.unlink()
    return {"ok": True, "name": name}


app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)
