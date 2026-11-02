# sketchDiy — Plan

## Vision
A **CorelDRAW-style vector design tool built specifically for DIY makers**: draw
and lay out a physical project in the browser, generate parts with Python, and
export to the formats cutters, lasers, CNC machines, and printers actually use.

## Problem & why it exists
General vector tools (CorelDRAW, Illustrator) are powerful but expensive and
overkill for a shelf or a stencil. Maker-specific tools are narrow. sketchDiy
targets the middle ground: approachable, Python-friendly, and fluent in
**maker output formats** rather than just pretty pictures.

## Target users
Hobbyists, woodworkers, crafters, educators, and anyone who wants to turn an
idea into something they can **cut, engrave, print, or build**.

## Scope (the "all of them" list)
In early brainstorming we committed to *all* of the following. Each is delivered
as an independent module around one shared document, so breadth doesn't become
sprawl:

- **Image → pattern** — trace a photo/logo into line art for plotters, laser
  engraving, Cricut, or coloring pages.
- **Plans & layouts** — design measured projects (furniture, shelves, rooms,
  gardens) with real dimensions.
- **General vector editor** — a flexible editor with a DIY shape/template
  library.
- **Export** — SVG (cutters/laser), PNG/PDF (print), DXF/G-code (CNC).
- **Hybrid interaction** — code generates the design; the GUI tweaks it.

## Key decisions
- **Frontend = web, canvas = Fabric.js** (loaded via CDN, v5.3.1). It gives a
  real object model — selection, drag/resize/rotate — with no JS build step.
- **Backend = FastAPI (Python).** The engine in `core/` is *pure Python and
  framework-agnostic*, so it can later be reused inside a Reflex frontend or a
  desktop app without rewrites.
- **Rendering/export:** matplotlib for PNG/PDF; a small custom serializer for
  SVG (exact, dependency-free); hand-rolled DXF (R2000) and profiling G-code for
  CNC/laser — no `ezdxf` dependency.
- **Python console:** runs user code via `exec` with a restricted builtins set.
  This is fine for a local, single-user tool but is **not a security sandbox**
  (see `agents.md`).
- **Reflex was evaluated and deferred.** We chose to keep the working
  FastAPI + Fabric.js app for now. The engine stays reusable if we revisit
  Reflex later (its backend is Python too).

## Architecture (high level)
```
Browser (Fabric.js canvas + properties + console)
        │  REST/JSON (canonical objects)
FastAPI  ── core/document.py  (model + factories)
          core/export.py     (SVG + matplotlib PNG/PDF)
          core/runner.py     (console executor)
          core/trace.py      (Pillow + scikit-image raster → `path` objects)
```
See `agents.md` for the exact object schema and `milestones.md` for phasing.

## Status
**Phase 0 (Foundation) is complete.** Drawing, selection/transform, properties,
the Python console, and SVG/PNG/PDF export all work.

**M1 is complete:** snapping covers the 20 px grid *and* other objects/page
edges and centres (Alt bypass), with align/distribute helpers, arrow-key nudge,
unit-aware **dimension lines**, a **ruler + alignment-guide overlay** with a
scale bar, **lock/unlock** objects, and **anchor-based connector lines** that
follow their referenced objects. The console also gained M4-style **layout
helpers** (`row` / `column` / `grid`) that reposition objects in place, borrowing
the *idea* of Tesserax's layouts without the dependency. **M2** (image → paths)
supports outline/edges/threshold/invert, optional `rembg`, and **curve smoothing**
(Catmull-Rom → cubic Bézier; open edge traces stay open). Tracing returns preview
objects; the frontend commits them through the normal `/api/objects` path.
sketchpy remains open. **M4** is under way: a **Templates** gallery (shelf, drill
guide, box/crate, cutting mat) generates editable layouts from those helpers.
Parametric template controls are the remaining open step. See `milestones.md`.

## Open questions / risks
- Image-tracing quality: sketchpy (animated flavor) vs. scikit-image contour
  extraction (`find_contours` / `Canny`, with Pillow for I/O + preprocessing)
  for real geometry. OpenCV kept as an optional alternative.
- Background removal for photos: optional `rembg` (U2Net / onnxruntime) as a
  preprocessing step before tracing, gated behind a UI toggle and lazy-installed
  so it stays out of the core dependency set. Verified to import on Python 3.14
  (onnxruntime 1.31.1).
- DXF / G-code correctness for real machines.
- Undo/redo strategy (command stack vs. snapshots).
- Project persistence format (JSON document vs. a DB).

## Related docs
- `design-principles.md` — the rules we build by.
- `agents.md` — how to work on this repo (AI agents & humans).
- `milestones.md` — phased plan with status.
