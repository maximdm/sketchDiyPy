/* Plan workspace: a manual shopping / buy list, ordered assembly steps, a Parts
   cut list (on-demand from the drawing), and free-form Notes — all persisted
   per project (like layers). There is deliberately NO auto-BOM derived from the
   drawing for buy/steps — the user maintains what to buy and how to build,
   because consumables (screws, glue, finish) and build order are things the
   geometry can't tell you.

   Data shape (lives on `state.doc.plan`, round-trips through /api/settings):
     { currency: "$",
       buy:   [{ name, qty, unit, price, link }],
       steps: [{ text, done }],
       parts: [{ id, kind, w, h, units, layerId, qty, members, name,
                 material, unit_cost, note }],
       notes: [{ text, kind }] }   // kind: "manual" | "sketch" (sketch notes are
                                   // auto-added when a freehand stroke is drawn) */

import { state, $ } from "./state.js";
import { api } from "./sync.js";
import { scheduleCommit, serializeCanvas } from "./persistence.js";

// Guarantee a usable plan object on whatever doc we load.
function ensurePlan() {
  if (!state.doc) return;
  if (!state.doc.plan || typeof state.doc.plan !== "object") {
    state.doc.plan = { currency: "$", buy: [], steps: [], parts: [], notes: [] };
  }
  if (!Array.isArray(state.doc.plan.buy)) state.doc.plan.buy = [];
  if (!Array.isArray(state.doc.plan.steps)) state.doc.plan.steps = [];
  if (!Array.isArray(state.doc.plan.parts)) state.doc.plan.parts = [];
  if (!Array.isArray(state.doc.plan.notes)) state.doc.plan.notes = [];
  if (!state.doc.plan.currency) state.doc.plan.currency = "$";
}

// Local re-render + debounced persistence (localStorage, history AND server).
let planPushTimer = null;
function syncPlan() {
  ensurePlan();
  scheduleCommit(); // debounced: localStorage + one undo step
  if (planPushTimer) clearTimeout(planPushTimer);
  planPushTimer = setTimeout(() => {
    api("PUT", "/api/settings", { plan: state.doc.plan }).catch(console.error);
  }, 400);
}

// ---- small helpers -------------------------------------------------------
function money(n) {
  ensurePlan();
  const c = state.doc.plan.currency || "$";
  const v = Number(n) || 0;
  const s = v.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return c + s;
}

function buyTotal() {
  ensurePlan();
  return state.doc.plan.buy.reduce(
    (sum, it) => sum + (Number(it.qty) || 0) * (Number(it.price) || 0),
    0
  );
}

// Update the live cost displays (summary stat + list footer) without
// rebuilding the inputs (which would steal typing focus mid-keystroke).
function updateTotals() {
  const c = money(buyTotal());
  const cost = $("planStatCost");
  if (cost) cost.textContent = c;
  const tot = $("buyTotal");
  if (tot) tot.textContent = c;
}

function field(type, value, placeholder) {
  const el = document.createElement("input");
  el.type = type;
  el.className = "field";
  el.value = value != null ? value : "";
  if (placeholder) el.placeholder = placeholder;
  return el;
}

function btn(label, cls, title) {
  const b = document.createElement("button");
  b.className = cls;
  b.textContent = label;
  if (title) b.title = title;
  return b;
}

// ---- render --------------------------------------------------------------
function renderPlan() {
  if (!state.doc) return;
  ensurePlan();
  renderSummary();
  renderParts();
  renderBuy();
  renderSteps();
  renderNotes();
}

function renderSummary() {
  ensurePlan();
  const p = state.doc.plan;
  const cur = $("planCurrency");
  if (cur) cur.value = p.currency || "$";
  const items = $("planStatItems");
  if (items) items.textContent = String(p.buy.length);
  const steps = $("planStatSteps");
  if (steps) steps.textContent = String(p.steps.length);
  updateTotals();
}

