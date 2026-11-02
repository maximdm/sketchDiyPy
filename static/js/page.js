/* Page / theme concerns: artboard board + grid painting, the Page card inputs,
   theme switching + swatches, the size/units readouts, and dropping new shapes. */

import { canvas, state, $, genId, palette, THEMES } from "./state.js";
import { api, toCanonical, makeLine, refreshDimensionLabels } from "./sync.js";
import { scheduleCommit } from "./persistence.js";

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
  const map = { rect: palette.rectStroke, circle: palette.circleStroke,
                line: palette.lineStroke, text: palette.textStroke };
  document.querySelectorAll("[data-shape]").forEach((b) => {
    const sw = b.querySelector(".sw");
    if (sw && map[b.dataset.shape]) sw.style.background = map[b.dataset.shape];
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

// ---- toolbar: add shapes ------------------------------------------------
function addShape(kind) {
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
  obj.id = id;
  obj._meta = { layer: state.activeLayerId };
  canvas.add(obj);
  canvas.setActiveObject(obj);
  api("POST", "/api/objects", toCanonical(obj)).catch(console.error);
  scheduleCommit();
}

document.querySelectorAll("[data-shape]").forEach((b) =>
  b.addEventListener("click", () => addShape(b.dataset.shape))
);

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
  updateSizeReadout, updateReadout, applyPageSize,
};
