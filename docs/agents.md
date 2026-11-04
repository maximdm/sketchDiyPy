# agents.md — How to work on sketchDiy

This file is for **AI coding agents and human contributors**. It tells you how
to run, understand, and extend the project without re-deriving decisions. If
you change the architecture, update this file.

- **Plan & rationale:** `plan.md`
- **Rules we build by:** `design-principles.md`
- **Phasing & status:** `milestones.md`

---

## Quickstart
```powershell
cd sketchDiy
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python -m uvicorn server:app --reload
# open http://127.0.0.1:8000
```
Python 3.11+ (developed on 3.14). A `venv/` is already present in the repo.

## Repository layout
```
server.py            FastAPI app: serves page, document CRUD, /api/run, exports
core/
  document.py        Canonical document model + shape factories (PURE Python);
                     Document.load_dict() restores a saved project
  export.py          SVG serializer + matplotlib PNG/PDF renderer, plus DXF & G-code maker export
  runner.py          Executes the console's Python against the live document
  trace.py           Image → `path` objects (Pillow + scikit-image; optional rembg)
static/
  index.html         Page shell (toolbar, canvas, properties, console, exports).
                      Links the pre-compiled Tailwind stylesheet (no CDN, no build).
                      Loads the frontend as ES modules (see static/js/).
  js/                Frontend split into ES modules — **no build step, no bundler**.
                      `app.js` is the entry point (imports the rest); `state.js`
                      creates the Fabric canvas and holds the shared mutable state;
                      `sync.js` (canonical<->Fabric + API), `layers.js`, `page.js`
                      (themes/page/readouts/shape tools), `tools.js` (dimension &
                      connector tools, snapping, align, delete, rulers), `console.js`
                      (Python console + templates), `trace.js` (image->paths), and
                      `persistence.js` (undo/redo + named projects + boot). Edit
                      logic stays in the two converters (`buildFabric`/`toCanonical`
                      in `sync.js`), not scattered handlers.
  fabric.min.js      VENDORED Fabric.js 5.3.1 (MIT). Do not hand-edit; bump by
                      re-downloading the matching version from a CDN.
  tailwind.css       COMPILED output — do not hand-edit; regenerate (see below).
docs/                This folder — planning & contributor docs
```

**Frontend module rule:** the modules under `static/js/` are loaded as native ES
modules (the entry `app.js` is `<script type="module">`). They share state through
`static/js/state.js` (the `state` object plus the `canvas`, `$`, `genId`,
`palette`, `THEMES` exports). Because ES module imports are read-only, the values
that get *reassigned* at runtime (the current `doc`, snap flags, undo pointer,
…) live on the `state` object rather than as top-level `let`s — keep it that way.
There is still no build step or CDN at runtime; Fabric.js is vendored globally.
**Rule:** `core/` must not import FastAPI, Fabric, or anything web/GUI. Keep it
pure Python so the engine stays portable.

**Frontend styling constraint:** the UI is styled with **Tailwind, compiled once
into `static/tailwind.css`**. There is **no build step or CDN at runtime** — the
page links `/static/tailwind.css` directly and styles itself fully offline. To
change styling:

1. Edit the markup/classes in `index.html` (and the JS-generated classes in
   `static/js/app.js`), or the component rules / custom palette.
2. Recompile with the Tailwind v3 CLI and commit the regenerated `static/tailwind.css`:

```powershell
# one-time, in a scratch dir (NOT checked into the repo):
npm init -y; npm i -D tailwindcss@3
# author input.css (with @layer components + plain CSS for #c/#code) and
# tailwind.config.js (custom brand/ink/muted/line colors; content: index.html, static/js/app.js)
npx tailwindcss -c tailwind.config.js -i input.css -o static/tailwind.css --minify
```

Do **not** reintroduce a runtime CDN or a bundler. Fabric.js is vendored locally
at `static/fabric.min.js` (MIT; pinned to 5.3.1), so the whole app — styling **and**
canvas — runs with no network access at all.

Gotcha when editing `input.css`: author component classes as **plain CSS** with
explicit hex values, not `@apply hover:border-brand` — `@apply` with a `hover:`/
`focus:` variant of a *custom-color* utility fails the build in this setup.
Verify visual changes in a real browser — the sandbox's headless Chrome stalls on
the Fabric CDN under `--virtual-time`.

