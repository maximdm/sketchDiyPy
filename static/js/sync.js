/* Canonical <-> Fabric sync: serialize a Fabric object back to the canonical
   schema, rebuild a Fabric object from a canonical dict, and load a whole
   document into the canvas. Also the low-level `api()` fetch helper and the
   per-object `syncObj()` PUT. */

import { canvas, state, palette, THEMES } from "./state.js";
import { layerById, ensureLayers, renderLayers } from "./layers.js";
import {
  pxPerUnit, updateReadout, syncPageControls, applyBoard,
} from "./page.js";

// ---- color helper -------------------------------------------------------
function rgbToHex(c) {
  if (!c) return null;
  if (c[0] === "#") return c;
  const m = c.match(/rgba?\(([^)]+)\)/);
  if (!m) return "#222222";
  const p = m[1].split(",").map((s) => parseFloat(s));
  const h = (x) => ("0" + Math.round(x).toString(16)).slice(-2);
  return "#" + h(p[0]) + h(p[1]) + h(p[2]);
}

// ---- canonical <-> Fabric ----------------------------------------------
// Decompose an object's *world* matrix. Unlike raw left/top, this stays correct
// while the object is inside an ActiveSelection (multi-selection), where
// children store group-relative coordinates.
function toCanonical(o) {
  const t = o.type;
  const s = o.stroke || "#222222";
  const fill = o.fill || null;
  const sw = o.strokeWidth || 0;
  const dec = fabric.util.qrDecompose(o.calcTransformMatrix());
  // Rects and circles are mirror-symmetric, so flips can be dropped: keep the
  // scales positive and let the decomposed angle carry the orientation. That
  // keeps reloads stable (negative scales confuse Fabric's origin maths).
  const flat = t === "rect" || t === "circle" || t === "i-text";
  const base = {
    id: o.id,
    type: t === "i-text" ? "text" : t,
    x: dec.translateX,
    y: dec.translateY,
    rotation: dec.angle || 0,
    scaleX: (flat ? Math.abs(dec.scaleX) : dec.scaleX) || 1,
    scaleY: (flat ? Math.abs(dec.scaleY) : dec.scaleY) || 1,
    style: { fill, stroke: s, strokeWidth: sw, fontSize: o.fontSize || 24 },
    meta: o._meta || {},
  };
  if (t === "rect") {
    base.geometry = { w: o.width, h: o.height };
  } else if (t === "circle") {
    base.geometry = { r: o.radius };
  } else if (t === "line") {
    // Bake position/scale/rotation into absolute endpoints (like paths), so
    // the canonical line is always identity-transformed. Fabric stores line
    // geometry only as x1/y1/x2/y2 plus width/height; calcLinePoints() gives
    // the endpoints in local space, centred on the object's centre. M3: the
    // canonical `x,y` is now the endpoints' midpoint (a uniform origin) instead
    // of 0.
    const lp = o.calcLinePoints();
    const m = o.calcTransformMatrix();
    const p0 = fabric.util.transformPoint({ x: lp.x1, y: lp.y1 }, m);
    const p1 = fabric.util.transformPoint({ x: lp.x2, y: lp.y2 }, m);
    base.x = (p0.x + p1.x) / 2; base.y = (p0.y + p1.y) / 2;
    base.rotation = 0;
    base.scaleX = 1; base.scaleY = 1;
    base.geometry = { x1: p0.x, y1: p0.y, x2: p1.x, y2: p1.y };
  } else if (t === "path") {
    // Bake the object's live transform into the path data so moves, scales and
    // rotations survive a reload. Paths stay absolute (identity transform). M3:
    // `x,y` is now the bbox centre (a uniform origin) instead of 0.
    const d = pathToAbsoluteD(o);
    const c = pathBBoxCenter(d) || { x: 0, y: 0 };
    base.x = c.x; base.y = c.y;
    base.rotation = 0;
    base.scaleX = 1; base.scaleY = 1;
    base.geometry = { d };
  } else if (t === "i-text") {
    base.geometry = { text: o.text, fontSize: o.fontSize || 24 };
  } else if (o._isDimension) {
    // Dimensions keep absolute endpoints like lines. Apply the group's live
    // transform relative to the transform it was built with, so moving a
    // dimension updates its measured points.
    const b = o._dimBase;
    const delta = fabric.util.multiplyTransformMatrices(
      o.calcTransformMatrix(), fabric.util.invertTransform(b.matrix)
    );
    const p0 = fabric.util.transformPoint({ x: b.x1, y: b.y1 }, delta);
    const p1 = fabric.util.transformPoint({ x: b.x2, y: b.y2 }, delta);
    base.type = "dimension";
    base.x = (p0.x + p1.x) / 2; base.y = (p0.y + p1.y) / 2;  // M3: uniform origin
    base.rotation = 0;
    base.scaleX = 1; base.scaleY = 1;
    base.geometry = {
      x1: p0.x, y1: p0.y, x2: p1.x, y2: p1.y, offset: b.offset,
    };
    base.style = { ...o._dimStyle };
  } else if (o._isConnector) {
    // Connectors keep their semantic references (not baked endpoints), so they
    // follow the referenced objects after a reload/export.
    const r = o._connectorRef;
    base.type = "connector";
    base.x = 0; base.y = 0;
    base.rotation = 0;
    base.scaleX = 1; base.scaleY = 1;
    base.geometry = { from: { ...r.from }, to: { ...r.to } };
  } else if (t === "group") {
    // A real Fabric group: its children already carry their own world transforms
    // (calcTransformMatrix folds in the group transform), so flatten them and
    // store the group itself as identity. Reload rebuilds the children at world
    // coords and wraps them in a new group — positionally stable, and the member
    // ids survive so parts/connectors that reference them keep working.
    base.type = "group";
    base.x = 0; base.y = 0;
    base.rotation = 0;
    base.scaleX = 1; base.scaleY = 1;
    base.geometry = { children: o.getObjects().map((c) => toCanonical(c)) };
  }
  return base;
}

