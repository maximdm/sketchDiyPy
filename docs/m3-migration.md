# M3 — Coordinate-convention unification: migration plan

**Status:** **Implemented (Option B), 2026-11-04.** The uniform-origin
convention is now live: `line`/`dimension`/`path` carry a meaningful `x,y`
centroid origin (computed by the factories, and migrated for legacy objects by
`Document.load_dict`). The geometry of each type is unchanged. A baseline +
uniformity test harness (`tests/test_m3_baseline.py`) is in place and green; it
is the regression net proving the renderers produce identical output for
identical geometry. Source recommendation: `docs/review.md` §6.3 M3.

---

## 1. The problem

`sketchDiy`'s canonical object schema uses **two different coordinate
conventions at once**:

- **Center-based** — `rect`, `circle`, `text` store an object origin `x,y` that
  is the *centre* of the shape, plus `geometry` that is half-extents / radius /
  text.
- **Absolute** — `line`, `dimension`, `path` ignore `x,y` (always `0`) and store
  absolute points (`x1,y1,x2,y2` or an SVG `d` string).

It works today only because every exporter and the frontend converter special-
case each type, and `docs/AGENTS.md` has to explain the split. As the model
grows (new shape types, new exporters) each one re-implements a centre→absolute
or absolute→centre translation, and that is where drift/bugs creep in. M3 is
about removing that split.

---

## 2. Current conventions (with evidence)

### Center-based (`x,y` = centre)
| Type | Stored `x,y` | `geometry` |
|------|--------------|------------|
| `rect` | centre | `{w, h}` |
| `circle` | centre | `{r}` |
| `text` | centre | `{text, fontSize}` |

- Factories: `core/document.py` `rect()`/`circle()`/`text()` pass `x,y` straight
  through; `line()`/`dimension()` pass `x=0, y=0` (see below).
- Backend exporters: `core/export.py` `_svg_obj` maps rect centre→top-left
  (`x0 = o["x"] - w/2`), uses `o["x"]` as `cx`/`cy` for circle/text;
  `_cut_polylines` and `_bbox` derive corners/centroid from `o["x"],o["y"]`.
- Frontend: `static/js/sync.js` `buildFabric` places rect/circle via
  `originFromCenter(o.x, o.y, …)`; `toCanonical` sets `x = dec.translateX`
  (centre) for rect/circle/text.

### Absolute (`x,y` = `0`, geometry carries the points)
| Type | Stored `x,y` | `geometry` |
|------|--------------|------------|
| `line` | `0,0` | `{x1,y1,x2,y2}` |
| `dimension` | `0,0` | `{x1,y1,x2,y2,offset}` |
| `path` | `0,0` (rotation centre only; identity transform) | `{d}` (absolute SVG path) |
| `connector` | `0,0` | `{from:{id,anchor}, to:{id,anchor}}` (semantic refs, **decoupled** from coords) |

- Factories: `core/document.py` `line()`/`dimension()` hard-code `x=0,y=0`;
  `path()` stores `x,y` as a rotation centre but the path is baked absolute with
  identity transform.
- Backend exporters: `_svg_obj` renders line endpoints straight from
  `g["x1"]…` and **never reads `o["x"]/o["y"]`**; `_cut_polylines` / `_dim_geometry`
  use `geometry` only. `_bbox` recomputes the centre *from geometry* for these
  types (because `o["x"],o["y"]` is useless).
- Frontend: `toCanonical` explicitly zeroes `x,y,rotation,scale` for
  line/path/dimension and stores absolute geometry; `buildFabric` builds them
  from geometry and never reads `o["x"]/o["y"]`.

**Net effect:** for `line`/`dimension`/`path`, `o["x"]`/`o["y"]` is *always* `0`
and unused. That is the inconsistency — and the latent bug source: any future
code that reads `o["x"]/o["y"]` as "where is this object" for those types gets
`0,0` and breaks.

---

## 3. Why it is a latent bug source (concrete)

- `_bbox` and `anchor_point` in `core/export.py` already special-case
  line/dimension to recompute the centre from `geometry`. That is extra code that
  must be kept in sync every time a shape is added.
- Selection / move / align / snap on the frontend read position from Fabric
  objects (safe today), but any server-side or script-side logic that assumes
  `o["x"],o["y"]` is a meaningful origin would be wrong for three of the seven
  types.
- A new shape type has to "pick a side" and every exporter has to be taught it.

---

## 4. Candidate target conventions

### Option A — absolute bounding box for *all* types
Every shape stores an absolute bounding box (e.g. rect as `{x1,y1,x2,y2}` or
top-left + `w,h`; circle as its bbox + `r`; line/dim/path keep endpoints).
Rotation is about the bbox centre. This removes the centre/absolute split
entirely.

- **Pros:** one true convention; rotation pivot and bbox/anchor become trivial.
- **Cons:** large blast radius. It touches the **stroke-inclusive**
  `originFromCenter` math in `sync.js` (rect/circle are placed via Fabric
  `left/top` = top-left, with half-stroke offsets — see `docs/AGENTS.md`
  "Stroke-inclusive geometry") and `_svg_obj`/`_cut_polylines`/`_mpl_obj` on the
  backend. The console helper signatures (`rect(x,y,w,h)` meaning centre) would
  change meaning. High risk of half-stroke drift regressions.

