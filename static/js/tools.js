/* Interactive canvas tools: the Dimension and Connector tools, selection +
   properties editing, grid/object/page snapping, align & distribute, arrow-key
   nudge, delete, and the ruler / alignment-guide overlay. */

import { canvas, state, $, genId } from "./state.js";
import {
  api, syncObj, loadDoc, makeDimension, nearestAnchor, refreshConnectors,
  refreshConnectorsFor, applyDimensionStyle, rgbToHex, toCanonical, setLocked,
} from "./sync.js";
import { pxPerUnit, updateSizeReadout } from "./page.js";
import { scheduleCommit } from "./persistence.js";

// px grid step for snapping, arrow-nudge and ruler ticks. Follows the document's
// customizable grid_size (clamped 4-200) so the visible board grid, ruler ticks
// and object snapping all stay in agreement.
const gridStep = () => Math.max(4, Math.min(200, (state.doc && state.doc.grid_size) || 20));
// Granular snapping controls (all default on). "Grid" snaps to the page grid
// step; "Objects" aligns edges/centres to other objects; "Page" aligns to the
// canvas edges and centre. Hold Alt while dragging/resizing to move freely.
const snapActive = () => state.snapGrid || state.snapObjects || state.snapPage;

// ---- M1: dimension tool (click two points) ------------------------------
function clearDimPreview() {
  if (state.dimPreview) {
    canvas.remove(state.dimPreview);
    state.dimPreview = null;
  }
  state.dimStart = null;
  canvas.requestRenderAll();
}

function setDimensionMode(on) {
  state.dimensionMode = on;
  canvas.selection = !on;
  canvas.skipTargetFind = on;
  canvas.defaultCursor = on ? "crosshair" : "default";
  $("dimBtn").classList.toggle("active", on);
  if (on) canvas.discardActiveObject();
  else clearDimPreview();
  canvas.requestRenderAll();
}

function snappedPoint(p, evt) {
  if (!state.snapGrid || (evt && evt.altKey)) return { x: p.x, y: p.y };
  return { x: roundGrid(p.x), y: roundGrid(p.y) };
}

const roundGrid = (v) => Math.round(v / gridStep()) * gridStep();

$("dimBtn").addEventListener("click", () => setDimensionMode(!state.dimensionMode));

canvas.on("mouse:down", (opt) => {
  if (!state.dimensionMode) return;
  const p = snappedPoint(canvas.getPointer(opt.e), opt.e);
  if (!state.dimStart) {
    state.dimStart = p;
    state.dimPreview = new fabric.Line([p.x, p.y, p.x, p.y], {
      stroke: "#1f6feb", strokeWidth: 1, strokeDashArray: [4, 4],
      selectable: false, evented: false, excludeFromExport: true,
    });
    canvas.add(state.dimPreview);
    canvas.requestRenderAll();
    return;
  }
  const a = state.dimStart;
  const b = p;
  clearDimPreview();
  if (Math.hypot(b.x - a.x, b.y - a.y) < 4) return;
  const obj = makeDimension(
    { x1: a.x, y1: a.y, x2: b.x, y2: b.y, offset: -26 },
    { stroke: "#1f6feb", strokeWidth: 1.5, fontSize: 14 },
    {}
  );
  obj.id = genId();
  canvas.add(obj);
  canvas.setActiveObject(obj);
  canvas.requestRenderAll();
  api("POST", "/api/objects", toCanonical(obj)).catch(console.error);
  setDimensionMode(false);
  scheduleCommit();
});

canvas.on("mouse:move", (opt) => {
  if (!state.dimensionMode || !state.dimStart || !state.dimPreview) return;
  const p = snappedPoint(canvas.getPointer(opt.e), opt.e);
  state.dimPreview.set({ x1: state.dimStart.x, y1: state.dimStart.y, x2: p.x, y2: p.y });
  canvas.requestRenderAll();
});

window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.dimensionMode) {
    e.preventDefault();
    setDimensionMode(false);
  }
});