// Flatten a Fabric.Path's transform (position/scale/rotation) into its `d`
// string, expressed in canvas-absolute coordinates.
function pathToAbsoluteD(o) {
  // Paths created directly (e.g. the freehand tool) may not carry `_d`; fall
  // back to the parsed command list so they still serialize a usable `d`.
  const d = o._d || (o.path ? fabric.util.joinPath(o.path) : "");
  if (!d) return "";
  try {
    const cmds = o.path || fabric.util.parsePath(d);
    const abs = fabric.util.transformPath(
      cmds, o.calcTransformMatrix(), o.pathOffset
    );
    return fabric.util.joinPath(abs);
  } catch (err) {
    console.warn("path bake failed, keeping original d", err);
    return d;
  }
}

// Centre of a path's coordinate bounding box. M3: used as the path's uniform
// `x,y` origin so every shape carries a meaningful origin (rotation pivot /
// selection origin) instead of `x,y == 0`. Mirrors core/pathgeom.path_center.
function pathBBoxCenter(d) {
  if (!d) return null;
  const nums = (d.match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi) || []).map(Number);
  if (nums.length < 2) return null;
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (let i = 0; i + 1 < nums.length; i += 2) {
    const x = nums[i], y = nums[i + 1];
    if (x < minx) minx = x;
    if (y < miny) miny = y;
    if (x > maxx) maxx = x;
    if (y > maxy) maxy = y;
  }
  if (!isFinite(minx) || !isFinite(miny)) return null;
  return { x: (minx + maxx) / 2, y: (miny + maxy) / 2 };
}

// Build a line that renders exactly between two absolute points. Placing it by
// its centre avoids Fabric's half-stroke offset, which would otherwise make
// lines drift by strokeWidth/2 on every save/reload.
function makeLine(x1, y1, x2, y2, opts) {
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.hypot(dx, dy);
  return new fabric.Line([-len / 2, 0, len / 2, 0], {
    ...opts,
    originX: "center", originY: "center",
    left: (x1 + x2) / 2, top: (y1 + y2) / 2,
    angle: (Math.atan2(dy, dx) * 180) / Math.PI,
  });
}

// Convert a canonical centre + rotation into Fabric's left/top origin. Fabric
// includes the stroke in an object's dimensions, so the centre of a scaled,
// rotated rect/circle sits half a (scaled) stroke away from its top-left
// origin. Rotating that offset keeps reloads pixel-stable.
function originFromCenter(cx, cy, u, v, angle) {
  const rad = ((angle || 0) * Math.PI) / 180;
  const c = Math.cos(rad), s = Math.sin(rad);
  return { left: cx - (u * c - v * s), top: cy - (u * s + v * c) };
}