### Option B — uniform origin (recommended first step)
Keep the *geometry* exactly as it is today (centre-based `w/h/r` for
rect/circle/text; absolute endpoints/`d` for line/dim/path) but give **every**
object a meaningful, consistent `x,y` = its **centroid** (the rotation pivot and
selection origin). Concretely: set `x,y` for `line`/`dimension`/`path` to their
bounding-box centre instead of `0`.

- **Pros:** tiny blast radius. The exporters already ignore `o["x"]/o["y"]` for
  line/dim/path and use `geometry`; `buildFabric` for those types also ignores
  `o["x"]/o["y"]`. The only code that changes is: the factories (compute
  centroid), `toCanonical` (stop zeroing `x,y`), and `load_dict` (dual-read for
  legacy docs). It removes the actual inconsistency — `o["x"],o["y"]` becomes a
  meaningful origin for *all* types — which is what bites in practice. Anchors,
  bbox, and selection become uniform for free.
- **Cons:** the *internal* geometry is still "mixed" (centre-based vs absolute),
  but the object origin is uniform. A later hardening pass could promote to A.

**Recommendation:** do **Option B** now — it is the pragmatic unification that
actually eliminates the latent bugs with minimal risk. (The review phrased the
target as "absolute points everywhere"; B achieves the practical goal — one
uniform origin — without rewriting the stroke-sensitive placement math. Option A
can be deferred until a concrete requirement forces it, e.g. a generic
"transform every shape uniformly" tool or an external interchange format that
assumes bounding boxes.)

---

## 5. Migration steps (Option B)

1. **Baseline + contract tests (DONE).** `tests/test_m3_baseline.py` pins the SVG
   / cut-polyline output for a current-schema fixture scene. These **must stay
   green** before and after the migration — they prove the renderers produce
   identical output for identical geometry.
2. **Factories (`core/document.py`).** `line` / `dimension` / `path` compute and
   store `x,y` = centroid of their geometry (add a small `_centroid(type, g)`
   helper). `rect` / `circle` / `text` are unchanged.
3. **Frontend `toCanonical` (`static/js/sync.js`).** For line/path/dimension set
   `base.x/base.y` to the geometry centroid instead of `0`. `buildFabric` needs
   no change (it already ignores `o["x"]/o["y"]` for those types).
4. **`load_dict` (`core/document.py`).** Dual-read: when a stored object has
   `x,y ≈ 0` but absolute geometry (legacy schema), recompute the centroid into
   `x,y` on load. This migrates the in-memory default doc **and every
   `./projects/*.json`** automatically — no separate migration script or file
   rewrite needed.
5. **Exporters (`core/export.py`).** Expected to need **no change** (they already
   use geometry for line/dim/path; rect/circle/text use `o["x"],o["y"]` as
   centre, unchanged). Add a uniformity regression test asserting
   `_bbox(o)["cx"] == o["x"]` and `["cy"] == o["y"]` for every type.
6. **Console helpers.** `rect/circle/line/text/dimension/connect/row/column/grid`
   keep their current signatures and semantics — no user-visible change.
   `line(0,0,80,120)` still means exactly those absolute endpoints.
7. **Docs.** Update `docs/AGENTS.md` (canonical schema section: `x,y` = centroid
   for all; line/dim/path geometry still absolute) and `docs/review.md` M3 status.

---

## 6. Backward compatibility

- `load_dict` recomputes centroids for legacy objects, so existing
  `./projects/*.json` files and the default starter doc keep working with no
  rewrite.
- The frontend `localStorage` autosave re-syncs on next persist (the new
  `toCanonical` emits the centroid); this is a harmless one-time change.

---

## 7. Test strategy / acceptance

- `tests/test_m3_baseline.py` output-contract tests stay green **before and
  after** migration (renderers unchanged for the same geometry).
- New uniformity tests: `_bbox(o)["cx"] == o["x"]` and `["cy"] == o["y"]` for a
  fixture containing one of every type.
- **Frontend converter parity:** `buildFabric` / `toCanonical` are coupled to
  Fabric and therefore **cannot** be unit-tested under Node/Vitest (Fabric is a
  browser global, not installed in Node). Verify frontend parity with the
  Playwright e2e smoke (`tests/test_ui_smoke.py`: `test_console_run_roundtrip`,
  `test_ui_loads_without_errors_and_renders`) **plus a manual browser check**
  after migration. This limitation is called out explicitly so it is not missed.
- Full `pytest` suite remains green (currently 91 passed / 13 skipped).

---

## 8. Risks & mitigations

- **Stroke-inclusive geometry:** Option B does **not** touch `originFromCenter`,
  so the half-stroke offset logic is untouched. (Option A would.)
- **Path baking:** `path` keeps absolute `d` + identity transform; the centroid
  is just metadata — no change to the baking step.
- **Rotation:** `o["x"],o["y"]` becomes the rotation pivot consistently. For
  line/dim/path the transform is identity (rotation `0`), so the pivot change is
  invisible; confirm in the e2e smoke.
- **Connectors:** already reference-based, unaffected.

---

## 9. When to promote to Option A

If a future requirement needs a generic "transform/normalize all shapes
uniformly" tool, or an external interchange format that assumes bounding boxes,
revisit Option A. Until then Option B is sufficient and far safer.