$("clearBtn").addEventListener("click", async () => {
  if (!confirm("Clear all objects?")) return;
  state.suppressSync = true;
  canvas.clear();
  state.suppressSync = false;
  await api("POST", "/api/clear", {}).catch(console.error);
  scheduleCommit();
});

// ---- connector tool (select two objects, link them) ----------------------
function connectorCanonical(fromId, fromAnchor, toId, toAnchor) {
  return {
    type: "connector", x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1,
    geometry: {
      from: { id: fromId, anchor: fromAnchor },
      to: { id: toId, anchor: toAnchor },
    },
    style: { fill: null, stroke: "#495057", strokeWidth: 2, fontSize: 24 },
    meta: { layer: state.activeLayerId },
  };
}

$("connectBtn").addEventListener("click", async () => {
  const sel = canvas.getActiveObjects();
  if (sel.length !== 2) {
    alert("Select exactly two objects, then click Connect.");
    return;
  }
  const [a, b] = sel;
  const pa = a.getCenterPoint();
  const pb = b.getCenterPoint();
  const fa = nearestAnchor(a, pb);
  const ta = nearestAnchor(b, pa);
  try {
    await api("POST", "/api/objects", connectorCanonical(a.id, fa, b.id, ta));
    loadDoc(await api("GET", "/api/doc", null));
    scheduleCommit();
  } catch (err) {
    console.error(err);
  }
});

// ---- selection + properties ---------------------------------------------
const propsEmpty = $("props");
const propsBody = $("propsBody");

canvas.on("selection:created", () => showProps());
canvas.on("selection:updated", () => showProps());
canvas.on("selection:cleared", () => {
  propsEmpty.hidden = false;
  propsBody.hidden = true;
  state.snapGuides = { xs: [], ys: [] };
  canvas.requestRenderAll();
  updateSizeReadout();
});

function activeOnes() {
  const a = canvas.getActiveObjects();
  return a.length ? a : [canvas.getActiveObject()].filter(Boolean);
}

function showProps() {
  const o = canvas.getActiveObject();
  if (!o) return;
  propsEmpty.hidden = true;
  propsBody.hidden = false;
  $("pStroke").value = rgbToHex(o.stroke) || "#222222";
  const isText = o.type === "i-text";
  const noFill = isText || o._isDimension || o._isConnector;
  if (noFill) {
    $("pFill").disabled = true;
    $("pFillNone").disabled = true;
    $("pFillNone").checked = true;
  } else {
    $("pFill").disabled = false;
    $("pFillNone").disabled = false;
    $("pFillNone").checked = !o.fill;
    $("pFill").value = rgbToHex(o.fill) || "#ffffff";
  }
  $("pWidth").value = o.strokeWidth || 0;
  $("pWidthVal").textContent = o.strokeWidth || 0;
  $("pLock").checked = !!(o._meta && o._meta.locked);
  updateSizeReadout();
}

function applyToActive(fn) {
  activeOnes().forEach((o) => {
    fn(o);
    if (o._isDimension) applyDimensionStyle(o);
    syncObj(o);
  });
  canvas.renderAll();
}

$("pStroke").addEventListener("input", (e) =>
  applyToActive((o) => {
    o.set("stroke", e.target.value);
    if (o.type === "i-text") o.set("fill", e.target.value);
  }) || scheduleCommit()
);
$("pFill").addEventListener("input", (e) =>
  applyToActive((o) => {
    if (o.type !== "i-text") o.set("fill", e.target.value);
  }) || scheduleCommit()
);
$("pFillNone").addEventListener("change", (e) =>
  applyToActive((o) => {
    if (o.type !== "i-text") o.set("fill", e.target.checked ? null : $("pFill").value);
  }) || scheduleCommit()
);
$("pWidth").addEventListener("input", (e) => {
  $("pWidthVal").textContent = e.target.value;
  applyToActive((o) => o.set("strokeWidth", parseFloat(e.target.value)));
  scheduleCommit();
});

