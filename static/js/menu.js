/* Top menu bar (Shapes / Materials / Tools / Edit). The menus are pure
   navigational chrome layered on top of the existing toolbar buttons: every
   action reuses the same handlers the toolbar already wires, so behaviour stays
   identical whether you click the toolbar or the menu. The menu lives in the
   always-visible <header>, so it (unlike #toolbar2d) is present in every
   workspace tab.

   - Shapes    -> addShape() / toggleFreehand() (same as the toolbar)
   - Materials -> apply a colour preset to the selected object(s)
   - Tools     -> Eraser / Group / Ungroup / Dimension / Connect / Delete
   - Edit      -> Undo / Redo / Clear all
*/

import { addShape, toggleFreehand, toggleEraser } from "./page.js";
import {
  groupSelection, ungroupSelection, deleteSelected, toggleDimension,
  applyToActive,
} from "./tools.js";
import { undo, redo } from "./persistence.js";

// Material colour presets applied to the selected shape(s). These double as a
// quick "paint" action; the deeper Parts-list material taxonomy lives in
// plan.js (PART_MATERIALS).
const MATERIALS = {
  wood:      { stroke: "#8B5A2B", fill: "#C19A6B" },
  steel:     { stroke: "#3A3F44", fill: "#9AA3AB" },
  aluminium: { stroke: "#5B6470", fill: "#D6DBE0" },
  acrylic:   { stroke: "#1F6FEB", fill: "#BFD7FF" },
  glass:     { stroke: "#5FA8D3", fill: "#D6ECF7" },
  pine:      { stroke: "#B98B4E", fill: "#E4C9A0" },
  oak:       { stroke: "#7A4F23", fill: "#CDA36B" },
  black:     { stroke: "#222222", fill: "#111111" },
};

function applyMaterial(key) {
  const m = MATERIALS[key];
  if (!m) return;
  applyToActive((o) => {
    o.set({ stroke: m.stroke });
    if (o.type !== "i-text") o.set({ fill: m.fill });
  });
}

function closeMenus(bar) {
  bar.querySelectorAll(".menu.open").forEach((m) => {
    m.classList.remove("open");
    const t = m.querySelector(".menu-title");
    if (t) t.setAttribute("aria-expanded", "false");
  });
}

function initMenus() {
  const bar = document.querySelector(".menubar");
  if (!bar) return;

  // Open/close dropdowns (only one open at a time); close on outside click.
  bar.querySelectorAll(".menu").forEach((menu) => {
    const title = menu.querySelector(".menu-title");
    title.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = !menu.classList.contains("open");
      closeMenus(bar);
      if (willOpen) {
        menu.classList.add("open");
        title.setAttribute("aria-expanded", "true");
      }
    });
  });
  document.addEventListener("click", () => closeMenus(bar));

  // Wire each action button.
  bar.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const a = btn.dataset.action;
      try {
        if (a.startsWith("shape-")) {
          const kind = a.slice("shape-".length);
          if (kind === "free") toggleFreehand();
          else addShape(kind);
        } else if (a.startsWith("material-")) {
          applyMaterial(a.slice("material-".length));
        } else if (a === "tool-eraser") {
          toggleEraser();
        } else if (a === "tool-group") {
          groupSelection();
        } else if (a === "tool-ungroup") {
          ungroupSelection();
        } else if (a === "tool-dimension") {
          toggleDimension();
        } else if (a === "tool-connect") {
          const c = document.querySelector("#connectBtn");
          if (c) c.click();
        } else if (a === "tool-delete") {
          deleteSelected();
        } else if (a === "edit-undo") {
          undo();
        } else if (a === "edit-redo") {
          redo();
        } else if (a === "edit-clear") {
          const c = document.querySelector("#clearBtn");
          if (c) c.click();
        }
      } catch (err) {
        console.error("menu action failed:", a, err);
      }
      closeMenus(bar);
    });
  });
}

// Module side-effect: wire the menu once the DOM (deferred module load) is ready.
initMenus();
