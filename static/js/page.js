/* Page / theme concerns: artboard board + grid painting, the Page card inputs,
   theme switching + swatches, the size/units readouts, and dropping new shapes. */

import { canvas, state, $, genId, palette, THEMES } from "./state.js";
import { api, toCanonical, makeLine, refreshDimensionLabels } from "./sync.js";
import { scheduleCommit } from "./persistence.js";
import { addNote } from "./plan.js";
import { eraseTarget } from "./tools.js";

// Paint the board background colour and grid onto the (transparent) Fabric canvas
// element. The grid is a CSS background-image, so it sits *behind* the artwork and
// re-tints with the chosen colour (and disappears when the grid is toggled off).
function applyBoard() {
  if (!state.doc) return;
  const el = canvas.lowerCanvasEl;
  if (!el) return;
  el.style.backgroundColor = state.doc.background || "#ffffff";
  if (state.doc.show_grid) {
    const c = state.doc.grid_color || "#eef2f7";
    const s = Math.max(4, Math.min(200, state.doc.grid_size || 20));
    el.style.backgroundImage =
      `linear-gradient(${c} 1px, transparent 1px), linear-gradient(90deg, ${c} 1px, transparent 1px)`;
    el.style.backgroundSize = `${s}px ${s}px`;
  } else {
    el.style.backgroundImage = "none";
  }
}

// Mirror the document's page settings into the Page card inputs + UI skin.
function syncPageControls() {
  if (!state.doc) return;
  const ppu = pxPerUnit();
  const wIn = state.doc.width / ppu;
  const hIn = state.doc.height / ppu;
  $("pageW").value = Math.round(wIn * 100) / 100;
  $("pageH").value = Math.round(hIn * 100) / 100;
  $("pageBg").value = state.doc.background || "#ffffff";
  $("gridColor").value = state.doc.grid_color || "#eef2f7";
  $("gridSize").value = Math.round(state.doc.grid_size || 20);
  $("gridToggle").checked = !!state.doc.show_grid;
  $("exportGrid").checked = !!state.doc.export_grid;
  $("pageRulers").checked = !!state.doc.show_rulers;
  // Snap preferences are persisted on the document; mirror them into the
  // toolbar checkboxes and the live snap flags.
  state.snapGrid = state.doc.snap_grid !== false;
  state.snapObjects = state.doc.snap_objects !== false;
  state.snapPage = state.doc.snap_page !== false;
  $("snapGrid").checked = state.snapGrid;
  $("snapObjects").checked = state.snapObjects;
  $("snapPage").checked = state.snapPage;
  const theme = state.doc.theme || "light";
  $("themeSelect").value = theme;
  ["chalk", "blueprint", "kraft", "neon"].forEach((c) =>
    document.body.classList.toggle(c, theme === c));
  $("recolorOnTheme").checked = recolorOnThemeEnabled();
  updateToolbarSwatches();
}

// Paint a small colour swatch on each shape-tool button so it's obvious what
// colour a newly-dropped shape will use under the current theme palette.
function updateToolbarSwatches() {
  document.querySelectorAll("[data-shape]").forEach((b) => {
    const sw = b.querySelector(".sw");
    if (!sw) return;
    const k = b.dataset.shape;
    const c = k === "circle" ? palette.circleStroke
            : k === "line" || k === "free" ? palette.lineStroke
            : k === "text" ? palette.textStroke
            : palette.rectStroke;
    sw.style.background = c;
  });
}

// Whether switching themes should repaint existing artwork to the theme palette.
// Off by default (opt-in): the board, UI skin and *new*-shape defaults still
// change, but the user's existing stroke/fill colours are left untouched.
// The choice lives on the document (so a saved/shared project reopens with the
// same preference) and is included in the autosave snapshot + /api/doc.
function recolorOnThemeEnabled() {
  return !!(state.doc && state.doc.recolor_on_theme);
}

// Recolour a Fabric object to the theme palette. Groups (connectors, dimensions)
// are skipped so their anchor geometry isn't disturbed.
function recolorToTheme(obj, pal) {
  const t = (obj.type || "").toLowerCase();
  if (t === "group" || t === "activegroup") return;
  if (t === "rect") obj.set({ stroke: pal.rectStroke, fill: pal.rectFill });
  else if (t === "circle") obj.set({ stroke: pal.circleStroke });
  else if (t === "line" || t === "path") obj.set({ stroke: pal.lineStroke });
  else if (t === "itext" || t === "text") obj.set({ fill: pal.textStroke });
}

