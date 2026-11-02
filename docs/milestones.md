# Milestones

Phased plan. Each milestone is independently shippable and adds one module
around the shared document. Status legend: ✅ done · 🚧 in progress · ⬜ todo.

---

## M0 — Foundation ✅
The working drawing surface + engine.
- [x] Canonical document model (`core/document.py`).
- [x] FastAPI server: page, document CRUD, settings (`server.py`).
- [x] Fabric.js canvas: draw rect/circle/line/text, select/move/resize/rotate.
- [x] Properties panel: stroke color, fill, stroke width.
- [x] Python console (Hybrid) executing against the live doc (`core/runner.py`).
- [x] Export SVG (custom serializer) + PNG/PDF (matplotlib) (`core/export.py`).
- [x] Units readout (mm/cm/inch) + px-per-unit scale.
- [x] Starter scene + docs (README, plan, design-principles, agents, milestones).

**Acceptance:** Open the app, draw and edit shapes, run the console example,
download SVG/PNG/PDF that match the canvas.

---

## M1 — Plans & dimensions ✅
Make it a real layout tool for builders.
- [x] Snap-to-grid (20 px; hold **Alt** to drag freely).
- [x] Real-world size readout for the selection (mm/cm/inch, via
      `px_per_unit`).
- [x] Object-to-object snapping: grid plus other objects' and the page's
      edges/centres, with **Alt** to bypass.
- [x] Align (left / centre-H / right / top / centre-V / bottom) and distribute
      (horizontal / vertical) helpers (`#alignSelect`).
- [x] Arrow-key nudge (grid step; **Shift** = 5×, **Alt** = 1 px).
- [x] Dimension lines: measure any two points with a labelled, unit-aware
      dimension object — click two points with the **Dimension** tool, or use
      `dimension(x1,y1,x2,y2, offset=…)` in the console. The label follows the
      current units (mm/cm/inch) and updates with them.
- [x] Ruler/guidelines overlay: ticked top + left rulers in real units, dashed
      alignment guides shown while dragging, and a corner scale bar.
- [x] Page/artboard size shown in real units (`#scaleReadout`) plus the scale bar.
- [x] Lock/unlock objects (`meta.locked`; "Unlock all" releases everything).
- [x] **Connector lines:** anchor-based links between two objects (`center` /
      `left` / `right` / `top` / `bottom`) that follow the objects when they move;
      add via the **🔗 Connect** button (auto-picks the nearest edges) or
      `connect(a, b, fa, ta)` in the console. Missing targets cascade-delete.
- [x] **M4-style layout helpers:** console `row()` / `column()` / `grid()`
      reposition object lists in place — borrowing Tesserax's layout *idea* without
      the dependency — on top of the existing canonical schema.

**Acceptance (met):** A user can snap parts to the grid and to each other, see
the selected object's real size, align/distribute a selection, drop unit-aware
dimension lines, connect objects with anchor-based connector lines that follow
them, arrange groups with `row`/`column`/`grid` helpers, read rulers/scale bar,
and lock objects. Rulers/guides are visual only (not exported); everything else
round-trips through the document.

---

## M2 — Image → pattern 🚧
Turn photos/logos into line art for cutting/engraving/coloring.
- [x] Image import (upload) endpoint — `POST /api/trace`.
- [x] Contour extraction (Pillow + scikit-image: `find_contours` / `Canny`) → `path` objects (real geometry).
- [x] Threshold / edges / invert controls; tracing **returns preview objects
      without mutating the document**.
- [x] "Trace to paths" action that commits the preview objects to the doc.
- [x] (optional) Background removal via `rembg` before tracing, behind a UI
      toggle; lazy-imported, not a core dependency (model download untested here).
- [x] Curve smoothing: Catmull-Rom → cubic Bézier (`smooth` option). Outlines
      stay closed (`Z`), edge traces stay open.
- [ ] sketchpy integration for the animated drawing flavor.
- [ ] Per-contour fill/silhouette mode.

**Acceptance (met):** Upload a logo, get editable line or smooth curve paths on
the canvas, export as SVG suitable for a laser/Cricut.

