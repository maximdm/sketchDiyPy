/* Workspace switching (Blender-like "2D Design" / "3D Model" tabs) and the
   wiring for the 3D context panel: view presets (Top/Front/Right/Perspective),
   the Transform rotation sliders, and shading / material colour.

   The 3D viewer itself lives in threed.js; this module just drives it via the
   functions it exports (show3D, setViewPreset, captureSelection,
   setModelRotation, setShading, setMaterialColor) so the two stay decoupled. */

import { canvas, $ } from "./state.js";
import {
  show3D, setViewPreset, captureSelection,
  setModelRotation, setShading, setMaterialColor,
} from "./threed.js";

let current = "2d";

export function initViewSwitch() {
  // Workspace tabs (top of the header).
  document.querySelectorAll(".ws-tab").forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  });

  // View preset buttons (Blender-style camera angles).
  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setViewPreset(btn.dataset.preset);
      markPreset(btn.dataset.preset);
    });
  });

  // Transform sliders + reset button.
  bindRotation();

  // Shading mode + material colour.
  if ($("d3shade"))
    $("d3shade").addEventListener("change", () => setShading($("d3shade").value));
  if ($("d3matcolor"))
    $("d3matcolor").addEventListener("input", () => setMaterialColor($("d3matcolor").value));

  setView("2d");
}

function setView(v) {
  const is3d = v === "3d";

  // Capture the 2D selection *before* the 2D canvas is hidden, so
  // "Build selection" still has something to act on in the 3D workspace.
  if (is3d) captureSelection();

  // Activate the matching view container (2D / 3D / Plan).
  ["2d", "3d", "plan"].forEach((view) =>
    $("view" + view).classList.toggle("view--active", view === v));
  document.querySelectorAll(".ws-tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.view === v));
  document.body.classList.toggle("view-2d", v === "2d");
  document.body.classList.toggle("view-3d", is3d);
  document.body.classList.toggle("view-plan", v === "plan");

  const wsName = v === "3d" ? "3D Model" : v === "plan" ? "Plan" : "2D Design";
  if ($("sbWorkspace")) $("sbWorkspace").textContent = wsName;
  if ($("sbView")) $("sbView").textContent = is3d ? "Perspective" : "—";
  if ($("sbMsg")) {
    $("sbMsg").textContent = is3d
      ? "3D view · pick an operation and Build all"
      : v === "plan"
        ? "Plan · shopping list and build steps for this project"
        : "Ready · draw in 2D, then switch to 3D Model";
  }

  if (is3d) {
    show3D(); // size the renderer now that the big canvas is visible
  } else {
    // The 2D Fabric canvas was display:none (which drops its pointer-offset
    // cache); recompute it and redraw so editing works again.
    if (canvas && canvas.calcOffset) canvas.calcOffset();
    if (canvas && canvas.requestRenderAll) canvas.requestRenderAll();
  }
  current = v;
}

function markPreset(name) {
  document.querySelectorAll("[data-preset]").forEach((b) =>
    b.classList.toggle("preset-active", b.dataset.preset === name));
}

function bindRotation() {
  ["X", "Y", "Z"].forEach((axis) => {
    const slider = $("rot" + axis);
    const val = $("rot" + axis + "Val");
    if (!slider) return;
    slider.addEventListener("input", () => {
      const deg = parseFloat(slider.value) || 0;
      if (val) val.textContent = deg + "°";
      setModelRotation(axis.toLowerCase(), deg);
    });
  });
  const reset = $("rotReset");
  if (reset) {
    reset.addEventListener("click", () => {
      ["X", "Y", "Z"].forEach((axis) => {
        const slider = $("rot" + axis);
        const val = $("rot" + axis + "Val");
        if (slider) slider.value = "0";
        if (val) val.textContent = "0°";
        setModelRotation(axis.toLowerCase(), 0);
      });
    });
  }
}

// Run once on import (like the other modules' top-level side effects).
initViewSwitch();
