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
