/* Shared, mutable application state + a few cross-cutting constants/helpers.

   Everything that was a top-level `let`/`const` in the old monolithic app.js and
   that gets *reassigned* (doc, snap flags, undo index, …) now lives on the
   `state` object so it can be shared across ES modules without reassigning an
   imported binding (ES module imports are read-only). Pure constants that are
   only ever mutated in place (objects/arrays) are exported directly.

   `fabric.min.js` is loaded as a classic script before this module, so the
   global `fabric` is available here. */

export const canvas = new fabric.Canvas("c", { selection: true });

// Reassigned-at-runtime values live here.
export const state = {
  doc: null, // latest doc dict from the server
  suppressSync: false, // true while we rebuild the canvas from the server
  pendingConnectors: [], // connector canonicals whose targets weren't ready yet
  activeLayerId: null, // layer new shapes are assigned to (UI state)
  snapGrid: true,
  snapObjects: true,
  snapPage: true,
  dimensionMode: false,
  dimStart: null,
  dimPreview: null,
  snapGuides: { xs: [], ys: [] }, // alignment guide lines shown while dragging
  histIndex: -1, // undo/redo pointer
  commitTimer: null, // debounce handle for scheduleCommit
};

export const $ = (id) => document.getElementById(id);

export const genId = () =>
  (crypto.randomUUID && crypto.randomUUID()) ||
  Math.random().toString(36).slice(2) + Date.now().toString(36);

// Shape colour palettes per theme. `addShape` and the default property inputs
// read from `palette`, so switching themes changes what new shapes look like.
export const palette = {
  rectFill: "#cfe8ff", rectStroke: "#1f6feb",
  circleStroke: "#d9480f", lineStroke: "#2f9e44", textStroke: "#222222",
};

// Named themes: board colours + grid + the shape palette + UI skin class.
export const THEMES = {
  light: {
    background: "#ffffff", grid_color: "#eef2f7", show_grid: true, show_rulers: true,
    ui: "light",
    palette: { rectFill: "#cfe8ff", rectStroke: "#1f6feb", circleStroke: "#d9480f",
               lineStroke: "#2f9e44", textStroke: "#222222" },
  },
  chalk: {
    // Black board with a faint chalk-blue grid; chalk-blue / chalk-green / white
    // strokes on dark fills so new shapes read like chalk on a blackboard.
    background: "#0e1116", grid_color: "#2b3744", show_grid: true, show_rulers: true,
    ui: "chalk",
    palette: { rectFill: "#16242f", rectStroke: "#8ab4ff", circleStroke: "#9be7a8",
               lineStroke: "#9be7a8", textStroke: "#e8eef2" },
  },
  blueprint: {
    // Classic blueprint: blue board, lighter-blue grid, white/cyan chalk lines.
    background: "#0d2b52", grid_color: "#1e4f86", show_grid: true, show_rulers: true,
    ui: "blueprint",
    palette: { rectFill: "#143a66", rectStroke: "#d6ecff", circleStroke: "#9be7ff",
               lineStroke: "#9be7ff", textStroke: "#eaf4ff" },
  },
  kraft: {
    // Warm kraft-paper: cream board, tan grid, earthy ochre/brown chalk lines.
    background: "#efe2c4", grid_color: "#d9c6a0", show_grid: true, show_rulers: true,
    ui: "kraft",
    palette: { rectFill: "#e7d8b8", rectStroke: "#7a5230", circleStroke: "#b5651d",
               lineStroke: "#5b4636", textStroke: "#3a2e1f" },
  },
  neon: {
    // Dark mode with neon accents: near-black board, neon cyan/magenta/green.
    background: "#0b0f14", grid_color: "#1c2b33", show_grid: true, show_rulers: true,
    ui: "neon",
    palette: { rectFill: "#10161d", rectStroke: "#39d0ff", circleStroke: "#ff5ec7",
               lineStroke: "#39ff88", textStroke: "#eaffff" },
  },
};