$("pLock").addEventListener("change", (e) =>
  applyToActive((o) => {
    o._meta = o._meta || {};
    o._meta.locked = e.target.checked;
    setLocked(o, e.target.checked);
  }) || scheduleCommit()
);

$("unlockAllBtn").addEventListener("click", () => {
  canvas.getObjects().forEach((o) => {
    if (o._meta && o._meta.locked) {
      o._meta.locked = false;
      setLocked(o, false);
      syncObj(o);
    }
  });
  canvas.requestRenderAll();
});

// ---- M1: snapping (grid + other objects) --------------------------------
const SNAP_DIST = 6;  // px: how close edges/centres must be to snap

$("snapGrid").addEventListener("change", (e) => {
  state.snapGrid = e.target.checked;
  if (state.doc) {
    state.doc.snap_grid = state.snapGrid;
    api("PUT", "/api/settings", { snap_grid: state.snapGrid }).catch(console.error);
    scheduleCommit();
  }
});
$("snapObjects").addEventListener("change", (e) => {
  state.snapObjects = e.target.checked;
  if (state.doc) {
    state.doc.snap_objects = state.snapObjects;
    api("PUT", "/api/settings", { snap_objects: state.snapObjects }).catch(console.error);
    scheduleCommit();
  }
});
$("snapPage").addEventListener("change", (e) => {
  state.snapPage = e.target.checked;
  if (state.doc) {
    state.doc.snap_page = state.snapPage;
    api("PUT", "/api/settings", { snap_page: state.snapPage }).catch(console.error);
    scheduleCommit();
  }
});

// Find the nearest edge/centre alignment against other objects and/or the page.
// Returns deltas to apply, or null per axis when nothing is close.
function objectSnapDelta(o, includeObjects, includePage) {
  const b = o.getBoundingRect(true);
  const targets = [];
  if (includeObjects) {
    canvas.getObjects().forEach((other) => {
      if (other === o || other.type === "activeSelection") return;
      if (other._isConnector || other._isDimension) return;
      targets.push(other.getBoundingRect(true));
    });
  }
  if (includePage && state.doc) {
    targets.push({ left: 0, top: 0, width: state.doc.width, height: state.doc.height });
  }
  const mine = {
    x: [b.left, b.left + b.width / 2, b.left + b.width],
    y: [b.top, b.top + b.height / 2, b.top + b.height],
  };
  let bestX = null, bestY = null;
  targets.forEach((t) => {
    const tx = [t.left, t.left + t.width / 2, t.left + t.width];
    const ty = [t.top, t.top + t.height / 2, t.top + t.height];
    tx.forEach((v) => mine.x.forEach((m) => {
      const d = v - m;
      if (Math.abs(d) <= SNAP_DIST &&
          (bestX === null || Math.abs(d) < Math.abs(bestX))) bestX = d;
    }));
    ty.forEach((v) => mine.y.forEach((m) => {
      const d = v - m;
      if (Math.abs(d) <= SNAP_DIST &&
          (bestY === null || Math.abs(d) < Math.abs(bestY))) bestY = d;
    }));
  });
  return { dx: bestX, dy: bestY };
}

// Snap-guide lines: which target edges (page / other objects) does the object
// currently align with? Returned as canvas coordinates for drawing.
function collectGuides(o, includeObjects, includePage) {
  const b = o.getBoundingRect(true);
  const ex = [b.left, b.left + b.width / 2, b.left + b.width];
  const ey = [b.top, b.top + b.height / 2, b.top + b.height];
  const targets = [];
  if (includeObjects) {
    canvas.getObjects().forEach((other) => {
      if (other === o || other.type === "activeSelection") return;
      if (other._isConnector || other._isDimension) return;
      targets.push(other.getBoundingRect(true));
    });
  }
  if (includePage && state.doc) targets.push({ left: 0, top: 0, width: state.doc.width, height: state.doc.height });
  const tx = [], ty = [];
  targets.forEach((t) => {
    tx.push(t.left, t.left + t.width / 2, t.left + t.width);
    ty.push(t.top, t.top + t.height / 2, t.top + t.height);
  });
  const xs = new Set(), ys = new Set();
  ex.forEach((e) => tx.forEach((t) => {
    if (Math.abs(t - e) <= SNAP_DIST) xs.add(t);
  }));
  ey.forEach((e) => ty.forEach((t) => {
    if (Math.abs(t - e) <= SNAP_DIST) ys.add(t);
  }));
  return { xs: [...xs], ys: [...ys] };
}