function renderBuy() {
  const host = $("buyList");
  if (!host) return;
  ensurePlan();
  host.innerHTML = "";
  state.doc.plan.buy.forEach((it, idx) => {
    const row = document.createElement("div");
    row.className = "buy-row";

    const name = field("text", it.name, "Item (e.g. #8 wood screw)");
    name.addEventListener("input", () => { it.name = name.value; syncPlan(); });

    const qty = field("number", it.qty, "Qty");
    qty.min = "0"; qty.step = "any";
    qty.addEventListener("input", () => {
      it.qty = qty.value;
      line.textContent = money((Number(it.qty) || 0) * (Number(it.price) || 0));
      updateTotals();
      syncPlan();
    });

    const unit = field("text", it.unit, "unit (pcs/m/sheet)");
    unit.addEventListener("input", () => { it.unit = unit.value; syncPlan(); });

    const price = field("number", it.price, "price each");
    price.min = "0"; price.step = "any";
    price.addEventListener("input", () => {
      it.price = price.value;
      line.textContent = money((Number(it.qty) || 0) * (Number(it.price) || 0));
      updateTotals();
      syncPlan();
    });

    const note = field("text", it.link, "store / link / note");
    note.addEventListener("input", () => { it.link = note.value; syncPlan(); });

    const line = document.createElement("span");
    line.className = "buy-line-total";
    line.textContent = money((Number(it.qty) || 0) * (Number(it.price) || 0));

    const rm = btn("✕", "xbtn buy-rm", "Remove item");
    rm.addEventListener("click", () => {
      state.doc.plan.buy.splice(idx, 1);
      renderPlan();
      syncPlan();
    });

    row.append(name, qty, unit, price, note, line, rm);
    host.appendChild(row);
  });
  updateTotals();
}

function renderSteps() {
  const host = $("stepList");
  if (!host) return;
  ensurePlan();
  host.innerHTML = "";
  state.doc.plan.steps.forEach((s, idx) => {
    const row = document.createElement("div");
    row.className = "step-row" + (s.done ? " done" : "");
    row.draggable = true;
    row.dataset.idx = String(idx);

    const no = document.createElement("span");
    no.className = "step-no";
    no.textContent = String(idx + 1);

    const done = document.createElement("input");
    done.type = "checkbox";
    done.checked = !!s.done;
    done.title = "Mark step done";
    done.addEventListener("change", () => {
      s.done = done.checked;
      row.classList.toggle("done", s.done);
      syncPlan();
    });

    const ta = document.createElement("textarea");
    ta.className = "field";
    ta.value = s.text || "";
    ta.placeholder = "Describe this step…";
    ta.addEventListener("input", () => { s.text = ta.value; syncPlan(); });

    const up = btn("↑", "xbtn", "Move up");
    up.disabled = idx === 0;
    up.addEventListener("click", () => moveStep(idx, -1));

    const down = btn("↓", "xbtn", "Move down");
    down.disabled = idx === state.doc.plan.steps.length - 1;
    down.addEventListener("click", () => moveStep(idx, 1));

    const rm = btn("✕", "xbtn", "Remove step");
    rm.addEventListener("click", () => {
      state.doc.plan.steps.splice(idx, 1);
      renderPlan();
      syncPlan();
    });

    row.append(no, done, ta, up, down, rm);

    // drag-to-reorder
    row.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", String(idx));
      e.dataTransfer.effectAllowed = "move";
      row.classList.add("dragging");
    });
    row.addEventListener("dragend", () => row.classList.remove("dragging"));
    row.addEventListener("dragover", (e) => { e.preventDefault(); row.classList.add("drop-target"); });
    row.addEventListener("dragleave", () => row.classList.remove("drop-target"));
    row.addEventListener("drop", (e) => {
      e.preventDefault();
      row.classList.remove("drop-target");
      const from = parseInt(e.dataTransfer.getData("text/plain"), 10);
      if (Number.isNaN(from) || from === idx) return;
      reorderStep(from, idx);
    });

    host.appendChild(row);
  });
}

