# Code review & recommendations — sketchDiy

A written-up opinion on the current state of the project, plus prioritized
recommendations. Captured during a review pass; the first item (console
transactionality) is already implemented below.

This is **not** a plan doc — it's the "what I'd fix and why" companion to
`milestones.md` / `plan.md`. Take what's useful.

---

## 1. What the project is

A local-first, maker-focused vector design tool: draw in the browser (Fabric.js),
generate with an embedded Python console, and export to **SVG / PNG / PDF / DXF /
G-code**. Ambitious for a personal project, and most of M0–M5 is actually built
and tested.

## 2. What's genuinely strong

- **Clean separation of concerns.** `core/` is pure Python with zero web/GUI
  imports and a real test suite. That discipline is rare in hobby projects and
  makes the engine portable (Reflex, CLI, desktop, serverless).
- **The canonical-object schema as a contract** (one dict shape per object,
  agreed by frontend and backend, documented in `agents.md`) is the right call
  and is what keeps a drawing tool from collapsing into spaghetti.
- **Offline-first is executed, not just claimed** — Tailwind pre-compiled, Fabric
  vendored, no runtime CDN. The optional Playwright smoke test that *drives* the
  real UI is a nice touch.
- **Maker output is first-class** (DXF + G-code). Directly serves design
  principle #1 ("make a physical thing").
- **Tests pass and cover the risky parts** — exporters (mm scaling, y-up flip,
  connector→polyline), the runner (blocked builtins, error surfacing), tracing.

## 3. Weaknesses / risks

1. **Console was non-transactional (FIXED).** `core/runner.py` executed the
   user's script directly against the live in-memory `doc`. A script that errored
   part-way through (after adding some shapes) left the document half-mutated.
   Now fixed: the doc is deep-copied before `exec` and restored on any error
   (see §5 and `docs/agents.md` gotchas).
2. **`/api/run` is unsandboxed by design** (documented; acceptable for
   local-first). Two consequences to keep in mind: it's a single module-level
   `doc` global, so two browser tabs clobber each other; and "expose this to a
   friend" would be dangerous. Fine today, a landmine later. If the goal ever
   becomes sharing, the console needs a real sandbox (AST allow-list).
3. **frontend was a ~65 KB / 400+ function monolith** (`static/app.js`) — snapping,
   connectors, dimensions, themes, templates, exports all intermixed. *(Now
   split into ES modules under `static/js/` — see recommendation #2; the testing
   gap below still stands.)* There are **no automated tests** for this code
   except the optional e2e smoke (skipped by default), so the riskiest,
   most-edited logic is the least covered.
4. **Small code smells:**
   - `core/document.py:61` — `kw.setdefault("fontSize", kw.pop("fontSize", 24))`
     is a literal no-op (pop then setdefault to the same value).
   - `Document.upsert` does a shallow `existing.update(obj)`, so a `PUT` with a
     partial `style` dict silently drops the other style keys.
5. **Maker-export depth is thin where it matters.** G-code is "basic profiling"
   only — no pocketing/fill, no lead-in, no toolpath ordering. Tiled print PDF
   and an export scale-confirmation preview are still ⬜. Given principle #1,
   these are higher-value than more templates.
6. **Accessibility is unaddressed** (still listed as remaining in M5). The UI is
   mouse/canvas-centric — no keyboard paths for tools, weak labeling.
7. **`layers` feature is ahead of its docs** (the panel exists; docs lag). The
   `Document` model already has a `layers` attribute (a default `"layer-base"`)
   and `load_dict` backfills `meta["layer"]` on every object. The frontend has a
   full Layers panel (`renderLayers`, `addLayer`, hide/lock, reorder), but neither
   `README.md` nor `milestones.md` mentions it — `milestones.md` even still lists
   "Layers panel" as a remaining M5 item — and the canonical-schema section of
   `agents.md` doesn't document the `layers` field. This is a finished feature
   that simply hasn't been written up; the docs should catch up.

## 4. Recommendations (priority order)

1. **Make the console transactional — DONE.** Snapshot then rollback on error.
2. **Split `app.js` into ES modules — DONE.** `static/app.js` is now split into
   `static/js/{state,sync,layers,page,tools,console,trace,persistence}.js`
   loaded as native ES modules (`<script type="module">`). No build step, no
   CDN — the offline/no-bundler constraint is preserved. Shared mutable state
   (the `doc`, snap flags, undo pointer, …) lives on a `state` object in
   `state.js`; everything else imports from it. See `docs/agents.md`.