canvas.on("object:moving", (e) => {
  const o = e.target;
  if (!o || o._isConnector) return;
  state.snapGuides = { xs: [], ys: [] };
  // Hold Alt to drag freely.
  if (!snapActive() || (e.e && e.e.altKey)) return;
  const snap = objectSnapDelta(o, state.snapObjects, state.snapPage);
  const useGridX = state.snapGrid && snap.dx === null;
  const useGridY = state.snapGrid && snap.dy === null;
  o.set({
    left: snap.dx === null ? (useGridX ? roundGrid(o.left) : o.left) : o.left + snap.dx,
    top: snap.dy === null ? (useGridY ? roundGrid(o.top) : o.top) : o.top + snap.dy,
  });
  o.setCoords();
  state.snapGuides = collectGuides(o, state.snapObjects, state.snapPage);
  // Keep connectors attached to any object we're dragging.
  const ids = new Set();
  if (o.type === "activeSelection") o.getObjects().forEach((c) => c.id && ids.add(c.id));
  else if (o.id) ids.add(o.id);
  if (ids.size) refreshConnectorsFor(ids);
});

// Snap each edge of a bounding box to the nearest target edge (other objects /
// page) within SNAP_DIST, else to the grid step. Returns a grid-aligned box.
function snapBox(b) {
  const step = gridStep();
  const tx = [], ty = [];
  if (state.snapObjects) {
    canvas.getObjects().forEach((other) => {
      if (other._isConnector || other._isDimension) return;
      const ob = other.getBoundingRect(true);
      tx.push(ob.left, ob.left + ob.width);
      ty.push(ob.top, ob.top + ob.height);
    });
  }
  if (state.snapPage && state.doc) { tx.push(0, state.doc.width); ty.push(0, state.doc.height); }
  const snapEdge = (edge, targets, useGrid) => {
    let best = null;
    for (const t of targets) {
      const d = t - edge;
      if (Math.abs(d) <= SNAP_DIST && (best === null || Math.abs(d) < Math.abs(best))) best = d;
    }
    if (best !== null) return edge + best;
    if (useGrid) return Math.round(edge / step) * step;
    return edge;
  };
  const left = snapEdge(b.left, tx, state.snapGrid);
  const right = snapEdge(b.left + b.width, tx, state.snapGrid);
  const top = snapEdge(b.top, ty, state.snapGrid);
  const bottom = snapEdge(b.top + b.height, ty, state.snapGrid);
  return {
    left, top,
    width: Math.max(step, right - left),
    height: Math.max(step, bottom - top),
  };
}

// Snap edges / dimensions to the grid or other objects while resizing.
canvas.on("object:scaling", (e) => {
  const o = e.target;
  if (!o || o._isConnector || !snapActive() || (e.e && e.e.altKey)) return;
  const b = o.getBoundingRect(true);
  if (b.width < 2 || b.height < 2) return;
  const { left, top, width, height } = snapBox(b);
  const set = { left, top };
  if (typeof o.width === "number" && o.width > 0) set.scaleX = width / o.width;
  if (typeof o.height === "number" && o.height > 0) set.scaleY = height / o.height;
  o.set(set);
  o.setCoords();
  const ids = new Set();
  if (o.type === "activeSelection") o.getObjects().forEach((c) => c.id && ids.add(c.id));
  else if (o.id) ids.add(o.id);
  if (ids.size) refreshConnectorsFor(ids);
});

canvas.on("mouse:up", () => {
  state.snapGuides = { xs: [], ys: [] };
  canvas.requestRenderAll();
});