function renderNotes() {
  const host = $("noteList");
  if (!host) return;
  ensurePlan();
  // Keep the "Project:" scope label in sync (mirrors persistence.applyProjectScope).
  const sc = $("noteProject");
  if (sc) sc.textContent = state.projectName || "Unsaved project";
  host.innerHTML = "";
  state.doc.plan.notes.forEach((n, idx) => {
    const row = document.createElement("div");
    row.className = "note-row" + (n.kind === "sketch" ? " note-sketch" : "");

    if (n.kind === "sketch") {
      const badge = document.createElement("span");
      badge.className = "note-badge";
      badge.textContent = "✎ sketch";
      row.appendChild(badge);
    }

    const ta = document.createElement("textarea");
    ta.className = "field";
    ta.value = n.text || "";
    ta.placeholder = "Note…";
    ta.addEventListener("input", () => { n.text = ta.value; syncPlan(); });

    const rm = btn("✕", "xbtn", "Remove note");
    rm.addEventListener("click", () => {
      state.doc.plan.notes.splice(idx, 1);
      renderNotes();
      syncPlan();
    });

    row.append(ta, rm);
    host.appendChild(row);
  });
}

// Auto-append a note (used by the freehand tool) or let the user add one.
function addNote(text, kind) {
  ensurePlan();
  if (!Array.isArray(state.doc.plan.notes)) state.doc.plan.notes = [];
  state.doc.plan.notes.push({ text: text || "", kind: kind || "manual" });
  renderNotes();
  syncPlan();
}

function moveStep(idx, dir) {
  const list = state.doc.plan.steps;
  const j = idx + dir;
  if (j < 0 || j >= list.length) return;
  const [m] = list.splice(idx, 1);
  list.splice(j, 0, m);
  renderSteps();
  syncPlan();
}

function reorderStep(from, to) {
  const list = state.doc.plan.steps;
  if (from < 0 || to < 0 || from >= list.length || to >= list.length) return;
  const [m] = list.splice(from, 1);
  list.splice(to, 0, m);
  renderSteps();
  syncPlan();
}

// ---- Parts (on-demand cut list derived from the drawing) -----------------
// "Regenerate from drawing" pulls rect/circle/line geometry into editable
// parts, grouped by the layer they live on. It is always explicit — we never
// auto-wipe the pricing the user has entered; material/unit-cost/name/note are
// preserved across regenerations when a part's geometry signature still exists.
const PART_MATERIALS = [
  "", "Plywood", "MDF", "Acrylic", "Steel",
  "Aluminium", "Pine", "Oak", "Other",
];

function layerName(id) {
  const l = (state.doc.layers || []).find((x) => x.id === id);
  return l ? l.name : "Layer";
}

// Real-world size of a shape in document units, or null if it has no clean
// measurable geometry (paths, text, dimensions, connectors are skipped).
function partDims(o) {
  const g = o.geometry || {};
  const sx = Math.abs(o.scaleX) || 1;
  const sy = Math.abs(o.scaleY) || 1;
  if (o.type === "rect") return { kind: "rect", w: g.w * sx, h: g.h * sy };
  if (o.type === "circle") {
    const d = 2 * g.r * Math.max(sx, sy);
    return { kind: "circle", w: d, h: d };
  }
  if (o.type === "line") {
    const len = Math.hypot(g.x2 - g.x1, g.y2 - g.y1);
    return { kind: "line", w: len, h: 0 };
  }
  return null;
}