---

## M3 — Maker export ✅
Beyond SVG/PNG/PDF.
- [x] DXF exporter (AutoCAD R2000 `LWPOLYLINE`, millimetres, `$INSUNITS`,
       y-up) — cuttable outlines for rect/circle/path/line/connector.
- [x] G-code generator (basic **profiling** — rapid to each outline, plunge, cut
       the perimeter) with `feed` / `plunge` / `depth` / `safe` query params.
       Output is mm, absolute (`G21`/`G90`), ending in `M2`.
- [ ] Tiled/posed PDF for large print (e.g. poster, pattern pieces).
- [ ] Export preview with scale confirmation.
- [ ] Pocketing / fill (interior material removal) for G-code.

**Acceptance (met):** A drawn part exports to a valid DXF (`SECTION`/`ENTITIES`/
`LWPOLYLINE`/`EOF`, mm, connectors resolved to lines) and a runnable G-code file
(`G0`/`G1`/`G21`/`M2`, mm) — verified against the live API including a
connector-linked template scene.

---

## M4 — DIY templates 🚧
Generators that produce whole projects from parameters.
- [x] Template registry + UI picker (a **Templates** card with one-click buttons).
- [x] Console helpers `row()` / `column()` / `grid()` / `connect()` that the
       generators build on.
- [x] Generators: **shelf unit**, **drill guide** (hole grid), **box/crate**
       (stacked panels linked by connectors), **cutting mat** (grid of cells).
       Each is a Python script using the console helpers.
- [x] Parametric controls: a template picker + per-template inputs (size, count,
       thickness/gap) that feed the generators, so each layout is editable before
       it's drawn.

**Acceptance (partial):** Pick a template from the gallery, set its dimensions, and
get an editable, exportable plan built from the layout/connector helpers. More
generators (garden bed, pegboard, joinery) are still to come.

---

## M5 — Persistence & polish 🚧
Make work durable and the UX solid.
- [x] **Auto-save:** the canvas is serialized through the canonical schema into
       `localStorage` (debounced), so a page reload restores your work even if
       the server's in-memory doc was lost to a restart. On boot the app prefers
       local storage and re-syncs the server via `POST /api/doc`.
- [x] **Named projects:** save/load/delete project files on the server
       (`./projects/<name>.json`) with a recent-files list in the UI.
- [x] **Undo/redo:** a linear document-snapshot history — `Ctrl/Cmd+Z` undoes,
       `Ctrl/Cmd+Shift+Z` (or `Ctrl+Y`) redoes. One step per gesture (drag,
       nudge, add, delete, clear, console run, template, trace, align/distribute,
       style edit, units change).
- [x] **Layers panel:** named layers with add/delete/rename, per-layer
      visibility (hidden layers are skipped by every exporter) and lock, an
      active layer that new shapes are assigned to, layer selection, and
      reorder-to-restack.
- [ ] Keyboard shortcuts & accessibility pass.
- [x] Automated tests for `core/` — `tests/` (pytest): document model, SVG/PNG/
       PDF/DXF/G-code exporters (incl. mm scaling + y-up + connector resolution),
       the console runner (helpers, connect, errors), and image tracing.

**Acceptance (partial):** Reload the page and your work is still there (localStorage
auto-save + server re-sync); Ctrl+Z undoes and Ctrl+Shift+Z redoes; named projects
save/load/delete with a recent-files list. All server endpoints verified via the
live API; the in-browser paths are covered by the optional Playwright UI smoke
test (`tests/test_ui_smoke.py`, run with `UI_SMOKE_RUN=1`) — which now actually
*drives* the UI (type console code → Run → assert the server doc and SVG export
reflect the shapes) because Fabric is vendored locally and real-time headless
Chrome works (the old `--virtual-time-budget` harness stalled on the Fabric CDN).

---

## Possible later
- Mobile/touch canvas support.
- Cloud sync / sharing (optional, local-first by principle #7).
- Reflex frontend port (engine already reusable — see `plan.md`).
- Parametric constraints solver.