// ---- M1: align & distribute ---------------------------------------------
function reselect(objs) {
  if (!objs.length) return;
  canvas.setActiveObject(
    objs.length === 1
      ? objs[0]
      : new fabric.ActiveSelection(objs, { canvas })
  );
  canvas.requestRenderAll();
  updateSizeReadout();
}

function alignSelection(mode) {
  const objs = canvas.getActiveObjects();
  if (objs.length < 2) return;
  const active = canvas.getActiveObject();
  if (active && active.type === "activeSelection") canvas.discardActiveObject();

  const boxes = objs.map((o) => ({ o, b: o.getBoundingRect(true) }));
  const left = Math.min(...boxes.map((x) => x.b.left));
  const right = Math.max(...boxes.map((x) => x.b.left + x.b.width));
  const top = Math.min(...boxes.map((x) => x.b.top));
  const bottom = Math.max(...boxes.map((x) => x.b.top + x.b.height));
  const cx = (left + right) / 2;
  const cy = (top + bottom) / 2;

  boxes.forEach(({ o, b }) => {
    let dx = 0, dy = 0;
    if (mode === "left") dx = left - b.left;
    else if (mode === "centerH") dx = cx - (b.left + b.width / 2);
    else if (mode === "right") dx = right - (b.left + b.width);
    else if (mode === "top") dy = top - b.top;
    else if (mode === "centerV") dy = cy - (b.top + b.height / 2);
    else if (mode === "bottom") dy = bottom - (b.top + b.height);
    if (dx || dy) o.set({ left: o.left + dx, top: o.top + dy });
    o.setCoords();
    syncObj(o);
  });
  refreshConnectors();
  reselect(objs);
  scheduleCommit();
}

// Equal-gap distribution along one axis; first object stays put.
function distributeSelection(axis) {
  const objs = canvas.getActiveObjects();
  if (objs.length < 3) return;
  const active = canvas.getActiveObject();
  if (active && active.type === "activeSelection") canvas.discardActiveObject();

  const items = objs.map((o) => ({ o, b: o.getBoundingRect(true) }));
  const pos = axis === "x" ? "left" : "top";
  const size = axis === "x" ? "width" : "height";
  items.sort((a, b) => a.b[pos] - b.b[pos]);

  const first = items[0].b[pos];
  const lastEdge = items[items.length - 1].b[pos] + items[items.length - 1].b[size];
  const total = items.reduce((sum, i) => sum + i.b[size], 0);
  const gap = (lastEdge - first - total) / (items.length - 1);

  let cursor = first;
  items.forEach(({ o, b }) => {
    const delta = cursor - b[pos];
    if (delta && axis === "x") o.set({ left: o.left + delta });
    else if (delta) o.set({ top: o.top + delta });
    cursor += b[size] + gap;
    o.setCoords();
    syncObj(o);
  });
  refreshConnectors();
  reselect(objs);
  scheduleCommit();
}

$("alignSelect").addEventListener("change", (e) => {
  const mode = e.target.value;
  e.target.value = "";
  if (!mode) return;
  if (mode === "distH") distributeSelection("x");
  else if (mode === "distV") distributeSelection("y");
  else alignSelection(mode);
});

// Arrow-key nudge: grid step, Shift = 5×, Alt = 1 px.
window.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  const map = {
    ArrowLeft: [-1, 0], ArrowRight: [1, 0],
    ArrowUp: [0, -1], ArrowDown: [0, 1],
  };
  const dir = map[e.key];
  if (!dir) return;
  const objs = activeOnes();
  if (!objs.length) return;
  const active = canvas.getActiveObject();
  if (active && active.isEditing) return;
  e.preventDefault();
  const step = e.shiftKey ? gridStep() * 5 : e.altKey ? 1 : gridStep();
  const dx = dir[0] * step, dy = dir[1] * step;
  if (active && active.type === "activeSelection") {
    active.set({ left: active.left + dx, top: active.top + dy });
    active.setCoords();
    active.getObjects().forEach(syncObj);
  } else {
    objs.forEach((o) => {
      o.set({ left: o.left + dx, top: o.top + dy });
      o.setCoords();
      syncObj(o);
    });
  }
  canvas.requestRenderAll();
  updateSizeReadout();
  refreshConnectors();
  scheduleCommit();
});