// Build the fresh derivation from the live canvas (via the canonical snapshot).
function deriveParts() {
  const snap = serializeCanvas();
  const ppu = snap.px_per_unit || 1;
  const units = snap.units || "mm";
  const groups = {}; // signature -> accumulated part
  // Flatten grouped members so a shape that lives inside a group still shows up
  // in the cut list (the group itself has no raw geometry to measure).
  const flat = [];
  (snap.objects || []).forEach((o) => {
    if (o.type === "group" && o.geometry && Array.isArray(o.geometry.children)) {
      o.geometry.children.forEach((c) => flat.push(c));
    } else {
      flat.push(o);
    }
  });
  flat.forEach((o) => {
    if (o.excludeFromExport) return;
    const d = partDims(o);
    if (!d) return;
    const wU = d.w / ppu;
    const hU = d.h / ppu;
    // Round to 1 decimal for both display and the merge signature so
    // near-identical cut pieces collapse into one line item.
    const wr = Math.round(wU * 10) / 10;
    const hr = Math.round(hU * 10) / 10;
    const layerId = (o.meta && o.meta.layer) || (snap.layers[0] && snap.layers[0].id);
    const sig = `${d.kind}|${wr}x${hr}|${layerId}`;
    if (!groups[sig]) {
      groups[sig] = {
        id: "part-" + Math.random().toString(36).slice(2, 10),
        kind: d.kind, w: wr, h: hr, units,
        layerId, qty: 0, members: [],
        name: "", material: "", unit_cost: 0, note: "",
      };
    }
    groups[sig].qty += 1;
    // Remember which drawing object(s) this part came from so a later
    // Regenerate can match the part by identity (in-place size updates) even
    // after a shape is resized, rather than dropping it as "new".
    groups[sig].members.push(o.id);
  });
  // Default display name per kind.
  Object.values(groups).forEach((p) => {
    if (!p.name) {
      if (p.kind === "rect") p.name = `Panel ${p.w}×${p.h} ${p.units}`;
      else if (p.kind === "circle") p.name = `Disc ⌀${p.w} ${p.units}`;
      else p.name = `Bar ${p.w} ${p.units}`;
    }
  });
  return Object.values(groups);
}

// Merge a fresh derivation with the user's existing costing edits, matching
// parts by *identity* (their source drawing object(s)) so a resize updates the
// part in place instead of dropping it as "new". Signature (kind|size|layer) is
// tried first; if a part's size changed, we fall back to a shared source-object
// id so the same physical part keeps its material/price.
function regenerateParts() {
  ensurePlan();
  const fresh = deriveParts();
  const prev = state.doc.plan.parts || [];
  const used = new Set();
  const sigOf = (p) => `${p.kind}|${p.w}x${p.h}|${p.layerId}`;
  const oldBySig = new Map();
  prev.forEach((p) => { if (!used.has(p.id)) oldBySig.set(sigOf(p), p); });

  const keep = (fp, op) => ({
    id: op.id,
    kind: fp.kind, w: fp.w, h: fp.h, units: fp.units, layerId: fp.layerId,
    qty: fp.qty, members: fp.members,
    // Preserve the user's labels/pricing; fall back to the derived default name.
    name: op.name || fp.name,
    material: op.material || "",
    unit_cost: op.unit_cost || 0,
    note: op.note || "",
  });

  const result = [];
  fresh.forEach((fp) => {
    // 1) Exact signature match (size unchanged) — keep everything as-is.
    const sid = oldBySig.get(sigOf(fp));
    if (sid && !used.has(sid)) {
      result.push(keep(fp, sid));
      used.add(sid);
      return;
    }
    // 2) Fallback: a part that shares at least one source object (a resized
    //    shape). Update its size/qty in place; keep material/price.
    const cand = prev.find(
      (op) => !used.has(op.id) &&
        (op.members || []).some((mid) => fp.members.includes(mid))
    );
    if (cand) {
      result.push(keep(fp, cand));
      used.add(cand.id);
      return;
    }
    // 3) Brand-new part (no prior match).
    result.push(fp);
  });
  // Parts whose source objects were deleted are simply dropped (not in `result`).

  state.doc.plan.parts = result;
  renderParts();
  syncPlan();
  const st = $("partStatus");
  if (st) st.textContent = `${result.length} part${result.length === 1 ? "" : "s"} from ${state.doc.layers.length} layer(s)`;
}

function partTotal() {
  ensurePlan();
  return state.doc.plan.parts.reduce(
    (s, p) => s + (Number(p.qty) || 0) * (Number(p.unit_cost) || 0),
    0
  );
}

