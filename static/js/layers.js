/* Layers (M5). A document owns an ordered list of layers; each object points at
   one via `meta.layer`. Higher layers paint above lower ones, and a hidden/locked
   layer hides/locks its members (in the canvas and in every export). */

import { canvas, state, $, genId } from "./state.js";
import { api, setLocked } from "./sync.js";
import { scheduleCommit, applyDoc } from "./persistence.js";
import { showProps } from "./tools.js";

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
// full rebuild (used when toggling a layer on/off).
function applyLayerStates() {
  canvas.getObjects().forEach((o) => {
    const layer = layerById(o._meta && o._meta.layer);
    const hidden = layer && layer.visible === false;
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
    row.className = "layer-row" + (l.id === state.activeLayerId ? " active" : "");

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

    const name = document.createElement("input");
    name.type = "text";
    name.className = "field layer-name";
    name.value = l.name;
    name.title = "Layer name";
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
    row.append(eye, lock, name, up, down);
    host.appendChild(row);
  });
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
  };
  state.doc.layers.push(l);
  state.activeLayerId = l.id;
  renderLayers();
  syncLayers();
}

function deleteLayer(id) {
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
  // Rebuild the canvas (object memberships changed) and resync the server.
  applyDoc(state.doc);
  scheduleCommit();
  renderLayers();
}

// Reorder a layer (and its members' z-order) then rebuild so stacking matches.
function moveLayer(id, dir) {
  const i = state.doc.layers.findIndex((l) => l.id === id);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= state.doc.layers.length) return;
  const tmp = state.doc.layers[i];
  state.doc.layers[i] = state.doc.layers[j];
  state.doc.layers[j] = tmp;
  // Sort objects by their layer's position; ties keep their existing order.
  const order = {};
  state.doc.layers.forEach((l, k) => (order[l.id] = k));
  state.doc.objects.sort(
    (a, b) =>
      (order[(a.meta && a.meta.layer)] ?? 0) -
      (order[(b.meta && b.meta.layer)] ?? 0)
  );
  applyDoc(state.doc);
  scheduleCommit();
  renderLayers();
}

// ---- M5: layers panel listeners -----------------------------------------
$("layerAdd").addEventListener("click", addLayer);
$("layerDelete").addEventListener("click", () => deleteLayer(state.activeLayerId));

export {
  layerById, ensureLayers, syncLayers, applyLayerStates, renderLayers,
  toggleLayerVisible, toggleLayerLocked, renameLayer, selectLayer,
  addLayer, deleteLayer, moveLayer,
};