// ---- dimensions ---------------------------------------------------------
// Same layout as core/export.py: measured span A→B, an offset dimension line,
// short extension lines, 45° ticks, and a rotated label in document units.
function dimGeometry(g) {
  const x1 = +g.x1, y1 = +g.y1, x2 = +g.x2, y2 = +g.y2;
  const off = g.offset == null ? 26 : +g.offset;
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.hypot(dx, dy);
  if (len < 1e-9) return null;
  const ux = dx / len, uy = dy / len;
  const nx = -uy, ny = ux;
  const ax = x1 + nx * off, ay = y1 + ny * off;
  const bx = x2 + nx * off, by = y2 + ny * off;
  const ext = off >= 0 ? 4 : -4;
  const tick = 5 / Math.SQRT2;
  const tx = (ux + nx) * tick, ty = (uy + ny) * tick;
  const side = off >= 0 ? 10 : -10;
  const lx = (ax + bx) / 2 + nx * side;
  const ly = (ay + by) / 2 + ny * side;
  let angle = (Math.atan2(dy, dx) * 180) / Math.PI;
  if (angle > 90) angle -= 180;
  else if (angle < -90) angle += 180;
  return {
    len, x1, y1, x2, y2, ax, ay, bx, by,
    eax: ax + nx * ext, eay: ay + ny * ext,
    ebx: bx + nx * ext, eby: by + ny * ext,
    tx, ty, lx, ly, angle,
  };
}