3. **Invest in export robustness over more templates.** Tiled/large-format PDF,
   G-code pocketing, and an export preview with a scale confirmation directly
   serve "make a physical thing." Templates are fun but lower payoff.
4. **Add focused frontend tests** for `toCanonical` / `buildFabric` (extract them
   as pure functions and test in Node, or make the Playwright smoke a required CI
   gate). These converters are the contract; they deserve coverage.
5. **Clean the small smells** (the `text()` fontSize line, `upsert` style merge)
   and consider lightweight pydantic models for `/api/objects` /
   `/api/settings` since FastAPI is already a dependency — cheap validation
   safety for the open-ended object schema.
6. **Resolve the `layers` inconsistency** (§3.7): either finish it (panel + docs)
   or pull the half-feature back out so the schema/docs stay honest.
7. **Do an accessibility pass** (tool buttons reachable by keyboard, ARIA labels,
   focus management) — the difference between "not a toy" (principle #8) and
   actually usable by everyone.

## 5. Open question that changes the priorities

Is the end goal **personal tooling for yourself**, or **a product you'll
share/distribute**? If the latter, items #2 (sandbox + frontend structure) jump
way up and the local-only assumptions need re-examining. If the former, the
pragmatic shortcuts are fine and the focus should stay on the maker-export
features that make *your* projects.

---

### Changelog of this review's fixes
- **Console transactionality** (`core/runner.py`): deep-copy snapshot before
  `exec`, restore via `load_dict` on any exception. Added
  `tests/test_runner.py::test_run_rolls_back_on_error` and
  `::test_run_rolls_back_settings_on_error`. All 39 core tests pass.
- **Frontend split into ES modules** (`static/app.js` → `static/js/*`): the
  monolith is now `state` + `sync` + `layers` + `page` + `tools` + `console` +
  `trace` + `persistence`, loaded as native ES modules with no build step.
  `index.html`, `docs/agents.md`, and this review were updated to match.

---

## 6. Second review pass — 2026-11-04

A fresh assessment after the **3D** (extrude / revolve / sweep + three.js viewer),
**image tracing**, **layers**, **connectors**, **dimensions**, and **plan** features
landed. Verified live: `pytest` → **84 passed, 13 skipped** (the 3D-dependent and
Playwright-ui cases) across Python 3.11 / 3.12 / 3.13 in CI; the full `core/`
engine and the ES-module frontend (~4 kLOC across `static/js/*.js`) are present
and the server boots.

The first pass's two DONE items (§4.1 console transactionality, §4.2 ES-module
split) still hold. Note: **§3.7 (Layers ahead of docs) is now resolved** —
`docs/agents.md` documents `layers` fully, and the Layers panel is real. What
follows is the **updated, consolidated recommendation list**. Items new since the
first pass are tagged **[NEW]**; items that refine an earlier one point back to it.

### 6.1 Strengths reaffirmed (still true, now more so)

- **3D crash/resource isolation is genuinely good engineering.** `core/worker3d.py`
  runs `build123d` / Open Cascade in a *spawn* `ProcessPoolExecutor` with a
  thread fallback, a configurable per-job timeout (`SKETCHDIY_3D_TIMEOUT`),
  `BrokenProcessPool`/`PicklingError` retry-on-thread, and `atexit` teardown.
  A kernel crash or OOM cannot take down the server, and the event loop never
  blocks. This is the part most projects get wrong.
- **Optional-dependency discipline is excellent.** `build123d`, `trimesh`,
  `rembg` are all lazily imported; the 2D app starts without them, and the 3D
  fallback (`core/part3d.py` `_prism_mesh` + ASCII STL writer) keeps preview/STL
  working dependency-free. The same lazy pattern is used for `scikit-image`.
- **Single source of truth for cut geometry.** `core/export.py`'s
  `_cut_polylines` is reused by DXF, G-code, **and** `core/part3d.py`, so the
  2D→3D path can't drift from the 2D exporters.
- **Offline-first frontend** (vendored Tailwind + Fabric, native ES modules, no
  build step) is preserved end-to-end.

### 6.2 High impact

**H1. The `/api/run` "sandbox" can be escaped — and there's a clean fix.
(Refines §3.2.)** `core/runner.py` passes a restricted `safe_builtins` map to
`exec`, but that is *not* a security boundary: a determined caller escapes it
trivially (e.g. `().__class__.__bases__[0].__subclasses__()` → reach `os` /
file I/O / `subprocess`). The module already admits "NOT a sandbox for untrusted
input." Two safe paths:
- **Own it:** keep the app strictly `127.0.0.1` and document that it must never
  be exposed. Fine for a personal tool.
