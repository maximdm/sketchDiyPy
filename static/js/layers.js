/* Layers (M5). A document owns an ordered list of layers; each object points at
   one via `meta.layer`. Higher layers paint above lower ones, and a hidden/locked
   layer hides/locks its members (in the canvas and in every export). */

import { canvas, state, $, genId } from "./state.js";
import { api, setLocked, syncObj } from "./sync.js";
import { scheduleCommit, applyDoc, serializeCanvas } from "./persistence.js";
import { showProps } from "./tools.js";

// Default colour tags for new layers (Blender-style organisational swatch,
// shown in the outliner only — it never recolours the artwork itself).
const LAYER_COLORS = [
  "#1f6feb", "#d9480f", "#2f9e44", "#9c36b5",
  "#e8590c", "#0ca678", "#e64980", "#f08c00",
];

// Session-only "solo" target: when set, only that layer is shown (like
// Blender's collection solo). Not persisted — it's a view aid.
let soloId = null;

// Rebuild the in-memory document from the live canvas so operations that
// later rebuild from `state.doc.objects` (reorder / delete / move-to-layer)
// never drop shapes that were drawn but only existed on the canvas so far.
function refreshFromCanvas() {
  state.doc = serializeCanvas();
  ensureLayers();
}

function layerById(id) {
  if (!state.doc || !state.doc.layers) return null;
  return state.doc.layers.find((l) => l.id === id) || null;
}

// Guarantee a usable layer list + a valid active layer on whatever doc we load,
// and backfill each object's layer membership (console/template/older objects
// may not carry one yet) so rendering and export stay consistent.
function ensureLayers() {
  if (!state.doc.layers || !state.doc.layers.length) {
    state.doc.layers = [{ id: "layer-base", name: "Layer 1", visible: true, locked: false }];
  }
  if (!state.activeLayerId || !state.doc.layers.some((l) => l.id === state.activeLayerId)) {
    state.activeLayerId = state.doc.layers[0].id;
  }
  const base = state.doc.layers[0].id;
  const ids = new Set(state.doc.layers.map((l) => l.id));
  state.doc.layers.forEach((l, i) => {
    if (!l.color) l.color = LAYER_COLORS[i % LAYER_COLORS.length];
  });
  (state.doc.objects || []).forEach((o) => {
    const m = (o.meta = o.meta || {});
    if (!m.layer || !ids.has(m.layer)) m.layer = base;
  });
}

// Push the current layer list to the server (kept in sync with settings) and
// record an undo step.
function syncLayers() {
  api("PUT", "/api/settings", { layers: state.doc.layers }).catch(console.error);
  scheduleCommit();
}

// Re-apply every layer's visibility/lock to the live Fabric objects without a
// full rebuild (used when toggling a layer on/off, soloing, or moving an object).
function applyLayerStates() {
  canvas.getObjects().forEach((o) => {
    const layer = layerById(o._meta && o._meta.layer);
    // Solo overrides every layer's own visibility: only the soloed layer shows.
    const hidden = soloId
      ? (layer && layer.id !== soloId)
      : (layer && layer.visible === false);
    const locked = (o._meta && o._meta.locked) || (layer && layer.locked);
    o.visible = !hidden;
    if (hidden) {
      o.selectable = false;
      o.evented = false;
    } else {
      setLocked(o, !!locked);
    }
    o.setCoords();
  });
  const active = canvas.getActiveObject();
  if (active && (active.visible === false || active.selectable === false)) {
    canvas.discardActiveObject();
  }
  canvas.requestRenderAll();
}