function dimensionLabel(lengthPx, meta) {
  if (meta && meta.label) return meta.label;
  const units = (state.doc && state.doc.units) || "mm";
  const value = lengthPx / (pxPerUnit() || 1);
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${units}`;
}

function makeDimension(g, style, meta) {
  const d = dimGeometry(g);
  if (!d) return null;
  const st = {
    fill: null, stroke: "#1f6feb", strokeWidth: 1.5, fontSize: 14,
    ...(style || {}),
  };
  const lineOpts = {
    stroke: st.stroke, strokeWidth: st.strokeWidth,
    selectable: false, evented: false,
  };
  const children = [
    makeLine(d.x1, d.y1, d.eax, d.eay, lineOpts),
    makeLine(d.x2, d.y2, d.ebx, d.eby, lineOpts),
    makeLine(d.ax, d.ay, d.bx, d.by, lineOpts),
    makeLine(d.ax - d.tx, d.ay - d.ty, d.ax + d.tx, d.ay + d.ty, lineOpts),
    makeLine(d.bx - d.tx, d.by - d.ty, d.bx + d.tx, d.by + d.ty, lineOpts),
  ];
  const label = dimensionLabel(d.len, meta);
  const labelObj = new fabric.Text(label, {
    left: d.lx, top: d.ly,
    fontSize: st.fontSize, fill: st.stroke,
    originX: "center", originY: "center", angle: d.angle,
    selectable: false, evented: false,
  });
  labelObj._dimRole = "label";
  children.push(labelObj);

  const grp = new fabric.Group(children, {
    lockScalingX: true, lockScalingY: true, lockRotation: true,
    hasControls: false, hasRotatingPoint: false, hoverCursor: "move",
    stroke: st.stroke, strokeWidth: st.strokeWidth, fontSize: st.fontSize,
  });
  grp._isDimension = true;
  grp._dimStyle = { ...st };
  grp._dimBase = {
    x1: +g.x1, y1: +g.y1, x2: +g.x2, y2: +g.y2,
    offset: g.offset == null ? 26 : +g.offset,
  };
  grp._dimBase.matrix = grp.calcTransformMatrix();
  grp._meta = meta || {};
  return grp;
}

// Push group-level style edits down to the child lines/label.
function applyDimensionStyle(o) {
  if (!o._isDimension) return;
  const st = o._dimStyle || {};
  const stroke = rgbToHex(o.stroke) || st.stroke || "#1f6feb";
  const width = o.strokeWidth == null ? (st.strokeWidth || 1.5) : o.strokeWidth;
  const fontSize = o.fontSize || st.fontSize || 14;
  o._dimStyle = { ...st, stroke, strokeWidth: width, fontSize };
  o.getObjects().forEach((c) => {
    if (c._dimRole === "label") c.set({ fill: stroke, fontSize });
    else c.set({ stroke, strokeWidth: width });
  });
}

// Recompute labels after a unit change.
function refreshDimensionLabels() {
  canvas.getObjects().forEach((o) => {
    if (!o._isDimension || !o._dimBase) return;
    const d = dimGeometry(o._dimBase);
    if (!d) return;
    const label = o.getObjects().find((c) => c._dimRole === "label");
    if (label) label.set("text", dimensionLabel(d.len, o._meta));
  });
}

// ---- connectors (anchor-based, follow their referenced objects) -----------
// A connector stores `geometry.from`/`to` = { id, anchor } (anchor in
// center/left/right/top/bottom). It renders as a selectable, non-draggable line
// whose endpoints are recomputed from the referenced objects' bounding geometry.

function anchorPoint(o, anchor) {
  // getCenterPoint() folds the stroke into its dimensions, but the canonical
  // model (and the backend exporter) treat the object centre as stroke-excluded
  // geometry. Subtract half the stroke so anchors line up with the exported SVG.
  const c = o.getCenterPoint();
  const sw = o.strokeWidth || 0;
  const cx = c.x - sw / 2;
  const cy = c.y - sw / 2;
  const hw = (o.width * Math.abs(o.scaleX || 1)) / 2;
  const hh = (o.height * Math.abs(o.scaleY || 1)) / 2;
  const rad = ((o.angle || 0) * Math.PI) / 180;
  const cos = Math.cos(rad), sin = Math.sin(rad);
  let lx = 0, ly = 0;
  if (anchor === "left") lx = -hw;
  else if (anchor === "right") lx = hw;
  else if (anchor === "top") ly = -hh;
  else if (anchor === "bottom") ly = hh;
  return { x: cx + lx * cos - ly * sin, y: cy + lx * sin + ly * cos };
}

function resolveAnchor(ref) {
  if (!ref || !ref.id) return null;
  const o = canvas.getObjects().find((x) => x.id === ref.id);
  if (!o) return null;
  return anchorPoint(o, ref.anchor || "center");
}

// Pick the anchor of `o` closest to point `p` — used by the Connect button.
function nearestAnchor(o, p) {
  const anchors = ["center", "left", "right", "top", "bottom"];
  let best = "center", bd = Infinity;
  for (const a of anchors) {
    const q = anchorPoint(o, a);
    const d = Math.hypot(q.x - p.x, q.y - p.y);
    if (d < bd) { bd = d; best = a; }
  }
  return best;
}

function buildConnector(o) {
  const g = o.geometry || {};
  const p0 = resolveAnchor(g.from);
  const p1 = resolveAnchor(g.to);
  if (!p0 || !p1) return null;
  const line = makeLine(p0.x, p0.y, p1.x, p1.y, {
    stroke: (o.style && o.style.stroke) || "#495057",
    strokeWidth: (o.style && o.style.strokeWidth) || 2,
    hasControls: false,
    hasRotatingPoint: false,
    lockMovementX: true,
    lockMovementY: true,
    hoverCursor: "pointer",
  });
  line._isConnector = true;
  line._connectorRef = { from: { ...g.from }, to: { ...g.to } };
  line.id = o.id;
  line._meta = o.meta || {};
  return line;
}

// Recompute a connector's endpoints from its referenced objects.
function setLineEndpoints(line) {
  const g = line._connectorRef;
  const p0 = resolveAnchor(g.from);
  const p1 = resolveAnchor(g.to);
  if (!p0 || !p1) return;
  const dx = p1.x - p0.x, dy = p1.y - p0.y;
  const len = Math.hypot(dx, dy);
  line.set({
    x1: -len / 2, y1: 0, x2: len / 2, y2: 0,
    left: (p0.x + p1.x) / 2,
    top: (p0.y + p1.y) / 2,
    angle: (Math.atan2(dy, dx) * 180) / Math.PI,
  });
  line.setCoords();
}

// Refresh all connectors whose referenced id is in `ids` (during/after a move).
function refreshConnectorsFor(ids) {
  if (!ids || !ids.size) return;
  canvas.getObjects().forEach((o) => {
    if (!o._isConnector) return;
    if (ids.has(o._connectorRef.from.id) || ids.has(o._connectorRef.to.id)) {
      setLineEndpoints(o);
    }
  });
}

// Rebuild any pending connectors once their targets exist; also a no-op refresh
// of existing ones. Called after a full document load.
function refreshConnectors() {
  if (state.pendingConnectors.length) {
    const still = [];
    state.pendingConnectors.forEach((c) => {
      const line = buildConnector(c);
      if (line) canvas.add(line);
      else still.push(c);
    });
    state.pendingConnectors = still;
  }
  canvas.getObjects().forEach((o) => {
    if (o._isConnector) setLineEndpoints(o);
  });
  canvas.requestRenderAll();
}

function buildFabric(o) {
  const s = o.style || {};
  const stroke = s.stroke || "#222222";
  const fill = s.fill || null;
  const sw = s.strokeWidth || 0;
  const g = o.geometry || {};
  let obj;
  if (o.type === "rect") {
    const sx = Math.abs(o.scaleX) || 1, sy = Math.abs(o.scaleY) || 1;
    const half = originFromCenter(
      o.x, o.y,
      ((g.w + sw) * sx) / 2,
      ((g.h + sw) * sy) / 2,
      o.rotation
    );
    obj = new fabric.Rect({
      left: half.left, top: half.top,
      width: g.w, height: g.h,
      fill, stroke, strokeWidth: sw,
      scaleX: sx, scaleY: sy, angle: o.rotation,
    });
  } else if (o.type === "circle") {
    const sx = Math.abs(o.scaleX) || 1, sy = Math.abs(o.scaleY) || 1;
    const half = originFromCenter(
      o.x, o.y,
      ((2 * g.r + sw) * sx) / 2,
      ((2 * g.r + sw) * sy) / 2,
      o.rotation
    );
    obj = new fabric.Circle({
      left: half.left, top: half.top,
      radius: g.r, fill, stroke, strokeWidth: sw,
      scaleX: sx, scaleY: sy, angle: o.rotation,
    });
  } else if (o.type === "line") {
    obj = makeLine(g.x1, g.y1, g.x2, g.y2, { stroke, strokeWidth: sw });
  } else if (o.type === "dimension") {
    obj = makeDimension(g, s, o.meta || {});
  } else if (o.type === "connector") {
    obj = buildConnector(o);
  } else if (o.type === "path") {
    obj = new fabric.Path(g.d, { fill, stroke, strokeWidth: sw });
    obj._d = g.d;
  } else if (o.type === "text") {
    obj = new fabric.IText(g.text || "", {
      left: o.x, top: o.y, fontSize: g.fontSize || 24,
      fill: stroke, originX: "center", originY: "center",
      scaleX: Math.abs(o.scaleX) || 1, scaleY: Math.abs(o.scaleY) || 1,
      angle: o.rotation || 0,
    });
  } else if (o.type === "group") {
    // Rebuild the (world-coordinate) children, then wrap them in a group. The
    // children's canonicals already encode their absolute placement.
    const kids = (g.children || []).map(buildFabric).filter(Boolean);
    obj = kids.length ? new fabric.Group(kids, { canvas }) : null;
  }
  if (obj) {
    obj.id = o.id;
    obj._meta = o.meta || {}; // preserve meta across edits (traced, labels, ...)
    // Layer state: a hidden layer hides its members; a locked layer (or a
    // per-object lock) makes them non-selectable. This keeps the canvas in sync
    // with the layer panel and the exporters, which skip hidden layers.
    const layer = layerById(o.meta && o.meta.layer);
    const hidden = layer && layer.visible === false;
    const locked = (o.meta && o.meta.locked) || (layer && layer.locked);
    if (hidden) {
      obj.visible = false;
      obj.selectable = false;
      obj.evented = false;
    }
    if (locked) setLocked(obj, true);
  }
  return obj;
}

// Locked objects can be rendered/exported but not selected, moved, or snapped to.
function setLocked(o, locked) {
  o.set({ selectable: !locked, evented: !locked });
  o.hoverCursor = locked ? "default" : "move";
}

// ---- server sync --------------------------------------------------------
async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

function syncObj(o) {
  if (!o.id) return;
  api("PUT", `/api/objects/${o.id}`, toCanonical(o)).catch(console.error);
}

async function loadDoc(data) {
  state.doc = data;
  ensureLayers();
  state.suppressSync = true;
  canvas.clear();
  state.pendingConnectors = [];
  canvas.setDimensions({ width: data.width, height: data.height });
  applyBoard();
  const objs = data.objects || [];
  // Add non-connectors first, then connectors so their referenced targets exist.
  const nonConns = objs.filter((o) => o.type !== "connector");
  const conns = objs.filter((o) => o.type === "connector");
  nonConns.forEach((o) => {
    const f = buildFabric(o);
    if (f) canvas.add(f);
  });
  conns.forEach((o) => {
    const f = buildConnector(o);
    if (f) canvas.add(f);
    else state.pendingConnectors.push(o);
  });
  canvas.renderAll();
  state.suppressSync = false;
  refreshConnectors();
  refreshDimensionLabels();
  canvas.requestRenderAll();
  updateReadout();
  if (THEMES[state.doc.theme]) Object.assign(palette, THEMES[state.doc.theme].palette);
  syncPageControls();
  renderLayers();
}

export {
  rgbToHex, toCanonical, buildFabric, buildConnector, refreshConnectors,
  refreshConnectorsFor, setLineEndpoints, anchorPoint, nearestAnchor,
  applyDimensionStyle, makeLine, makeDimension, api, syncObj, loadDoc,
  refreshDimensionLabels, setLocked,
};
