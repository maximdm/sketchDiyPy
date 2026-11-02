/* 3D / Extrude + Revolve panel.

   Takes the current 2D sketch (or the selected shapes) and turns it into a solid
   on the backend via build123d: either extruded (a prism) or revolved (a lathe /
   surface of revolution). The mesh comes back as a triangle soup (positions +
   indices) and is rendered here with three.js + OrbitControls (vendored, offline).

   The backend produces accurate STL/STEP files regardless of the viewer; if
   build123d isn't installed, the mesh + STL fall back to lightweight pure-Python
   approximations and STEP/Revolve are disabled. */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { canvas, $ } from "./state.js";
import { pxPerUnit } from "./page.js";

// --------------------------------------------------------------------------
// Server helper (mirrors sync.js `api`)
// --------------------------------------------------------------------------
async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

// --------------------------------------------------------------------------
// State
// --------------------------------------------------------------------------
let mesh = null;        // {positions, indices, bbox, accurate, build123d, op}
let lastIds = null;     // ids used for the last build (null = whole doc)
let lastOp = "extrude";
let lastDepth = 10;
let lastAngle = 360;
let lastAxis = null;    // revolve axis X in mm (null => backend default)
let lastQuality = "medium";
let pickMode = false;   // true while waiting for a canvas click to set the axis

// three.js scene
let renderer = null, scene = null, camera = null, controls = null;
let meshObj = null, axesObj = null, gridObj = null, axisObj = null;
let viewerReady = false;

function opVal() {
  return ($("d3op").value || "extrude").toLowerCase();
}
function depthVal() {
  const v = parseFloat($("d3depth").value);
  return Number.isFinite(v) && v > 0 ? v : 10;
}
function angleVal() {
  const v = parseFloat($("d3angle").value);
  return Number.isFinite(v) ? Math.min(360, Math.max(1, v)) : 360;
}
function axisVal() {
  const v = parseFloat($("d3axis").value);
  return Number.isFinite(v) ? v : null;
}
function qualityVal() {
  return ($("d3quality").value || "medium").toLowerCase();
}

// --------------------------------------------------------------------------
// Selection
// --------------------------------------------------------------------------
function selectionIds() {
  const objs = canvas.getActiveObjects();
  if (!objs.length) return null;
  const ids = new Set();
  for (const o of objs) {
    if (o.type === "activeSelection") {
      o.getObjects().forEach((c) => c.id && ids.add(c.id));
    } else if (o.id) {
      ids.add(o.id);
    }
  }
  return ids.size ? [...ids] : null;
}