## The canonical object schema (the contract)
Every shape in the document is one dict. Both the backend (`buildFabric` in
`static/js/sync.js` and the exporters in `export.py`) and the frontend agree on this:

> **Coordinate conventions are now unified (M3, Option B).** Every object carries a
> meaningful `x,y` centroid origin (centre for rect/circle/text; midpoint /
> bbox-centre of the absolute geometry for line/dimension/path), so rotation
> pivots, selection, anchors and bbox are computed uniformly. `line`/`dimension`/
> `path` still store *absolute* points in `geometry` (authoritative for position);
> `x,y` is just the origin. `docs/m3-migration.md` records the migration; legacy
> `./projects/*.json` (which stored `x,y == 0` for those types) are migrated
> automatically by `Document.load_dict`.

| Field        | Type   | Meaning |
|--------------|--------|---------|
| `id`         | str    | Stable uuid; **client-generated** when drawn. |
| `type`       | enum   | `rect` \| `circle` \| `line` \| `path` \| `text` \| `dimension` \| `connector` |
| `x`, `y`     | float  | Object origin in canvas px — a **uniform centroid** for every type (M3): the centre for rect/circle/text, and the midpoint / bbox-centre of the absolute geometry for line/dimension/path. It is the rotation pivot and selection origin; it is *not* authoritative for a line/path/dimension's position (that lives in `geometry`). |
| `rotation`   | float  | Degrees. |
| `scaleX`, `scaleY` | float | Scale factors. |
| `geometry`   | obj    | Type-specific (see below). |
| `style`      | obj    | `{ fill, stroke, strokeWidth, fontSize }` |
| `meta`       | obj    | Free-form: `{ label, rw, rh, unit }` etc. |

`geometry` by type:
- `rect` → `{ w, h }`
- `circle` → `{ r }`
- `line` → `{ x1, y1, x2, y2 }` (absolute; `x,y` is the centroid origin; ignores rotation/scale)
- `path` → `{ d }` (SVG path string, absolute; `x,y` is the bbox-centre origin)
- `text` → `{ text, fontSize }`
- `dimension` → `{ x1, y1, x2, y2, offset }` (measured span + perpendicular
  offset of the dimension line; label is generated from the document units at
  render/export time — do **not** bake a length into the schema; `x,y` is the
  measured-segment midpoint)
- `connector` → `{ from: {id, anchor}, to: {id, anchor} }` (semantic link between
  two objects; `anchor` ∈ `center`/`left`/`right`/`top`/`bottom`). The line is
  **derived** from the referenced objects' bounding geometry, so it follows them
  when they move; the canonical record stores only the refs, never endpoints.

**Dimension objects:** stored with identity transform (`x,y` = 0, rotation/scale
= 1); the frontend renders a Fabric `Group` (extension lines + dimension line +
45° ticks + rotated label) and `toCanonical` maps the group's live transform to
updated endpoints. Exporters lay out the same primitives via `core/export.py`
(`_dim_geometry` / `_dim_label`). The `meta.label` field, if set, overrides the
auto-generated measurement.

**Connector objects:** stored with identity transform. Both the frontend and the
exporters resolve each anchor to a canvas point via a shared rule — the object's
centre (stroke-excluded) plus a half-extent offset rotated by the object's angle
(`anchor_point` in `core/export.py`, `anchorPoint` in `static/js/sync.js`). Endpoints are
**not** serialized; recreating the document re-derives them. A connector that
references a missing object renders nothing (and is cascade-deleted if its target
is removed).

**Frontend sync model:** `toCanonical(obj)` converts a Fabric object to this
dict and `PUT`s/`POST`s it; `buildFabric(o)` rebuilds a Fabric object from a
dict. When the console runs, the server returns the whole doc and the frontend
rebuilds the canvas. Never serialize Fabric internals directly — go through
these two functions.

**Locked objects:** `meta.locked` (set via the Properties panel or console) makes
a Fabric object `selectable:false, evented:false` on rebuild, so it stays in the
document and exports but can't be selected/moved. "Unlock all" clears the flag on
every object.