function renderLayers() {
  const host = $("layerList");
  if (!host) return;
  host.innerHTML = "";
  state.doc.layers.forEach((l, idx) => {
    const row = document.createElement("div");
    row.className = "layer-row"
      + (l.id === state.activeLayerId ? " active" : "")
      + (soloId === l.id ? " solo-on" : "")
      + (soloId && soloId !== l.id ? " solo-dim" : "");
    row.draggable = true;
    row.dataset.layerId = l.id;

    // Colour tag (organisational only — does not recolour artwork).
    const color = document.createElement("input");
    color.type = "color";
    color.className = "layer-color";
    color.value = l.color || "#1f6feb";
    color.title = "Layer colour tag";
    color.addEventListener("click", (e) => e.stopPropagation());
    color.addEventListener("input", () => setLayerColor(l.id, color.value));

    const eye = document.createElement("button");
    eye.className = "xbtn";
    eye.textContent = l.visible === false ? "🚫" : "👁";
    eye.title = "Toggle visibility (also hides in exports)";
    eye.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleLayerVisible(l.id);
    });

    const lock = document.createElement("button");
    lock.className = "xbtn";
    lock.textContent = l.locked ? "🔒" : "🔓";
    lock.title = "Toggle lock";
    lock.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleLayerLocked(l.id);
    });

    const solo = document.createElement("button");
    solo.className = "xbtn" + (soloId === l.id ? " solo-on" : "");
    solo.textContent = soloId === l.id ? "◎" : "○";
    solo.title = "Solo — show only this layer";
    solo.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleSolo(l.id);
    });

    const name = document.createElement("input");
    name.type = "text";
    name.className = "field layer-name";
    name.value = l.name;
    name.title = "Layer name (click row to make active)";
    name.addEventListener("click", (e) => {
      e.stopPropagation();
      selectLayer(l.id);
    });
    name.addEventListener("change", () => renameLayer(l.id, name.value));

    const up = document.createElement("button");
    up.className = "xbtn";
    up.textContent = "↑";
    up.title = "Move layer up (towards front)";
    up.disabled = idx === 0;
    up.addEventListener("click", (e) => {
      e.stopPropagation();
      moveLayer(l.id, -1);
    });

    const down = document.createElement("button");
    down.className = "xbtn";
    down.textContent = "↓";
    down.title = "Move layer down (towards back)";
    down.disabled = idx === state.doc.layers.length - 1;
    down.addEventListener("click", (e) => {
      e.stopPropagation();
      moveLayer(l.id, 1);
    });

    row.addEventListener("click", () => selectLayer(l.id));

    // Drag-and-drop reordering (Blender-style outliner).
    row.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", l.id);
      e.dataTransfer.effectAllowed = "move";
      row.classList.add("dragging");
    });
    row.addEventListener("dragend", () => row.classList.remove("dragging"));
    row.addEventListener("dragover", (e) => e.preventDefault());
    row.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const src = e.dataTransfer.getData("text/plain");
      if (src) reorderLayerTo(src, l.id);
    });

    row.append(color, eye, lock, solo, name, up, down);
    host.appendChild(row);
  });

  // Keep the "move selection to layer" target dropdown in sync.
  const sel = $("layerTarget");
  if (sel) {
    const cur = sel.value;
    sel.innerHTML = "";
    state.doc.layers.forEach((l) => {
      const opt = document.createElement("option");
      opt.value = l.id;
      opt.textContent = l.name;
      sel.appendChild(opt);
    });
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
    else sel.value = state.activeLayerId;
  }
}

function toggleLayerVisible(id) {
  const l = layerById(id);
  if (!l) return;
  l.visible = l.visible === false ? true : false;
  applyLayerStates();
  syncLayers();
  renderLayers();
}

function toggleLayerLocked(id) {
  const l = layerById(id);
  if (!l) return;
  l.locked = !l.locked;
  applyLayerStates();
  syncLayers();
  renderLayers();
}

function renameLayer(id, value) {
  const l = layerById(id);
  if (!l) return;
  l.name = value || l.name;
  syncLayers();
  renderLayers();
}

function selectLayer(id) {
  state.activeLayerId = id;
  renderLayers();
  const objs = canvas.getObjects().filter(
    (o) => (o._meta && o._meta.layer) === id && o.visible !== false
  );
  if (objs.length) {
    canvas.discardActiveObject();
    canvas.setActiveObject(
      objs.length === 1
        ? objs[0]
        : new fabric.ActiveSelection(objs, { canvas })
    );
    canvas.requestRenderAll();
    showProps();
  }
}

function addLayer() {
  const l = {
    id: "layer-" + genId().slice(0, 8),
    name: "Layer " + (state.doc.layers.length + 1),
    visible: true,
    locked: false,
    color: LAYER_COLORS[state.doc.layers.length % LAYER_COLORS.length],
  };
  state.doc.layers.push(l);
  state.activeLayerId = l.id;
  renderLayers();
  syncLayers();
}