function updatePartTotals() {
  const c = money(partTotal());
  const tot = $("partTotal");
  if (tot) tot.textContent = c;
}

function renderParts() {
  const host = $("partList");
  if (!host) return;
  ensurePlan();
  host.innerHTML = "";
  state.doc.plan.parts.forEach((p, idx) => {
    const row = document.createElement("div");
    row.className = "part-row";

    const name = field("text", p.name, "Part name");
    name.addEventListener("input", () => { p.name = name.value; syncPlan(); });

    const size = document.createElement("span");
    size.className = "part-size";
    size.textContent = p.kind === "line"
      ? `${p.w} ${p.units}`
      : p.kind === "circle"
        ? `⌀ ${p.w} ${p.units}`
        : `${p.w} × ${p.h} ${p.units}`;

    const qty = field("number", p.qty, "Qty");
    qty.min = "0"; qty.step = "any";
    qty.addEventListener("input", () => {
      p.qty = qty.value;
      line.textContent = money((Number(p.qty) || 0) * (Number(p.unit_cost) || 0));
      updatePartTotals();
      syncPlan();
    });

    const mat = document.createElement("select");
    mat.className = "field";
    PART_MATERIALS.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m; opt.textContent = m || "— material —";
      mat.appendChild(opt);
    });
    mat.value = p.material || "";
    mat.addEventListener("change", () => { p.material = mat.value; syncPlan(); });

    const cost = field("number", p.unit_cost, "unit $");
    cost.min = "0"; cost.step = "any";
    cost.addEventListener("input", () => {
      p.unit_cost = cost.value;
      line.textContent = money((Number(p.qty) || 0) * (Number(p.unit_cost) || 0));
      updatePartTotals();
      syncPlan();
    });

    const line = document.createElement("span");
    line.className = "buy-line-total";
    line.textContent = money((Number(p.qty) || 0) * (Number(p.unit_cost) || 0));

    const layer = document.createElement("span");
    layer.className = "part-layer";
    layer.textContent = layerName(p.layerId);
    layer.title = "Source layer";

    const rm = btn("✕", "xbtn buy-rm", "Remove part");
    rm.addEventListener("click", () => {
      state.doc.plan.parts.splice(idx, 1);
      renderParts();
      syncPlan();
    });

    row.append(name, size, qty, mat, cost, line, layer, rm);
    host.appendChild(row);
  });
  updatePartTotals();
}

// ---- init ----------------------------------------------------------------
function initPlan() {
  const add = $("buyAdd");
  if (add) add.addEventListener("click", () => {
    ensurePlan();
    state.doc.plan.buy.push({ name: "", qty: 1, unit: "", price: 0, link: "" });
    renderPlan();
    syncPlan();
  });

  const addStep = $("stepAdd");
  if (addStep) addStep.addEventListener("click", () => {
    ensurePlan();
    state.doc.plan.steps.push({ text: "", done: false });
    renderPlan();
    syncPlan();
  });

  const cur = $("planCurrency");
  if (cur) cur.addEventListener("change", () => {
    ensurePlan();
    state.doc.plan.currency = cur.value;
    renderPlan();
    syncPlan();
  });

  const regen = $("partRegen");
  if (regen) regen.addEventListener("click", regenerateParts);

  const addNoteBtn = $("noteAdd");
  if (addNoteBtn) addNoteBtn.addEventListener("click", () => {
    ensurePlan();
    state.doc.plan.notes.push({ text: "", kind: "manual" });
    renderNotes();
    syncPlan();
  });
}

// Re-render the plan whenever the Plan tab is opened, and lazily on load.
document.querySelectorAll(".ws-tab").forEach((tab) => {
  if (tab.dataset.view === "plan") {
    tab.addEventListener("click", renderPlan);
  }
});

initPlan();
// Safe no-op until the doc is ready (renderPlan guards on state.doc).
if (state.doc) renderPlan();

export { renderPlan, ensurePlan, addNote, renderNotes };
