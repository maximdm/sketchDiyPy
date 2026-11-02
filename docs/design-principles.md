# Design Principles

The rules sketchDiy is built by. When a choice is ambiguous, these decide.

## 1. DIY-first, not design-first
Features earn their place by helping someone **make a physical thing**. A tool
that draws pretty vectors but can't output a cuttable file is a miss. Prefer
real-world utility (measurements, maker exports) over generic design dazzle.

## 2. Hybrid by default
The product assumes two modes of creation that cooperate: **write Python to
generate**, then **click to tweak**. The document is the shared language between
the console and the canvas — never bifurcate them.

## 3. One document, many modules
Every capability (image tracing, templates, dimension tools, exporters) reads
and writes the *same* canonical document. No feature owns its own private data
model. This is what keeps "all of the above" from becoming unmaintainable.

## 4. Keep the engine pure Python
Everything in `core/` must be importable with no web framework, no GUI, and no
I/O side effects beyond rendering to bytes. This is what makes the engine
portable (Reflex, CLI, desktop, serverless) and testable.

## 5. Canvas interactions stay client-side
Dragging, resizing, and rotating must be smooth, which means they happen in the
browser (Fabric.js), not via server round-trips. The backend is told only about
*committed* changes (`object:modified`, `selection:changed`), not every
mousemove.

## 6. Speak maker formats
SVG, DXF, G-code, and print-ready PDF/PNG are first-class. A feature that
produces only an on-screen image is incomplete.

## 7. Local-first and private
The tool runs on the user's machine and their files stay theirs. Cloud is
optional, never required. The Python console runs locally and is trusted-local
by design (documented, not hidden).

## 8. Approachable, but not a toy
Honor the friendly spirit of `turtle`/`sketchpy` (low floor) while still being
a real editor (high ceiling). Simple things should be one click or one line;
advanced things should be *possible*.

## 9. One source of truth for geometry
The canonical object schema (see `agents.md`) is the contract between frontend
and backend. Both sides convert *to* and *from* it; neither side's internal
representation leaks across the boundary.

## 10. Progressive disclosure
Beginners see a clean canvas and a friendly console. Power features (units,
dimension lines, G-code params) appear as you need them, not all at once.

---

### What these mean in code
- New shapes/exporters go in `core/`, pure Python, and are surfaced through the
  same API + schema — not bolted onto the frontend directly.
- Frontend edit logic lives in the `static/js/sync.js` converters
  (`toCanonical` / `buildFabric`); never hand-roll object JSON in two places.
- Exporters return **bytes** (PNG/PDF) or a **string** (SVG); the server just
  streams them.