// ---- modify / remove sync -----------------------------------------------
canvas.on("object:modified", (e) => {
  if (state.suppressSync || !e.target) return;
  state.snapGuides = { xs: [], ys: [] };
  if (e.target.type === "activeSelection") e.target.getObjects().forEach(syncObj);
  else syncObj(e.target);
  const ids = new Set();
  if (e.target.type === "activeSelection") e.target.getObjects().forEach((c) => c.id && ids.add(c.id));
  else if (e.target.id) ids.add(e.target.id);
  if (ids.size) refreshConnectorsFor(ids);
  updateSizeReadout();
  canvas.requestRenderAll();
  scheduleCommit();
});

// Delete a single object, cascading any connectors that reference it.
export function eraseTarget(o) {
  if (!o) return;
  const id = o.id;
  if (id) {
    const connIds = canvas.getObjects()
      .filter((c) => c._isConnector &&
              (c._connectorRef.from.id === id || c._connectorRef.to.id === id))
      .map((c) => c.id);
    connIds.forEach((cid) => {
      const c = canvas.getObjects().find((x) => x.id === cid);
      if (c) canvas.remove(c);
      api("DELETE", `/api/objects/${cid}`, {}).catch(console.error);
    });
    canvas.remove(o);
    api("DELETE", `/api/objects/${id}`, {}).catch(console.error);
  } else {
    canvas.remove(o);
  }
}

// Delete several objects at once (used by the eraser drag and the Delete key).
export function eraseObjects(objs) {
  if (!objs || !objs.length) return;
  state.suppressSync = true;
  objs.forEach(eraseTarget);
  state.suppressSync = false;
  canvas.discardActiveObject();
  canvas.renderAll();
  scheduleCommit();
}

function deleteSelected() {
  eraseObjects(activeOnes());
}
$("deleteBtn").addEventListener("click", deleteSelected);
window.addEventListener("keydown", (e) => {
  const a = canvas.getActiveObject();
  if ((e.key === "Delete" || e.key === "Backspace") && a && !a.isEditing) {
    e.preventDefault();
    deleteSelected();
  }
});

// ---- M-group: group / ungroup (a real Fabric group object) -----------------
// Cascade-delete connectors attached to any of the given ids before a group is
// formed, so we don't leave dangling references to members nested in the group.
function deleteConnectorsFor(ids) {
  const idSet = new Set(ids.filter(Boolean));
  if (!idSet.size) return;
  canvas.getObjects()
    .filter((c) => c._isConnector &&
            (idSet.has(c._connectorRef.from.id) || idSet.has(c._connectorRef.to.id)))
    .forEach(eraseTarget);
}

export function groupSelection() {
  const sel = canvas.getActiveObject();
  if (!sel || sel.type !== "activeSelection") {
    alert("Select two or more objects, then choose Group.");
    return;
  }
  // Connectors and dimensions are structural — exclude them from a group.
  const kids = sel.getObjects().filter((o) => !o._isConnector && !o._isDimension);
  if (kids.length < 2) {
    alert("Group needs at least two regular shapes (connectors and dimensions can't be grouped).");
    return;
  }
  // Drop connectors referencing any grouped member — they can't survive the
  // members being nested inside the group.
  deleteConnectorsFor(kids.map((k) => k.id));
  const grp = sel.toGroup();
  grp.id = genId();
  grp._meta = { layer: state.activeLayerId };
  canvas.setActiveObject(grp);
  canvas.requestRenderAll();
  // Persist: the group replaces its former members on the server.
  api("POST", "/api/objects", toCanonical(grp)).catch(console.error);
  kids.forEach((k) => {
    if (k.id) api("DELETE", `/api/objects/${k.id}`, {}).catch(console.error);
  });
  scheduleCommit();
}