// Apply a named theme: board colours, grid, shape palette, UI skin, and recolor
// the existing artwork so the change is unmistakable.
function applyTheme(name) {
  const t = THEMES[name];
  if (!t || !state.doc) return;
  state.doc.background = t.background;
  state.doc.grid_color = t.grid_color;
  state.doc.show_grid = t.show_grid;
  state.doc.show_rulers = t.show_rulers;
  state.doc.theme = name;
  Object.assign(palette, t.palette);
  $("pStroke").value = t.palette.rectStroke;
  $("pFill").value = t.palette.rectFill;
  applyBoard();
  syncPageControls();
  canvas.requestRenderAll();
  // When the user opts in, repaint existing artwork to the theme palette and
  // persist the changes. Otherwise only the board/UI/new-shape defaults change.
  if (recolorOnThemeEnabled()) {
    canvas.getObjects().forEach((o) => recolorToTheme(o, t.palette));
    canvas.requestRenderAll();
    canvas.getObjects()
      .filter((o) => o.id && (o.type || "").toLowerCase() !== "group")
      .forEach((o) => api("PUT", `/api/objects/${o.id}`, toCanonical(o)).catch(console.error));
  }
  api("PUT", "/api/settings", {
    background: state.doc.background, grid_color: state.doc.grid_color,
    show_grid: state.doc.show_grid, show_rulers: state.doc.show_rulers, theme: name,
  }).catch(console.error);
  scheduleCommit();
}