// --------------------------------------------------------------------------
// three.js viewer
// --------------------------------------------------------------------------
function initViewer() {
  const cv = $("d3canvas");
  renderer = new THREE.WebGLRenderer({ canvas: cv, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b1220);

  camera = new THREE.PerspectiveCamera(45, 1, 0.1, 5000);
  // Model Z (extrude thickness / revolve height) is the "up" axis.
  camera.up.set(0, 0, 1);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;

  scene.add(new THREE.HemisphereLight(0xffffff, 0x223044, 0.9));
  const dir = new THREE.DirectionalLight(0xffffff, 0.8);
  dir.position.set(1, 1.5, 2);
  scene.add(dir);
  const fill = new THREE.DirectionalLight(0x88aaff, 0.3);
  fill.position.set(-1, -0.5, -1);
  scene.add(fill);

  // Spatial references (toggled via the "Grid + axes" checkbox).
  axesObj = new THREE.AxesHelper(1);          // scaled per-model below
  axesObj.visible = $("d3grid").checked;
  scene.add(axesObj);
  gridObj = new THREE.GridHelper(1, 10);      // lies in XY (z=0) after rotation
  gridObj.rotation.x = Math.PI / 2;
  gridObj.material.transparent = true;
  gridObj.material.opacity = 0.35;
  gridObj.visible = $("d3grid").checked;
  scene.add(gridObj);

  viewerReady = true;
  resizeViewer();
  window.addEventListener("resize", resizeViewer);
  animate();
}

function resizeViewer() {
  const cv = $("d3canvas");
  const w = cv.clientWidth || 320;
  const h = cv.clientHeight || 240;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  if (mesh) render();
}

function buildGeometry(pos, idx) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

function frameModel(b) {
  // Centre the model. build123d models have Z as thickness / revolve height; we
  // rotate the mesh -90° about X so (x, y, z) -> (x, z, -y), mapping that axis to
  // world "up". After centring, the local z range maps to world y; the model's
  // bottom (local z = b.min[2]) lands at world y = b.min[2] - cz.
  const cx = (b.min[0] + b.max[0]) / 2;
  const cy = (b.min[1] + b.max[1]) / 2;
  const cz = (b.min[2] + b.max[2]) / 2;
  if (meshObj) meshObj.position.set(-cx, -cz, cy);

  const maxDim = Math.max(b.size[0], b.size[1], b.size[2]) || 1;
  if (axesObj) axesObj.scale.setScalar(maxDim * 0.55);
  if (gridObj) {
    // Grid geometry is in its local X-Z plane; local Z maps to world Y after the
    // X rotation, so scale local X and Z to size it in world X/Y.
    gridObj.scale.set(maxDim * 3, 1, maxDim * 3);
    // The mesh is rotated so the sketch's vertical (local Y) is world-up (world
    // Z = -y + cy). The floor therefore sits at the lowest world Z, i.e. the
    // model's highest local Y.
    gridObj.position.set(0, 0, -b.max[1] + cy);
  }

  const dist = maxDim * 1.6 + 5;
  camera.position.set(dist * 0.6, dist * 0.6, dist * 0.8);
  controls.target.set(0, 0, 0);
  controls.update();
}

function showMesh(data) {
  if (!viewerReady) return;
  if (meshObj) {
    scene.remove(meshObj);
    meshObj.geometry.dispose();
    meshObj.material.dispose();
    meshObj = null;
  }
  const b = data.bbox;
  const geom = buildGeometry(data.positions, data.indices);
  const mat = new THREE.MeshStandardMaterial({
    color: 0x4696e6,
    metalness: 0.05,
    roughness: 0.65,
    side: THREE.DoubleSide,
    flatShading: false,
    wireframe: $("d3wire").checked,
  });
  meshObj = new THREE.Mesh(geom, mat);
  // Rotate so the model's Z axis becomes world up.
  meshObj.rotation.x = -Math.PI / 2;
  scene.add(meshObj);

  updateAxis(data);
  frameModel(b);
  render();
}

// Draw the Revolve spin axis (a vertical line in the sketch at axis X) so the
// picker result is visible. Added as a child of meshObj so it inherits the same
// rotation/centring transform. Shown when the user explicitly set an axis, or
// (for "auto") when the server returned the effective axis it used.
function updateAxis(data) {
  if (axisObj) {
    axisObj.geometry.dispose();
    axisObj.material.dispose();
    axisObj = null;
  }
  if (data.op !== "revolve" || !meshObj) return;
  // Prefer the explicit user axis; otherwise fall back to the server-computed
  // auto axis (min profile X − 1e-3) so even an un-set axis is visualised.
  const ax = lastAxis !== null ? lastAxis
           : (data.axis === undefined || data.axis === null ? null : data.axis);
  if (ax === null) return;
  const b = data.bbox;
  const minY = b.min[1], maxY = b.max[1];
  const h = Math.max(1e-3, maxY - minY);
  const maxDim = Math.max(b.size[0], b.size[1], b.size[2]) || 1;
  const r = Math.max(0.3, maxDim * 0.004);
  // CylinderGeometry's axis is local Y, which matches the revolve (Y) axis and,
  // after meshObj's -90° X rotation, appears vertical on screen.
  const geom = new THREE.CylinderGeometry(r, r, h, 8);
  const mat = new THREE.MeshBasicMaterial({ color: 0xff5a3c });
  axisObj = new THREE.Mesh(geom, mat);
  axisObj.position.set(ax, (minY + maxY) / 2, 0);
  meshObj.add(axisObj);
}

function render() {
  if (renderer) renderer.render(scene, camera);
}
function animate() {
  requestAnimationFrame(animate);
  if (controls) controls.update();
  render();
}

// --------------------------------------------------------------------------
// Actions
// --------------------------------------------------------------------------
function setBusy(busy, message) {
  $("d3ExtrudeSel").disabled = busy;
  $("d3ExtrudeAll").disabled = busy;
  $("d3stl").disabled = busy || !mesh;
  $("d3step").disabled = busy || !(mesh && mesh.build123d);
  if (message !== undefined) $("d3status").textContent = message;
}

async function doBuild(useSelection) {
  const op = opVal();
  const ids = useSelection ? selectionIds() : null;
  lastIds = ids;
  lastOp = op;
  lastDepth = depthVal();
  lastAngle = angleVal();
  lastAxis = axisVal();
  lastQuality = qualityVal();
  setBusy(true, "Building…");
  try {
    mesh = await api("POST", "/api/3d/extrude", {
      ids, op, depth: lastDepth, angle: lastAngle, axis: lastAxis,
      quality: lastQuality,
    });
    showMesh(mesh);
    const b = mesh.bbox.size;
    const approx = mesh.accurate ? "" : " · preview approximation (install build123d for exact solids)";
    const bd = mesh.build123d ? "build123d active" : "build123d not installed";
    const what = op === "revolve" ? `${lastAngle}° revolution`
               : op === "sweep" ? "swept along path"
               : `${lastDepth} mm thick`;
    const axisNote = op === "revolve"
      ? (lastAxis === null ? " · axis auto" : ` · axis ${lastAxis.toFixed(1)} mm`)
      : "";
    let repairNote = "";
    if (mesh.trimesh && mesh.repaired > 0) {
      const wt = mesh.watertight ? " · watertight" : " · not watertight";
      repairNote = ` · trimesh cleaned ${mesh.repaired} face(s)${wt}`;
    }
    $("d3status").textContent =
      `${mesh.triangles} triangles · ${b[0].toFixed(1)}×${b[1].toFixed(1)}×${b[2].toFixed(1)} mm · ${what}${axisNote} · ${bd}${approx}${repairNote}`;
  } catch (err) {
    mesh = null;
    if (axisObj) { axisObj.geometry.dispose(); axisObj.material.dispose(); axisObj = null; }
    if (meshObj) { scene.remove(meshObj); meshObj = null; }
    $("d3status").textContent = "Build failed: " + err.message;
    render();
  } finally {
    setBusy(false);
  }
}

async function download(format) {
  setBusy(true, "Exporting…");
  try {
    const res = await fetch("/api/3d/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ids: lastIds, op: lastOp, depth: lastDepth, angle: lastAngle,
        axis: lastAxis, quality: lastQuality, format,
      }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      $("d3status").textContent = "Export failed: " + (e.detail || res.statusText);
      return;
    }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "sketch." + format;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (err) {
    $("d3status").textContent = "Export failed: " + err.message;
  } finally {
    setBusy(false);
  }
}

// --------------------------------------------------------------------------
// Wiring (runs on import, like the other modules)
// --------------------------------------------------------------------------
export function initThreeD() {
  $("d3ExtrudeSel").addEventListener("click", () => doBuild(true));
  $("d3ExtrudeAll").addEventListener("click", () => doBuild(false));
  $("d3stl").addEventListener("click", () => download("stl"));
  $("d3step").addEventListener("click", () => download("step"));
  $("d3reset").addEventListener("click", () => {
    if (mesh) { frameModel(mesh.bbox); render(); }
  });
  $("d3op").addEventListener("change", () => {
    const op = opVal();
    const isRev = op === "revolve";
    const isSweep = op === "sweep";
    $("d3depthWrap").hidden = isRev || isSweep;
    $("d3angleWrap").hidden = !isRev;
    $("d3axisWrap").hidden = !isRev;
    $("d3pick").hidden = !isRev;
    $("d3sweepHint").hidden = !isSweep;
    if (!isRev && pickMode) setPickMode(false);
  });
  $("d3pick").addEventListener("click", () => {
    if (pickMode) setPickMode(false);
    else if (opVal() === "revolve") setPickMode(true);
  });
  // Click on the 2D sketch to place the revolve axis (vertical line) at that X.
  canvas.on("mouse:down", (opt) => {
    if (!pickMode) return;
    const p = canvas.getPointer(opt.e);
    const mm = p.x / pxPerUnit();
    $("d3axis").value = mm.toFixed(2);
    lastAxis = mm;
    setPickMode(false);
  });
  $("d3wire").addEventListener("change", () => {
    if (meshObj) meshObj.material.wireframe = $("d3wire").checked;
  });
  $("d3grid").addEventListener("change", () => {
    const on = $("d3grid").checked;
    if (axesObj) axesObj.visible = on;
    if (gridObj) gridObj.visible = on;
  });
  try {
    initViewer();
  } catch (e) {
    // WebGL unavailable: still wire the buttons; just no preview.
    $("d3status").textContent = "3D preview unavailable (WebGL off)";
  }
}

function setPickMode(on) {
  pickMode = on;
  $("d3pick").textContent = on ? "✕ Cancel pick" : "↕ Pick axis";
  $("d3pick").classList.toggle("btn-primary", on);
  if (on) {
    $("d3status").textContent =
      "Click on the sketch to set the revolve axis (a vertical line at that X)";
  }
}

initThreeD();