- **Recommended: run the console in the browser via Pyodide.** `core/document.py`
  is pure Python and could be shared client-side; the `runner` helpers
  (`rect/circle/line/text/dimension/connect/row/column/grid`) are plain functions.
  This removes the server-side RCE entirely, eliminates the HTTP round-trip, and
  *strengthens* the "reusable engine" story — no sandbox escape is even possible
  because there's no privileged process. Worth prototyping.

**H2. `doc` is a process-global singleton (refines §3.2).** `server.py:66`
holds one in-memory `Document` shared by every connection; the frontend mirrors
it with a module-level singleton in `static/js/state.js`. Consequences: no
multi-user, no multiple open documents in memory, and concurrent browser tabs can
race on `upsert`/`remove` (sync endpoints run in FastAPI's threadpool). For a
local single-user tool this is acceptable — **document the limitation**. If you
ever want sessions, move to a session-keyed store (e.g. a `dict[str, Document]`
keyed by a cookie/header, or a small SQLite/DB). The global also means a server
restart loses the in-memory doc (only `localStorage` + `./projects/*.json`
survive).

**H3. [NEW] G-code ignores tool diameter — a real correctness gap for makers.**
`core/export.py:743` `to_gcode` cuts the outline **centerline**, so a real end
mill removes material on both sides of the line and the part comes out
undersized by one tool radius. There's also no multi-depth pass for cuts deeper
than one pass. Concrete fixes:
- **Tool-radius compensation:** offset each closed outline outward by `r`
  (and inward for holes) before emitting the path. Add a `tool` / `diameter`
  query param (default e.g. 3.175 mm).
- **Multi-pass depth:** step `depth` over several `G1 Z` passes instead of one
  plunge when `|depth|` exceeds a per-pass limit.
These directly serve design principle #1 ("make a physical thing") and are
higher-value than more templates.

### 6.3 Medium impact

**M1. [NEW] DXF is hand-rolled and uses `repr()` for floats.**
`core/export.py:705-706` emits `repr(X)` / `repr(Y)`, which can produce
scientific notation (`1e-05`) that some legacy CAD/CAM readers reject, and the
writer omits entity handles / full group-code coverage. It works for many
viewers but is fragile. Consider **`ezdxf`** (optionally, behind the same lazy
import pattern used elsewhere) for robust R2000/2013+ output, especially once
real CAM workflows depend on it. Keep the pure-Python path as the no-dependency
fallback.

**M2. [NEW] Frontend has ~no unit testing.** The first pass flagged this (§3.3,
§4.4) and it still stands: the ~4 kLOC JS frontend is covered only by the
optional Playwright e2e smoke (`tests/test_ui_smoke.py`, **skipped by default**,
and a 1 100-line file). Add a few **Vitest** unit tests for the *pure* logic —
state transforms, the `toCanonical`/`buildFabric` converters, layer
reorder/visibility resolution. You don't need to test Fabric itself; extract and
exercise the logic functions. This is the highest-leverage test investment
because the converters are the contract (§4.4).

**M3. [NEW] Mixed coordinate conventions are a latent bug source.** `rect` /
`circle` / `text` use a **center-based** `x,y` plus `geometry`; `line` / `path` /
`dimension` store **absolute** endpoints and ignore `x,y` (identity transform).
It works only because every exporter special-cases each type, and the schema doc
already has to explain the split. As the model grows this will drift. Consider
unifying on one convention (absolute points everywhere) the next time you touch
`document.py` / the converters.

### 6.4 Low impact / polish

**L1. [NEW] `requirements.txt` ships `pytest` at the base**, contradicting the
"lightweight, no test deps" story and the `pyproject.toml` split (where `pytest`
lives in `[test]`). Move `pytest` (and `httpx`, if present) into
`requirements-dev.txt` for consistency with the extras model.

**L2. [NEW] No `LICENSE` file.** The repo has a CI badge and a public GitHub
remote but no license — legally that blocks reuse. Add one (MIT is the usual fit
for a personal tool).

**L3. [NEW] The Plan workspace is undocumented.** `static/js/plan.js` (575
lines) and the document's `plan` field (`{ currency, buy, steps, parts, notes }`)
are substantial, but `README.md` never mentions the Plan workspace (shopping
list + assembly steps). Either document it as a feature or mark it clearly as
WIP so the README matches reality (the same honesty gap the first pass flagged
for Layers in §3.7).

**L4. [NEW] Inconsistent typing / no `mypy`.** `core/` uses
`from __future__ import annotations` and partial hints but isn't checked. Adding
`# mypy --strict` gradually would pay off as the engine grows — optional, given
the test coverage.

### 6.5 Suggested next step

Highest-value move overall: **the Pyodide-in-browser console (H1)** — it removes
the only real security hole, drops a server responsibility, and strengthens the
portable-engine story, all with no UX change. The **G-code tool-offset fix (H3)**
is the highest-value *maker-facing* correctness improvement.

---

### Changelog of this review's notes
- **Second review pass added (2026-11-04).** Consolidated prior recommendations
  and added H3 (G-code tool offset), M1 (DXF `repr`/ezdxf), M2 (Vitest frontend
  tests), M3 (coordinate-convention unification), L1–L4 (pytest-in-base,
  LICENSE, Plan docs, typing). Confirmed §3.7 (Layers docs) is now resolved via
  `docs/agents.md`. Assessment was verified against a live `pytest` run
  (84 passed / 13 skipped) and a full read of `core/`, `server.py`, and the
  `static/js/` frontend.

- **Started implementing the recommendations (2026-11-04).** Done this pass:
  - **L2** — added `LICENSE` (MIT).
  - **L1** — moved `pytest` (+ `httpx`) out of the base `requirements.txt` into
    `requirements-dev.txt`; updated the README install comment to match.
  - **L3** — documented the **Plan workspace** in `README.md` (shopping list,
    assembly steps, parts cut list, notes) and corrected the stale `app.js`
    architecture note.
  - **H3** — `core/export.py` `to_gcode` now supports `tool` (hole-aware
    end-mill-diameter offset) and `pass_depth` (multi-pass roughing); wired both
    into `server.py`'s `/api/export/gcode` and added tests
    (`test_to_gcode_tool_offset_moves_outline_outward`,
    `test_to_gcode_multipass_steps_depth`).
  - **M1** — added an optional `ezdxf`-backed DXF writer (`_to_dxf_ezdxf`) that
    lazily takes over when `ezdxf` is installed and falls back to the
    hand-rolled writer otherwise; added a fallback regression test
    (`test_to_dxf_fallback_when_ezdxf_absent`).
  - **M2** — added a Vitest harness (`package.json`, `vitest.config.js`), a pure
    `static/js/units.js` helper, and `tests/js/units.test.js` — the first
    frontend unit test. The `sync.js` converters still need extraction to be
    covered the same way.
  - Updated `docs/agents.md` API table for the new G-code params.
  - **G-code UI control** — added end-mill-diameter and roughing-pass-depth
    inputs to the export toolbar (`static/index.html` + `static/js/page.js`); they
    append `tool` / `pass_depth` to the `/api/export/gcode` link so the new
    engine options are reachable without code.
  - **H2 (foundation + activation)** — added `core/store.py` (`DocStore`) and
    rooted the live document in it; then wired it into `server.py` via a
    `current_doc` dependency that reads an optional `X-Session-Id` header. Every
    document-touching endpoint now resolves an isolated document per session
    (default when no header, preserving prior behaviour), the 3D test swap-blocks
    moved to `store.set_default(...)`, and a `test_sessions_are_isolated` check
    was added.
  - **H1 (AST sandbox) + H2 (session store) completed this pass (2026-11-04).**
    See `docs/agents.md` (sandbox gotcha + session-store note). Suite:
    **91 passed / 13 skipped**.
  - **M3 (coordinate-convention unification) implemented — Option B (2026-11-04).**
    Every object now carries a uniform `x,y` centroid origin (`core/document.py`:
    `line`/`dimension`/`path` factories + `_centroid`/`_normalise_origin`; the
    latter migrates legacy `./projects/*.json` objects with `x,y == 0`
    automatically via `Document.load_dict`). `static/js/sync.js::toCanonical`
    emits the centroid for those types too; exporters were unchanged (they
    already use `geometry` for line/dim/path and `o["x"],o["y"]` as centre for
    rect/circle/text). A pure `core/pathgeom.py` (`path_vertices`/`path_center`)
    was extracted so the path bbox/centroid math is shared and matplotlib-free.
    `tests/test_m3_baseline.py` grew from 10 → 13 tests (added centroid +
    legacy-normalisation + `_bbox`-matches-origin checks); `test_document.py`'s
    line-schema assertion was updated to the new centroid. Full suite:
    **104 passed / 13 skipped**.
  - Prior `pytest` re-run after the H2 foundation was **87 passed / 13 skipped**;
    the 3 H1 sandbox tests + 1 H2 isolation test brought it to 91, then the 10 M3
    baseline tests brought it to 101, then the 3 M3 implementation tests brought
    it to **104 passed / 13 skipped** (all additive; `tool=0` / `use_ezdxf`
    defaults preserve prior behavior).