// ---- 2D primitive path generators ---------------------------------------
// Expanded primitive set. Each generator returns a Fabric path `d` string in
// absolute canvas coordinates, centred at (cx, cy). They are dropped as plain
// `path` objects so they round-trip through the canonical schema, extrude into
// 3D solids, and export like every other shape.
function polyPoints(cx, cy, r, sides, rot = -Math.PI / 2) {
  const pts = [];
  for (let i = 0; i < sides; i++) {
    const a = rot + (i * 2 * Math.PI) / sides;
    pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return pts;
}
function pathFromPts(pts, closed = true) {
  let d = pts.map((p, i) => (i === 0 ? "M " : "L ") +
    p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  if (closed) d += " Z";
  return d;
}
function starPoints(cx, cy, outer, inner, points = 5) {
  const pts = [];
  for (let i = 0; i < points * 2; i++) {
    const r = i % 2 === 0 ? outer : inner;
    const a = -Math.PI / 2 + (i * Math.PI) / points;
    pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return pts;
}
function ellipsePoints(cx, cy, rx, ry, seg = 48) {
  const pts = [];
  for (let i = 0; i < seg; i++) {
    const a = (i * 2 * Math.PI) / seg;
    pts.push([cx + rx * Math.cos(a), cy + ry * Math.sin(a)]);
  }
  return pts;
}
function roundedRectPath(cx, cy, w, h, r) {
  const x0 = cx - w / 2, y0 = cy - h / 2, x1 = cx + w / 2, y1 = cy + h / 2;
  r = Math.min(r, w / 2, h / 2);
  return `M ${x0 + r} ${y0} L ${x1 - r} ${y0} Q ${x1} ${y0} ${x1} ${y0 + r} ` +
         `L ${x1} ${y1 - r} Q ${x1} ${y1} ${x1 - r} ${y1} ` +
         `L ${x0 + r} ${y1} Q ${x0} ${y1} ${x0} ${y1 - r} ` +
         `L ${x0} ${y0 + r} Q ${x0} ${y0} ${x0 + r} ${y0} Z`;
}
function trapezoidPath(cx, cy, wTop, wBot, h) {
  const x0 = cx - wBot / 2, x1 = cx + wBot / 2;
  const xt0 = cx - wTop / 2, xt1 = cx + wTop / 2;
  const y0 = cy - h / 2, y1 = cy + h / 2;
  return pathFromPts([[xt0, y0], [xt1, y0], [x1, y1], [x0, y1]]);
}
function parallelogramPath(cx, cy, w, h, skew) {
  const x0 = cx - w / 2, x1 = cx + w / 2;
  const y0 = cy - h / 2, y1 = cy + h / 2;
  return pathFromPts([[x0 + skew, y0], [x1 + skew, y0], [x1 - skew, y1], [x0 - skew, y1]]);
}
function arrowPath(cx, cy, w, h) {
  const x0 = cx - w / 2, x1 = cx + w / 2;
  const y0 = cy - h / 2, y1 = cy + h / 2;
  const headW = w * 0.4, shaftW = w * 0.28;
  return pathFromPts([
    [x0, y0], [x1 - headW, y0], [x1 - headW, cy - h * 0.32],
    [x1, cy], [x1 - headW, cy + h * 0.32], [x1 - headW, y1],
    [x0, y1], [x0, cy + shaftW / 2], [x0 - shaftW, cy], [x0, cy - shaftW / 2],
  ]);
}

// Map of shape kind -> generator returning {d, fill}. Stroke comes from the
// theme palette so new primitives match the board like the built-ins.
const SHAPE_GEN = {
  rounded: (cx, cy) => ({ d: roundedRectPath(cx, cy, 160, 110, 26), fill: palette.rectFill }),
  ellipse: (cx, cy) => ({ d: pathFromPts(ellipsePoints(cx, cy, 85, 60)), fill: palette.rectFill }),
  triangle: (cx, cy) => ({ d: pathFromPts(polyPoints(cx, cy, 80, 3)), fill: palette.rectFill }),
  pentagon: (cx, cy) => ({ d: pathFromPts(polyPoints(cx, cy, 85, 5)), fill: palette.rectFill }),
  hexagon: (cx, cy) => ({ d: pathFromPts(polyPoints(cx, cy, 85, 6)), fill: palette.rectFill }),
  heptagon: (cx, cy) => ({ d: pathFromPts(polyPoints(cx, cy, 85, 7)), fill: palette.rectFill }),
  octagon: (cx, cy) => ({ d: pathFromPts(polyPoints(cx, cy, 85, 8)), fill: palette.rectFill }),
  star: (cx, cy) => ({ d: pathFromPts(starPoints(cx, cy, 90, 40, 5)), fill: palette.rectFill }),
  diamond: (cx, cy) => ({ d: pathFromPts(polyPoints(cx, cy, 90, 4, 0)), fill: palette.rectFill }),
  trapezoid: (cx, cy) => ({ d: trapezoidPath(cx, cy, 90, 150, 100), fill: palette.rectFill }),
  parallelogram: (cx, cy) => ({ d: parallelogramPath(cx, cy, 150, 90, 35), fill: palette.rectFill }),
  arrow: (cx, cy) => ({ d: arrowPath(cx, cy, 150, 100), fill: palette.rectFill }),
};

// ---- toolbar: add shapes ------------------------------------------------
function addShape(kind) {
  if (freehandOn) setFreehand(false); // leaving the pencil for a placed shape
  const w = state.doc.width, h = state.doc.height;
  const cx = w / 2, cy = h / 2;
  let obj;
  const id = genId();
  if (kind === "rect")
    obj = new fabric.Rect({ left: cx - 75, top: cy - 50, width: 150, height: 100, fill: palette.rectFill, stroke: palette.rectStroke, strokeWidth: 2 });
  else if (kind === "circle")
    obj = new fabric.Circle({ left: cx - 50, top: cy - 50, radius: 50, fill: null, stroke: palette.circleStroke, strokeWidth: 2 });
  else if (kind === "line")
    obj = makeLine(cx - 80, cy, cx + 80, cy, { stroke: palette.lineStroke, strokeWidth: 2 });
  else if (kind === "text")
    obj = new fabric.IText("Double-click to edit", { left: cx, top: cy, fontSize: 28, fill: palette.textStroke, originX: "center", originY: "center" });
  else if (SHAPE_GEN[kind]) {
    const geo = SHAPE_GEN[kind](cx, cy);
    obj = new fabric.Path(geo.d, {
      fill: geo.fill, stroke: palette.rectStroke, strokeWidth: 2,
    });
    obj._d = geo.d; // ensure the canonical serializer has the raw `d`
  }
  if (!obj) return; // unknown shape kind — ignore
  obj.id = id;
  obj._meta = { layer: state.activeLayerId };
  canvas.add(obj);
  canvas.setActiveObject(obj);
  api("POST", "/api/objects", toCanonical(obj)).catch(console.error);
  scheduleCommit();
}

document.querySelectorAll("[data-shape]").forEach((b) =>
  b.addEventListener("click", () => {
    if (b.dataset.shape === "free") { toggleFreehand(); return; }
    addShape(b.dataset.shape);
  })
);

// ---- freehand pencil ----------------------------------------------------
// Click-drag on the canvas to sketch a freeform stroke. We capture the pointer
// ourselves (rather than Fabric's PencilBrush) so the stroke is a plain,
// editable `path` object that round-trips through the canonical schema like any
// other shape. Toggle the toolbar button (or press Esc) to leave freehand mode
// and return to normal selection/editing.
let freehandOn = false;
let fhPoints = null;   // points of the in-progress stroke (canvas coords)
let fhPreview = null;  // live preview path on the canvas

function fhColor() {
  return ($("pStroke") && $("pStroke").value) || palette.lineStroke;
}

function pointsToPath(pts) {
  return pts
    .map((pt, i) => (i === 0 ? "M " : "L ") + pt[0].toFixed(1) + " " + pt[1].toFixed(1))
    .join(" ");
}

function setFreehand(on) {
  freehandOn = on;
  // While sketching, disable object selection so drags draw instead of move.
  canvas.selection = !on;
  if (on) {
    if (eraserOn) setEraser(false); // freehand and eraser are mutually exclusive
    canvas.discardActiveObject();
  }
  document.querySelectorAll('[data-shape="free"]').forEach((b) =>
    b.classList.toggle("active", on));
  canvas.requestRenderAll();
}

function toggleFreehand() { setFreehand(!freehandOn); }

canvas.on("mouse:down", (opt) => {
  if (!freehandOn) return;
  const p = canvas.getPointer(opt.e);
  fhPoints = [[p.x, p.y]];
  if (fhPreview) { canvas.remove(fhPreview); fhPreview = null; }
});

canvas.on("mouse:move", (opt) => {
  if (!freehandOn || !fhPoints) return;
  const p = canvas.getPointer(opt.e);
  fhPoints.push([p.x, p.y]);
  if (fhPreview) canvas.remove(fhPreview);
  fhPreview = new fabric.Path(pointsToPath(fhPoints), {
    stroke: fhColor(), strokeWidth: 2.5, fill: null,
    selectable: false, evented: false,
  });
  canvas.add(fhPreview);
  canvas.requestRenderAll();
});

canvas.on("mouse:up", () => {
  if (!freehandOn || !fhPoints) return;
  const pts = fhPoints;
  fhPoints = null;
  if (fhPreview) { canvas.remove(fhPreview); fhPreview = null; }
  if (pts.length < 2) return; // a bare click, not a stroke
  const path = new fabric.Path(pointsToPath(pts), {
    stroke: fhColor(), strokeWidth: 2.5, fill: null,
  });
  path.id = genId();
  path._meta = { layer: state.activeLayerId };
  path._d = pointsToPath(pts); // ensure the canonical serializer has the raw `d`
  canvas.add(path);
  canvas.requestRenderAll();
  api("POST", "/api/objects", toCanonical(path)).catch(console.error);
  scheduleCommit();
  // Mirror the sketch as a note in the Plan tab so hand-drawn annotations are
  // captured in the written plan (editable / removable like any other note).
  addNote("✎ Hand-drawn sketch", "sketch");
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && freehandOn) setFreehand(false);
});

// ---- eraser tool --------------------------------------------------------
// Click (or click-drag) over an object to delete it, cascading connectors that
// reference it — just like the Delete key. Exclusive with freehand and the
// Dimension tool: turning it on drops the others.
let eraserOn = false;

function setEraser(on) {
  eraserOn = on;
  // Disable box-selection while erasing so a drag deletes instead of marqueeing.
  canvas.selection = !on;
  if (on) {
    if (freehandOn) setFreehand(false);
    canvas.discardActiveObject();
  }
  document.querySelectorAll('[data-action="tool-eraser"]').forEach((b) =>
    b.classList.toggle("active", on));
  canvas.defaultCursor = on ? "crosshair" : "default";
  canvas.requestRenderAll();
}

function toggleEraser() { setEraser(!eraserOn); }

function eraseAt(opt) {
  if (!eraserOn || !opt || !opt.target) return;
  const t = opt.target;
  // Skip the selection marquee, and structural overlays (dimensions/connectors
  // are evented:false so won't normally be targets, but guard anyway).
  if (t.type === "activeSelection" || t._isDimension || t._isConnector) return;
  eraseTarget(t);
}

canvas.on("mouse:down", (opt) => eraseAt(opt));
canvas.on("mouse:move", (opt) => {
  if (opt && opt.e && opt.e.buttons === 1) eraseAt(opt);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && eraserOn) setEraser(false);
});

// ---- units / readout ----------------------------------------------------
const UNIT_MM = { mm: 1, cm: 10, inch: 25.4 };

function pxPerUnit() {
  const pxPerMm = (state.doc && state.doc.px_per_unit) || 3.78;
  const factor = UNIT_MM[(state.doc && state.doc.units) || "mm"] || 1;
  return pxPerMm * factor;
}

function fmtPx(px) {
  const units = (state.doc && state.doc.units) || "mm";
  const v = px / pxPerUnit();
  const digits = v >= 100 ? 0 : v >= 10 ? 1 : 2;
  return `${v.toFixed(digits)} ${units}`;
}

function objSizePx(o) {
  const sx = o.scaleX || 1, sy = o.scaleY || 1;
  if (o._isDimension && o._dimBase) {
    const d = o._dimBase;
    return { w: Math.hypot(d.x2 - d.x1, d.y2 - d.y1), h: 0 };
  }
  if (o.type === "rect") return { w: o.width * sx, h: o.height * sy };
  if (o.type === "circle") return { w: 2 * o.radius * sx, h: 2 * o.radius * sy };
  if (o.type === "line") {
    // Fabric keeps a line's extent only in width/height.
    return { w: Math.hypot(o.width || 0, o.height || 0) * sx, h: 0 };
  }
  return { w: (o.width || 0) * sx, h: (o.height || 0) * sy };
}

function updateSizeReadout() {
  const el = $("selSize");
  if (!el) return;
  const o = canvas.getActiveObject();
  if (!o || !state.doc) { el.textContent = ""; return; }
  if (o.type === "activeSelection") {
    const r = o.getBoundingRect(true);
    el.textContent = `Selection: ${fmtPx(r.width)} × ${fmtPx(r.height)}`;
    return;
  }
  const { w, h } = objSizePx(o);
  const label = o._isDimension ? "Dimension"
    : o._isConnector ? "Connector"
    : ({
    rect: "Rectangle", circle: "Circle", line: "Line",
    path: "Path", "i-text": "Text",
  }[o.type] || "Object");
  const rot = o.angle && !o._isDimension ? ` · ${o.angle.toFixed(1)}°` : "";
  el.textContent = h
    ? `${label}: ${fmtPx(w)} × ${fmtPx(h)}${rot}`
    : `${label}: ${fmtPx(w)}${rot}`;
}

function updateReadout() {
  if (!state.doc) return;
  $("units").value = state.doc.units || "mm";
  const W = (state.doc.width / pxPerUnit()).toFixed(1);
  const H = (state.doc.height / pxPerUnit()).toFixed(1);
  $("scaleReadout").textContent = `${W} × ${H} ${state.doc.units}`;
  updateSizeReadout();
}

// ---- Page: artboard size / colour / rulers ------------------------------
function applyPageSize() {
  const ppu = pxPerUnit();
  const w = Math.max(1, parseFloat($("pageW").value) || state.doc.width / ppu) * ppu;
  const h = Math.max(1, parseFloat($("pageH").value) || state.doc.height / ppu) * ppu;
  state.doc.width = w;
  state.doc.height = h;
  canvas.setDimensions({ width: w, height: h });
  updateReadout();
  canvas.requestRenderAll();
  api("PUT", "/api/settings", { width: w, height: h }).catch(console.error);
  scheduleCommit();
}

$("pageW").addEventListener("change", applyPageSize);
$("pageH").addEventListener("change", applyPageSize);

$("pageBg").addEventListener("input", (e) => {
  state.doc.background = e.target.value;
  applyBoard();
  api("PUT", "/api/settings", { background: state.doc.background }).catch(console.error);
  scheduleCommit();
});

$("pageRulers").addEventListener("change", (e) => {
  state.doc.show_rulers = e.target.checked;
  canvas.requestRenderAll();
  api("PUT", "/api/settings", { show_rulers: state.doc.show_rulers }).catch(console.error);
  scheduleCommit();
});

$("gridColor").addEventListener("input", (e) => {
  state.doc.grid_color = e.target.value;
  applyBoard();
  api("PUT", "/api/settings", { grid_color: state.doc.grid_color }).catch(console.error);
  scheduleCommit();
});

$("gridToggle").addEventListener("change", (e) => {
  state.doc.show_grid = e.target.checked;
  applyBoard();
  api("PUT", "/api/settings", { show_grid: state.doc.show_grid }).catch(console.error);
  scheduleCommit();
});

$("themeSelect").addEventListener("change", (e) => applyTheme(e.target.value));

// Opt-in: repaint existing artwork to the current theme palette. Toggling it on
// recolours immediately; toggling off leaves the (already-theme-coloured) shapes
// as they are — it only stops future theme switches from touching them.
$("recolorOnTheme").addEventListener("change", (e) => {
  const on = e.target.checked;
  if (state.doc) {
    state.doc.recolor_on_theme = on;
    api("PUT", "/api/settings", { recolor_on_theme: on }).catch(console.error);
    scheduleCommit();
  }
  if (on && state.doc) {
    const t = THEMES[state.doc.theme || "light"];
    if (t) {
      canvas.getObjects().forEach((o) => recolorToTheme(o, t.palette));
      canvas.requestRenderAll();
      canvas.getObjects()
        .filter((o) => o.id && (o.type || "").toLowerCase() !== "group")
        .forEach((o) => api("PUT", `/api/objects/${o.id}`, toCanonical(o)).catch(console.error));
    }
  }
});

// Grid spacing: re-tint the grid background with the new cell size (live).
$("gridSize").addEventListener("input", (e) => {
  if (!state.doc) return;
  const v = parseInt(e.target.value, 10);
  if (isNaN(v)) return;
  state.doc.grid_size = Math.max(4, Math.min(200, v));
  applyBoard();
  api("PUT", "/api/settings", { grid_size: state.doc.grid_size }).catch(console.error);
  scheduleCommit();
});

// Whether to bake the grid into SVG / PNG / PDF exports.
$("exportGrid").addEventListener("change", (e) => {
  if (!state.doc) return;
  state.doc.export_grid = e.target.checked;
  api("PUT", "/api/settings", { export_grid: state.doc.export_grid }).catch(console.error);
  scheduleCommit();
});

// G-code export options: end-mill diameter + roughing pass depth (review.md §6.2 H3).
// Appended to the G-code link's query string so a tool-compensated / multi-pass
// cut is one click away from the toolbar.
function updateGcodeLink() {
  const link = $("gcodeLink");
  if (!link) return;
  const tool = parseFloat($("gcTool").value) || 0;
  const pass = parseFloat($("gcPass").value) || 0;
  const q = new URLSearchParams();
  if (tool > 0) q.set("tool", String(tool));
  if (pass > 0) q.set("pass_depth", String(pass));
  link.href = q.toString() ? `/api/export/gcode?${q}` : "/api/export/gcode";
}
$("gcTool").addEventListener("input", updateGcodeLink);
$("gcPass").addEventListener("input", updateGcodeLink);
updateGcodeLink();

// Re-apply the current theme's default board / grid / palette (after manual tweaks).
$("resetTheme").addEventListener("click", () => {
  if (state.doc) applyTheme(state.doc.theme || "light");
});

$("units").addEventListener("change", async (e) => {
  const units = e.target.value;
  if (state.doc) state.doc.units = units;
  await api("PUT", "/api/settings", { units }).catch(console.error);
  refreshDimensionLabels();
  updateReadout();
  syncPageControls();
  canvas.requestRenderAll();
  scheduleCommit();
});

export {
  applyBoard, syncPageControls, updateToolbarSwatches, recolorOnThemeEnabled,
  recolorToTheme, applyTheme, addShape, pxPerUnit, fmtPx, objSizePx,
  updateSizeReadout, updateReadout, applyPageSize, toggleFreehand,
  toggleEraser, setEraser,
};
