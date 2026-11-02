# sketchDiy

[![CI](https://github.com/maximdm/sketchDiyPy/actions/workflows/ci.yml/badge.svg)](https://github.com/maximdm/sketchDiyPy/actions/workflows/ci.yml)

A DIY-focused vector design tool — think CorelDRAW, but for makers. Draw and
lay out projects in the browser, drive generation with Python, and export to the
formats cutters, lasers, and printers actually use.

> Status: **Phase 0 + M1 done; M2 second slice in.** Drawing surface, Python
> console, exports, snapping (grid + objects), align/distribute, nudge,
> dimension lines, rulers + scale bar, lock/unlock, and image → path tracing with
> optional curve smoothing. See "Roadmap".

> **3D extrude:** select shapes (or the whole page) and extrude the sketch into a
> solid via **build123d**. Preview it in a built-in 3D viewer (drag to rotate,
> scroll to zoom) and export **STL** or **STEP**. build123d is an *optional*
> dependency — without it the 3D panel still works using a lightweight prism
> mesh + STL fallback.

## Run it

```powershell
cd sketchDiy
python -m venv venv            # already created
venv\Scripts\python -m pip install -r requirements.txt   # base, lightweight (2D + tests)
# Optional 3D features (accurate STL/STEP + mesh cleanup):
venv\Scripts\python -m pip install -r requirements-3d.txt
venv\Scripts\python -m uvicorn server:app --reload
```

Open http://127.0.0.1:8000

> `--reload` runs the app under a reloader (handy for development). The 3D work
> runs in a separate process pool; on reload the old workers are released via an
> `atexit` hook, so brief worker processes during dev reloads are expected. For
> production, drop `--reload`.

> Install shortcuts (equivalent, via pyproject extras): `pip install .` (base),
> `pip install .[3d]` (base + build123d + trimesh), `pip install .[test]` or
> `pip install .[3d,test]` (adds pytest + Playwright).

## What works today

- Draw **rect / circle / line / text** on a grid canvas (Fabric.js).
- Select, move, resize, rotate; edit **stroke color, fill, stroke width**.
- **Python console (Hybrid mode):** type Python that builds the document.
  Helpers: `rect(x,y,w,h,**kw)`, `circle(x,y,r,**kw)`, `line(x1,y1,x2,y2,**kw)`,
  `path(x,y,d,**kw)`, `text(x,y,s,**kw)`, `dimension(x1,y1,x2,y2, offset=…, **kw)`,
  `connect(a, b, fa="center", ta="center")` (anchor-based connectors),
  `row(objs, gap=…)` / `column(objs, gap=…)` / `grid(objs, cols, gap_x=…, gap_y=…)`
  for M4-style template layouts, `add(obj)`, `clear()`, and the live `doc`.
  Example in the console creates a little shelf unit linked by a connector. The
  **Templates** panel runs these helpers for you (shelf, drill guide, box, mat).
- **Export:** SVG (for Cricut/laser), PNG, and PDF (rendered with matplotlib),
  plus **DXF** (CAD/CNC, millimetres) and **G-code** (basic profiling, mm, with
  `feed`/`plunge`/`depth`/`safe` params) for makers.
- **3D solid (optional build123d):** turn the sketch — or just the selected
  shapes — into a solid, as an **extruded** prism, a **revolved** lathe, or a
  **sweep** along a path. A three.js WebGL viewer previews it (drag to orbit,
  scroll to zoom, **Reset view** re-fits the camera) and you can download **STL**
  (always) or **STEP** (needs build123d). Holes in outlines are detected and cut.
  When build123d is absent, meshes are still produced (a prism/revolve
  approximation) and, if **trimesh** is installed, cleaned of degenerate/duplicate
  faces before preview and STL export. The 2D→3D path reuses the same cut-geometry
  extraction as the DXF/G-code exporters. For **Revolve**, pick the spin axis (a
  vertical line in the sketch): enter its X in mm, or click **Pick axis** and click
  the sketch — leave it blank for the auto axis. The chosen axis is drawn in the
  3D viewer as a red line so you can verify it. For **Sweep**, select a closed
  profile plus an open path (a line/path shape); the profile is swept along the
  path into a solid. A **Quality** selector controls the tessellation resolution
  (Low / Medium / High) to trade preview smoothness against speed. Heavy geometry
  work runs in a **process-pool worker** (`core/worker3d.py`), so Open Cascade
  crashes can't take down the server and the event loop never blocks (with a thread
  fallback if multiprocessing is unavailable).
- **Persistence:** work **auto-saves to the browser** (survives reload), named
  **projects** save/load/delete on the server with a recent-files list, and
  **undo/redo** via `Ctrl/Cmd+Z` / `Ctrl/Cmd+Shift+Z`.
- **Tests:** the pure-Python `core/` engine has a pytest suite (`tests/`) covering
  the model, every exporter (SVG/PNG/PDF/DXF/G-code), the console runner, and
  image tracing. Run with `pytest` from the project root. There is also an
  **optional** Playwright UI smoke test (`tests/test_ui_smoke.py`) that loads the
  page in headless Chromium and checks for console/asset errors and required
  elements; it is skipped unless you set `UI_SMOKE_RUN=1` (after
  `pip install playwright && playwright install chromium`).
- **UI:** the frontend is a single vanilla-JS + Fabric.js page. Tailwind is
  pre-compiled to `static/tailwind.css` and Fabric.js is vendored at
  `static/fabric.min.js`, so the whole app — styling **and** canvas — works fully
  offline with no build step or CDN at runtime.
- Units readout (mm / cm / inch) with a px-per-unit scale.
- **Snap-to-grid** (20 px) **and object/page snapping** (edges + centres, hold
  Alt to bypass), align/distribute helpers, arrow-key nudge, **rulers + a scale
  bar** in real units, dashed alignment guides while dragging, and a **real-world
  size readout** for the selection.
- **Lock/unlock** objects so reference art stays put (an "Unlock all" button
  releases everything).
- **Layers panel:** group objects into named layers. New shapes are assigned to
  the active layer; click a layer to select its objects. Toggle a layer's
  visibility (👁) to hide it in the canvas **and** every export (SVG/PNG/PDF/DXF/
  G-code), or lock it (🔒) to keep its objects from being moved. Reorder layers
  (↑/↓) to control stacking order.
- **Connector lines:** select two objects and click **🔗 Connect** (or use
  `connect(a, b, "left", "right")` in the console) to drop an anchor-based line
  that stays attached to each object's edge and follows it when you move things.
- **Template gallery:** pick a **Template** (shelf unit, drill guide, box/crate,
  cutting mat), set its size/count/thickness, and generate an editable starter
  layout from the `row`/`column`/`grid`/`connect` helpers — then tweak it by hand
  or in the console.
- **Dimension lines:** click two points with the **Dimension** tool (or use
  `dimension(x1,y1,x2,y2, offset=…)` in the console) to drop a measured,
  unit-aware annotation that auto-labels in mm/cm/inch and updates with the
  units.
- **Image → paths:** upload a logo/photo, trace it (outline or edge mode,
  threshold/invert controls, optional curve smoothing and `rembg` background
  removal), and commit editable `path` objects to the canvas.

## Architecture

```
static/            Frontend: Fabric.js canvas + properties panel + console
  index.html
  app.js           canonical object <-> Fabric sync, backend API calls
  style.css
core/
  document.py      Canonical document model + shape factories (pure Python)
  export.py        SVG serializer + matplotlib PNG/PDF renderer, plus DXF & G-code
  runner.py        Executes the console's Python against the live document
  trace.py         Image → path objects (Pillow + scikit-image, optional rembg)
  part3d.py        ＋ 2D outline → extruded solid/mesh (build123d-backed, optional;
                   trimesh cleans the no-build123d fallback meshes)
  worker3d.py       ＋ ProcessPoolExecutor worker for heavy 3D ops (thread fallback)
server.py          FastAPI: serves the page, document CRUD, /api/run, /api/trace,
                   exports, and /api/3d/extrude + /api/3d/export
```

The engine (`core/`) is framework-agnostic Python, so it can later be reused
inside a Reflex frontend or a desktop app without rewrites.

## Roadmap (next phases)

1. **Plans & dimensions** — ✅ done (snapping, align/distribute, nudge,
   dimension lines, rulers + scale bar, lock/unlock).
2. **Image → pattern** — contour tracing is in (Pillow + scikit-image), with
   optional `rembg` and curve smoothing; sketchpy (animated flavor) and
   silhouette/fill mode remain.
3. **Maker export** — DXF and G-code for CNC, plus tiled print layouts.
4. **DIY templates** — generators for shelves, gardens, workshop layouts, etc.
5. **Persistence & undo** — save/load projects, multi-step undo.
