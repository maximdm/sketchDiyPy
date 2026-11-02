/* M5: persistence + undo/redo. A snapshot is the full canvas serialized back
   through the canonical schema (the same shape the backend eats). We keep a
   linear history for undo/redo and mirror the latest snapshot into localStorage
   so a page reload restores work even if the server's in-memory doc was lost to a
   restart. Also the named-project file actions. */

import { canvas, state, $ } from "./state.js";
import { api, loadDoc, toCanonical } from "./sync.js";

const LS_KEY = "sketchdiy:doc:v1";
const history = [];
// The live undo pointer lives on `state.histIndex` (shared mutable state in
// state.js) so other modules that touch history stay in sync.

function serializeCanvas() {
  const objects = canvas.getObjects()
    .filter((o) => o.id && !o.excludeFromExport)
    .map(toCanonical);
  return {
    units: state.doc.units,
    px_per_unit: state.doc.px_per_unit,
    width: state.doc.width,
    height: state.doc.height,
    background: state.doc.background,
    grid_color: state.doc.grid_color,
    grid_size: state.doc.grid_size,
    export_grid: state.doc.export_grid,
    show_grid: state.doc.show_grid,
    show_rulers: state.doc.show_rulers,
    theme: state.doc.theme,
    recolor_on_theme: state.doc.recolor_on_theme,
    snap_grid: state.doc.snap_grid,
    snap_objects: state.doc.snap_objects,
    snap_page: state.doc.snap_page,
    layers: state.doc.layers,
    objects,
  };
}

function persistLocal(snap) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(snap));
  } catch (e) {
    /* quota / private mode — autosave is best-effort */
  }
}

function pushSnapshot(snap) {
  // drop any redo branch
  if (state.histIndex < history.length - 1) history.length = state.histIndex + 1;
  history.push(snap);
  state.histIndex = history.length - 1;
  if (history.length > 120) {
    history.shift();
    state.histIndex--;
  }
}

function recordChange() {
  const snap = serializeCanvas();
  persistLocal(snap);
  pushSnapshot(snap);
}

// Debounced so a drag/slider gesture becomes a single undo step.
function scheduleCommit() {
  if (state.commitTimer) clearTimeout(state.commitTimer);
  state.commitTimer = setTimeout(recordChange, 350);
}

// Load a snapshot into the canvas AND resync the server (so exports match).
async function applyDoc(data) {
  loadDoc(data);
  try {
    await api("POST", "/api/doc", data);
  } catch (e) {
    console.error("doc sync failed", e);
  }
}

function undo() {
  if (state.histIndex <= 0) return;
  state.histIndex--;
  applyDoc(history[state.histIndex]);
  persistLocal(history[state.histIndex]);
}

function redo() {
  if (state.histIndex >= history.length - 1) return;
  state.histIndex++;
  applyDoc(history[state.histIndex]);
  persistLocal(history[state.histIndex]);
}

window.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
    e.preventDefault();
    if (e.shiftKey) redo();
    else undo();
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") {
    e.preventDefault();
    redo();
  }
});

// ---- M5: named project files (server-side ./projects) -------------------
function fmtSavedAt(ts) {
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch (e) {
    return "";
  }
}

async function refreshProjectList() {
  const host = $("projList");
  try {
    const list = await api("GET", "/api/projects", null);
    if (!list.length) {
      host.innerHTML = '<div class="proj-empty">No saved projects yet.</div>';
      return;
    }
    host.innerHTML = "";
    list.forEach((p) => {
      const row = document.createElement("div");
      row.className = "proj-item";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = p.name;
      name.title = fmtSavedAt(p.saved_at);
      const load = document.createElement("button");
      load.className = "btn btn-xs";
      load.textContent = "Open";
      load.addEventListener("click", () => openProject(p.name));
      const del = document.createElement("button");
      del.className = "btn btn-xs";
      del.textContent = "✕";
      del.title = "Delete";
      del.addEventListener("click", () => deleteProject(p.name));
      row.append(name, load, del);
      host.appendChild(row);
    });
  } catch (e) {
    host.innerHTML = '<div class="proj-empty">Could not list projects.</div>';
  }
}

async function openProject(name) {
  try {
    const data = await api("POST", "/api/project/load", { name });
    await applyDoc(data);
    persistLocal(serializeCanvas());
    pushSnapshot(serializeCanvas());
  } catch (e) {
    alert("Open failed: " + e.message);
  }
}

async function deleteProject(name) {
  if (!confirm(`Delete project "${name}"?`)) return;
  try {
    await api("DELETE", `/api/project/${encodeURIComponent(name)}`, {});
    await refreshProjectList();
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
}

$("projSave").addEventListener("click", async () => {
  const name = ($("projName").value || "").trim();
  if (!name) {
    alert("Enter a project name first.");
    return;
  }
  try {
    await api("POST", "/api/project/save", { name });
    $("projName").value = "";
    await refreshProjectList();
  } catch (e) {
    alert("Save failed: " + e.message);
  }
});

// ---- boot ---------------------------------------------------------------
async function boot() {
  let data = null;
  const saved = localStorage.getItem(LS_KEY);
  if (saved) {
    try {
      data = JSON.parse(saved);
    } catch (e) {
      data = null;
    }
  }
  if (!data) {
    try {
      data = await api("GET", "/api/doc", null);
    } catch (e) {
      alert("Failed to load: " + e.message);
    }
  }
  if (data) {
    state.doc = data;
    loadDoc(data);
    // Resync the server from local storage so exports match the restored canvas.
    if (saved) {
      try {
        await api("POST", "/api/doc", data);
      } catch (e) {
        console.error(e);
      }
    }
  }
  // Baseline snapshot for undo; everything after is a new step.
  pushSnapshot(serializeCanvas());
  persistLocal(serializeCanvas());
  await refreshProjectList();
}

export {
  serializeCanvas, persistLocal, pushSnapshot, recordChange, scheduleCommit,
  applyDoc, undo, redo, refreshProjectList, openProject, deleteProject, boot,
};