export function ungroupSelection() {
  const o = canvas.getActiveObject();
  if (!o || o.type !== "group") {
    alert("Select a grouped object, then choose Ungroup.");
    return;
  }
  const kids = o.getObjects().slice();
  // Convert the Fabric group back into a top-level ActiveSelection of members.
  const sel = o.toActiveSelection();
  if (sel) canvas.setActiveObject(sel);
  canvas.requestRenderAll();
  // Persist: the group is replaced by its (now top-level) members.
  if (o.id) api("DELETE", `/api/objects/${o.id}`, {}).catch(console.error);
  kids.forEach((k) => api("POST", "/api/objects", toCanonical(k)).catch(console.error));
  scheduleCommit();
  return sel;
}

// Flip the Dimension tool on/off (used by the Tools menu).
export function toggleDimension() { setDimensionMode(!state.dimensionMode); }

// ---- M1: ruler + alignment-guideline overlay ----------------------------
function niceStep(raw) {
  const pow = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const n = (raw || 1) / pow;
  const m = n >= 5 ? 5 : n >= 2 ? 2 : 1;
  return m * pow;
}

function fmtRuler(v) {
  return v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2);
}

// Draw a top + left ruler (ticked in real units), alignment guides while
// dragging, and a small scale bar. Painted on the lower canvas each render so it
// sits above the artwork but stays hidden from exports.
function drawRulers(ctx) {
  if (!state.doc || !state.doc.show_rulers) return;
  const ppu = pxPerUnit();
  const units = state.doc.units || "mm";
  ctx.save();
  ctx.strokeStyle = "#c7ccd4";
  ctx.fillStyle = "#8a93a2";
  ctx.lineWidth = 1;
  ctx.font = "9px system-ui, sans-serif";
  ctx.textBaseline = "top";
  for (let x = 0; x <= canvas.width; x += gridStep()) {
    const major = x % (gridStep() * 5) === 0;
    ctx.beginPath();
    ctx.moveTo(x + 0.5, 0);
    ctx.lineTo(x + 0.5, major ? 10 : 5);
    ctx.stroke();
    if (major && x > 0) ctx.fillText(`${fmtRuler(x / ppu)} ${units}`, x + 3, 11);
  }
  for (let y = 0; y <= canvas.height; y += gridStep()) {
    const major = y % (gridStep() * 5) === 0;
    ctx.beginPath();
    ctx.moveTo(0, y + 0.5);
    ctx.lineTo(major ? 10 : 5, y + 0.5);
    ctx.stroke();
    if (major && y > 0) {
      ctx.save();
      ctx.translate(2, y + 3);
      ctx.rotate(-Math.PI / 2);
      ctx.fillText(`${fmtRuler(y / ppu)} ${units}`, 0, 0);
      ctx.restore();
    }
  }
  // scale bar (bottom-left)
  const nice = niceStep(120 / ppu);
  const barPx = nice * ppu;
  const bx = 12, by = canvas.height - 22;
  ctx.fillStyle = "#6b7280";
  ctx.fillRect(bx, by, barPx, 5);
  ctx.strokeStyle = "#6b7280";
  ctx.strokeRect(bx, by, barPx, 5);
  ctx.fillText(`${nice} ${units}`, bx, by - 11);
  ctx.restore();
}

function drawGuides(ctx) {
  if (!state.snapGuides) return;
  ctx.save();
  ctx.strokeStyle = "#d9480f";
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 3]);
  state.snapGuides.xs.forEach((x) => {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, canvas.height);
    ctx.stroke();
  });
  state.snapGuides.ys.forEach((y) => {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(canvas.width, y);
    ctx.stroke();
  });
  ctx.restore();
}

canvas.on("after:render", () => {
  const ctx = canvas.getContext();
  if (!ctx) return;
  drawRulers(ctx);
  drawGuides(ctx);
});

export {
  setDimensionMode, clearDimPreview, snappedPoint, gridStep, snapActive,
  connectorCanonical, showProps, activeOnes, applyToActive, deleteSelected,
  alignSelection, distributeSelection, drawRulers, drawGuides,
};