function deleteLayer(id) {
  // Rebuild the doc from the canvas first, so shapes drawn since the last full
  // load are not lost when we later rebuild the canvas from state.doc.objects.
  refreshFromCanvas();
  if (state.doc.layers.length <= 1) {
    $("layerStatus").textContent = "can't delete the last layer";
    return;
  }
  const target = state.doc.layers.find((l) => l.id !== id).id;
  // Reassign the doomed layer's objects to another layer, then drop it.
  state.doc.objects.forEach((o) => {
    if ((o.meta && o.meta.layer) === id) {
      o.meta = o.meta || {};
      o.meta.layer = target;
    }
  });
  state.doc.layers = state.doc.layers.filter((l) => l.id !== id);
  if (state.activeLayerId === id) state.activeLayerId = state.doc.layers[0].id;
  if (soloId === id) soloId = null;
  // Rebuild the canvas (object memberships changed) and resync the server.
  applyDoc(state.doc);
  scheduleCommit();
  renderLayers();
}

// Reorder a layer (and its members' z-order) then rebuild so stacking matches.
function moveLayer(id, dir) {
  // Refresh from the canvas so newly-drawn shapes survive the rebuild below.
  refreshFromCanvas();
  const i = state.doc.layers.findIndex((l) => l.id === id);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= state.doc.layers.length) return;
  const tmp = state.doc.layers[i];
  state.doc.layers[i] = state.doc.layers[j];
  state.doc.layers[j] = tmp;
  applyLayerOrder();
  renderLayers();
}

// Sort objects to match the current layer order and rebuild the canvas.
function applyLayerOrder() {
  const order = {};
  state.doc.layers.forEach((l, k) => (order[l.id] = k));
  state.doc.objects.sort(
    (a, b) =>
      (order[(a.meta && a.meta.layer)] ?? 0) -
      (order[(b.meta && b.meta.layer)] ?? 0)
  );
  applyDoc(state.doc);
  scheduleCommit();
}

// Drag-and-drop reorder: move `srcId` to the position of `targetId`.
function reorderLayerTo(srcId, targetId) {
  if (srcId === targetId) return;
  refreshFromCanvas();
  const list = state.doc.layers;
  const si = list.findIndex((l) => l.id === srcId);
  const ti = list.findIndex((l) => l.id === targetId);
  if (si < 0 || ti < 0) return;
  const [moved] = list.splice(si, 1);
  const insertAt = list.findIndex((l) => l.id === targetId);
  list.splice(insertAt, 0, moved);
  applyLayerOrder();
  renderLayers();
}

// Move the current canvas selection onto a chosen layer (organise existing art).
function sendSelectionToLayer(targetId) {
  const objs = canvas.getActiveObjects();
  if (!objs.length) {
    $("layerStatus").textContent = "select objects first";
    return;
  }
  const layer = layerById(targetId);
  if (!layer) return;
  objs.forEach((o) => {
    o._meta = o._meta || {};
    o._meta.layer = targetId;
  });
  applyLayerStates();              // re-apply hide/lock for the new membership
  objs.forEach((o) => syncObj(o)); // persist each object's new layer on the server
  scheduleCommit();
  renderLayers();
  $("layerStatus").textContent = `moved ${objs.length} → ${layer.name}`;
}

function setLayerColor(id, hex) {
  const l = layerById(id);
  if (!l) return;
  l.color = hex;
  syncLayers();
}

// Toggle solo: show only this layer (session-only view aid).
function toggleSolo(id) {
  soloId = soloId === id ? null : id;
  applyLayerStates();
  renderLayers();
  $("layerStatus").textContent = soloId
    ? `solo: ${(layerById(soloId) || {}).name || ""}`
    : "";
}

// ---- M5: layers panel listeners -----------------------------------------
$("layerAdd").addEventListener("click", addLayer);
$("layerDelete").addEventListener("click", () => deleteLayer(state.activeLayerId));
$("layerSend").addEventListener("click", () =>
  sendSelectionToLayer($("layerTarget").value));

export {
  layerById, ensureLayers, syncLayers, applyLayerStates, renderLayers,
  toggleLayerVisible, toggleLayerLocked, renameLayer, selectLayer,
  addLayer, deleteLayer, moveLayer,
};