**Layers:** the document owns an ordered `layers` list
(`[{ id, name, visible, locked }]`); each object points at one via `meta.layer`.
Higher layers paint above lower ones, and a hidden layer hides its members in the
canvas **and** every exporter (SVG/PNG/PDF/DXF/G-code skip non-visible layers via
`core/export.py`'s `_layer_visible`); a locked layer makes its members
non-selectable. The default document has one layer (`layer-base`). Objects with no
`meta.layer` (or a stale id) are treated as visible (on the default layer) by the
exporters — `load_dict` is a faithful restore and does not mutate object `meta`.
The frontend's `ensureLayers()` assigns every object to a real layer for display
and editing, assigns new shapes to the active layer, and pushes layer edits
through `PUT /api/settings { layers }`; reordering re-sorts `objects` by layer and
resyncs via `POST /api/doc`.

**Ruler/guideline overlay** is frontend-only: ticked top/left rulers, drag
guides, and the scale bar are painted in `canvas.after:render` and are *not*
part of the document or the exports.

**Page settings** (the "Page" sidebar card) drive `doc.background`, `grid_color`,
`grid_size`, `export_grid`, `show_grid`, `show_rulers`, and `theme` via `PUT /api/settings`. The
board background + grid are painted onto the (transparent) Fabric canvas element
as CSS `background-color` / `background-image` so the grid sits behind the artwork
(`grid_size` sets the CSS `background-size`; clamped to 4–200 px). The grid is
screen-only by default; a **"Include grid in export"** checkbox (`doc.export_grid`)
bakes the grid into the SVG / PNG / PDF exporters (using the page's `grid_color` and
`grid_size`) so the exported file matches the on-screen artboard. For **DXF** the grid
is written as `LWPOLYLINE`s on a dedicated **`GRID`** layer (mm, y-up) so it can be
kept separate from cut geometry in CAM software. For **G-code** the grid is appended as
a non-cutting rapid (`G0`) reference pass at the safe Z height — the tool never lowers
into the material for it, so the grid is never cut but is still useful for positioning.
The card also
has a **"Reset page to theme"** button that re-applies the current theme's default
board/grid/palette after manual tweaks, and the shape-tool buttons show a small
**colour swatch** reflecting the current theme palette so you can see what colour a
new shape will use.
`theme` selects a preset (`light` / `chalk` / `blueprint` / `kraft` / `neon`) that
also swaps the shape `palette` (new shapes use the theme's colours), and toggles
a UI skin (`body.chalk` / `body.blueprint` /
`body.kraft` / `body.neon` — plain CSS overrides in `index.html`). The Page card
also has a **"Recolor artwork on theme change"** checkbox (opt-in, off by default):
when checked, switching themes also repaints existing shapes to the theme palette
(strokes + text fill; connector/dimension groups are left alone) and persists them;
when unchecked, only the board, UI skin and *new*-shape defaults change. The choice
is stored on the document as `recolor_on_theme` (via `PUT /api/settings`) and also
mirrored to localStorage, so a saved/shared project reopens with the same
preference. Add new themes to the `THEMES` map in `static/js/state.js`
(board colours + `grid_color` + `palette` + a `body.<name>` skin). The dev server sends
`Cache-Control: no-store` on every response so local edits to `static/js/*` /
`index.html` / `tailwind.css` are picked up on reload (no stale-cache surprises).

**Path transforms:** Fabric reports bounding-box `left`/`top` for a path, which
does not survive a rebuild from the original `d`. `toCanonical` therefore bakes
position/scale/rotation into `d` (`pathToAbsoluteD`, using
`fabric.util.transformPath`) and stores identity transform values. Lines are
baked the same way: their absolute endpoints are recovered from
`calcLinePoints()` transformed by `calcTransformMatrix()`, so line objects also
always reload with an identity transform. Keep new path/line features compatible
with that baking step.

**Stroke-inclusive geometry:** Fabric sizes an object *including* its stroke, so
a rect/circle's top-left that produces a given centre shifts by half the scaled
stroke. `buildFabric` therefore places rects and circles with
`originFromCenter()` (centre + scaled stroke) and lines with `makeLine()`
(centre origin, angle from the endpoint delta). Without this, shapes drift by
`strokeWidth / 2` on every save/reload.

**Flips:** rect/circle/text are mirror-symmetric, so `toCanonical` normalises
their scales to positive values and lets the decomposed angle carry orientation.
Paths and lines bake flips into their geometry instead (so negative scales are
preserved there).

## API surface (FastAPI, same-origin, no CORS needed)
| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/api/doc` | Current document (JSON). |
| POST   | `/api/objects` | Add/replace one object (body = canonical dict, must include `id`). |
| PUT    | `/api/objects/{id}` | Replace object by id. |
| DELETE | `/api/objects/{id}` | Remove object. |
| POST   | `/api/clear` | Remove all objects. |
| POST   | `/api/run` | Run Python `{ code }`; returns updated doc or `400` with error. |
| POST   | `/api/trace` | Multipart image + `mode`/`threshold`/`invert`/`remove_bg`/`smooth` → `{objects,count}`. **Does not mutate the doc.** |
| PUT    | `/api/settings` | Update `units` / `px_per_unit` / `width` / `height` / `background` / `grid_color` / `grid_size` / `export_grid` / `show_grid` / `show_rulers` / `theme` / `recolor_on_theme` / `layers`. |
| GET    | `/api/export/svg` \| `/png` \| `/pdf` | Export current doc (pixel-space). |
| GET    | `/api/export/dxf` | Export current doc as AutoCAD R2000 DXF (mm, y-up: cuttable outlines). |
| GET    | `/api/export/gcode` | Export current doc as profiling G-code (mm) with query params `feed` / `plunge` / `depth` / `safe` / `tool` (end-mill diameter; offsets closed outlines so the cut edge matches the drawing, holes inward) / `pass_depth` (max Z step per roughing pass; 0/omitted = single pass). |
| POST   | `/api/doc` | Full document replace (used by frontend persist / undo / restore). |
| GET    | `/api/projects` | List saved named project files (M5). |
| POST   | `/api/project/save` | Save current doc to `./projects/<name>.json` (M5). |
| POST   | `/api/project/load` | Load a named project into the doc (M5). |
| DELETE | `/api/project/{name}` | Delete a saved project (M5). |

The live document is rooted in **`core/store.py`'s `DocStore`** (H2,
review.md §6.2) and exposed through `server.py` as the module-level `doc`.
Every document-touching endpoint now resolves its document via a `current_doc`
dependency that reads an optional `X-Session-Id` header: requests **without**
that header use the shared default document (preserving the original
single-document behaviour), while a session id routes them to an isolated
document keyed in the store. This lets multiple browser tabs / users keep
separate in-memory documents without reworking every endpoint.
Named projects persist to `./projects/*.json` (M5); the frontend also mirrors
the current canvas into `localStorage` so a reload survives even a server
restart. `Document.load_dict()` replaces the whole doc from a saved dict.

## Coding conventions
- **Python:** type hints on public functions; factories in `document.py` return
  canonical dicts; keep `core/` side-effect-free except rendering to bytes.
- **JavaScript:** Fabric is **v5.3.1** (vendored). v6 changed the API — if you
  upgrade, expect to rewrite the frontend modules. Edit logic stays in the two
  converter functions (`buildFabric`/`toCanonical` in `static/js/sync.js`),
  not scattered handlers.
- **Snapping:** the three checkboxes `snapGrid` / `snapObjects` / `snapPage` (in the
  toolbar) drive `object:moving` and `object:scaling` handlers in
  `static/js/tools.js`. The grid
  step comes from `doc.grid_size` via `gridStep()`; object/page snapping uses
  `objectSnapDelta` / `collectGuides` (with `SNAP_DIST` px tolerance). Hold **Alt**
  while dragging/resizing to move freely. These prefs are **persisted per document**
  (`doc.snap_grid` / `doc.snap_objects` / `doc.snap_page`, via `PUT /api/settings`),
  so they travel with saved projects and survive reload.
- **Naming:** canonical fields are snake_case; Fabric fields are camelCase
  (handled in the converters).

## Recipes
**Add a new shape type**
1. Add a factory in `core/document.py` (e.g. `polygon(...)`) producing a
   canonical dict with a new `type`.
2. Handle it in `core/export.py` (`_svg_obj` + `_mpl_obj`) and in
   `static/js/sync.js` (`buildFabric` + `toCanonical`).
3. Optional: a toolbar button in `index.html` + an `addShape` branch (in
   `static/js/page.js`).

**Add an exporter** (e.g. DXF)
1. New function in `core/export.py` returning `bytes`/`str`.
2. New `GET /api/export/dxf` route in `server.py` returning a `Response`.
3. A link in `index.html` exports area.

**Add a canvas tool**
- Add a button in `index.html` and a branch in `addShape()` in `static/js/page.js`; the
  backend needs no change if it's an existing `type`.

**Add a console helper**
- Expose it in the `env` dict in `core/runner.py` (it already provides
  `rect/circle/line/path/text/dimension/add/clear/doc/math` plus the M4-style
  helpers `connect(a, b, fa, ta)`, `row(objs, gap, x0, y0)`,
  `column(objs, gap, x0, y0)`, `grid(objs, cols, gap_x, gap_y, x0, y0)`).

## Smoke-testing
```powershell
# document
Invoke-RestMethod http://127.0.0.1:8000/api/doc
# run console code
$body = @{ code = "clear(); rect(450,350,120,80, fill='#ffe066')" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/run -ContentType application/json -Body $body
# exports (raw bytes -> use -UseBasicParsing with Invoke-WebRequest)
(Invoke-WebRequest http://127.0.0.1:8000/api/export/svg -UseBasicParsing).Content
# image trace (multipart needs curl.exe on Windows PowerShell)
curl.exe -s -X POST http://127.0.0.1:8000/api/trace `
  -F "file=@C:\path\to\logo.png" -F "mode=outline" -F "threshold=128" -F "smooth=true"
```

`mode` is `outline` (closed filled/silhouette contours) or `edges` (open
Canny-style lines). `smooth=true` emits cubic Bézier `C` segments (Catmull-Rom);
`false` emits polylines (`L`).

## Gotchas
- **matplotlib** runs with the `Agg` backend (headless). Don't remove the
  `matplotlib.use("Agg")` line in `export.py`.
- **The console is AST-sandboxed (H1, review.md §6.2).** `runner.py` parses the
  user script and rejects imports, `global`/`nonlocal`, *any* dunder attribute
  access (this closes the `().__class__.__subclasses__()` style escape that a
  mere restricted-builtins map leaves open), and calls to names that aren't a
  console helper, a safe builtin, or defined by the script itself — *before*
  anything reaches `exec`. It is still meant for a local single-user DIY tool,
  not hostile multi-user input, but the trivial `exec`-escape is gone. A
  Pyodide-in-browser variant remains a future option only if the project's
  no-CDN/offline rule is ever relaxed.
- **Console runs are transactional.** `runner.run()` deep-copies the document
  before `exec` and restores it via `load_dict` if the script raises, so a
  script that errors part-way through can't leave the document half-mutated.
  `to_dict()` shares the live `objects` list by reference, which is why the
  snapshot must be a `deepcopy` — and why a post-rollback doc differs from a
  pre-run snapshot by the `layers` backfill (see below).
- **Layers are backfilled on load.** `Document.load_dict()` assigns
  `meta["layer"]` to every object that lacks a valid layer (older project files
  and any object referencing a deleted layer resolve to the base layer). A fresh
  `Document` starts with one `"layer-base"`; `to_dict()` includes the `layers`
  list. The Layers panel (`static/js/layers.js`) and this schema section already
  document the feature end-to-end, so the earlier "data model ahead of the
  docs" gap is resolved (see `docs/review.md` §6.1).
- **Line/path** store absolute geometry with an identity transform. `toCanonical`
  bakes moves/scales/rotations into the endpoints/`d`, so the canonical dict is
  always the final geometry. If you add transforms that must stay live, extend
  the schema + exporters together.
- **Rect/circle export**: non-uniform scale on a circle exports as an `<ellipse>`
  (SVG) / `Ellipse` (matplotlib); rotation is applied about the object centre in
  both exporters.
- **Fabric v5 origin quirks:** rect/circle `left`/`top` must account for
  stroke-inclusive dimensions + rotation (`originFromCenter`), and lines must be
  built centre-origin (`makeLine`). Don't hand-construct these from centre
  coordinates or shapes will drift by half a stroke on reload.
- **Don't hand-edit the running server's in-memory doc** expecting it to
  persist — there is no persistence yet.
- **`/api/trace` is read-only** — it returns preview `path` objects and never
  adds them. The frontend commits each via `POST /api/objects`, then reloads.
  `smooth` only changes the emitted `d`; outlines stay closed, edges open.
- **`rembg` is optional and lazy.** It is *not* in `requirements.txt` (install
  `pip install "rembg[cpu]"`), and the U2Net model downloads on first
  `remove()`. The "Remove background" toggle therefore fails with a friendly
  `400` if it is missing.
- **scikit-image is imported lazily** inside `core/trace.py` so the server
  starts fast; keep it that way.

## Installed agent skills
Global skills (`~\.agents\skills\`) that guide AI agents on this project. They
are **prompt/knowledge packs, not runtime dependencies** — they don't affect
`requirements.txt` or how the app runs. Reinstall/refresh with the commands.

| Skill | Why we use it | Install |
|-------|---------------|---------|
| `fastapi-python` | FastAPI/backend conventions | `npx skills add mindrally/skills@fastapi-python -g -y` |
| `python-testing-patterns` | pytest patterns for `core/` tests (M5) | `npx skills add wshobson/agents@python-testing-patterns -g -y` |
| `web-design-guidelines` | UI/UX + accessibility review of `static/` | `npx skills add vercel-labs/agent-skills@web-design-guidelines -g -y` |
| `architecture-decision-records` | record significant decisions (see `plan.md` / this folder) | `npx skills add wshobson/agents@architecture-decision-records -g -y` |

Style guidance is already covered by the built-in `python-code-style` skill, so
no external Python-style skill is needed.

## Tests

`core/` is pure Python and has a pytest suite in `tests/` (run `pytest` from the
project root — `pyproject.toml` puts the root on `pythonpath`). Coverage:

- `test_document.py` — canonical schema, factories, `add`/`find`/`upsert`/`remove`,
  `to_dict`/`load_dict` round-trip + tolerance of unknown/missing keys.
- `test_export.py` — SVG tags, PNG/PDF byte signatures, DXF structure + **mm scaling
  & y-up flip** + connector→polyline resolution, G-code `G21`/`G90`/`M2` + plunge,
  and the `feed`/`plunge`/`depth`/`safe` params.
- `test_runner.py` — console helpers, `connect`, `row`/`column`/`grid` layout,
  blocked dangerous builtins, exceptions surfaced as `RuntimeError`.
- `test_trace.py` — image→`path` tracing (skipped automatically if scikit-image is
  absent), `min_area` filtering, and smooth-Bézier output.
- `test_ui_smoke.py` — **optional** Playwright end-to-end check (see below).

Keep these green when you change the model or exporters.

### Optional Playwright UI smoke test

`tests/test_ui_smoke.py` boots the real `uvicorn` server, loads the page in
headless Chromium, and fails on:

- uncaught JavaScript exceptions (`pageerror`),
- `console.error` messages (favicon noise ignored),
- failed asset requests (proves every `/static/*` file loads — i.e. offline
  works),
- missing required element IDs, or a zero-sized Fabric canvas.

It is **skipped by default** so the fast `pytest` run stays browser-free. Enable
it with an env flag (it writes a screenshot to `docs/ui-smoke.png`):

```powershell
pip install playwright
playwright install chromium
$env:UI_SMOKE_RUN = "1"
pytest tests/test_ui_smoke.py        # or just `pytest` — it's collected but skipped otherwise
$env:UI_SMOKE_RUN = ""
```

Override the screenshot path with `UI_SMOKE_SHOT=path/to/shot.png`. The test
uses a free port and tears the server down after itself, so it doesn't collide
with a server you have running on `:8000`.

There are two UI tests:

- `test_ui_loads_without_errors_and_renders` — **structural**: page loads with no
  JS/console/asset errors (proves the offline vendored assets resolve), all
  required element IDs are present, and the Fabric canvas is sized.
- `test_console_run_roundtrip` — **functional**: types a console program, clicks
  **Run**, waits for completion, then asserts the server document actually gained
  the shapes and that `/api/export/svg` contains them. This is the test that was
  historically impossible in-sandbox (headless Chrome stalled on the Fabric CDN
  under `--virtual-time-budget`); vendoring Fabric + real-time headless Chrome
  unblocks it, so real browser interactions are now covered.
