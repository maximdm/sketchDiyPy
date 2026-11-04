/* 3D / Extrude + Revolve panel.

   Turns the 2D sketch into solids AND hosts a multi-object 3D scene: every
   solid (a sketch build or a generative primitive) becomes a scene item that can
   be positioned, rotated, scaled and deleted independently. The whole scene is
   rendered with three.js + OrbitControls (vendored, offline) and exported as a
   single merged STL/STEP via the backend.

   The backend produces accurate STL/STEP files regardless of the viewer; if
   build123d isn't installed, the mesh + STL fall back to lightweight pure-Python
   approximations and STEP is disabled. */

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
// A Three.Group that holds every scene item. It carries the base orientation
// (maps the model's thickness axis onto "up") plus any whole-scene rotation.
let modelGroup = null;
// Each item: { id, source, transform, data, obj3d, inner, axisObj, centroid }.
//   source    : { type: "sketch"|"primitive", op/kind, depth/params, quality, ids }
//   transform : { pos:{x,y,z}, rot:{x,y,z} (deg), scale }
let sceneItems = [];
let selectedId = null;
let nextItemId = 1;

// Last sketch-build options (kept for status/debug text).
let lastIds = null;
let lastOp = "extrude";
let lastDepth = 10;
let lastAngle = 360;
let lastAxis = null;
let lastQuality = "medium";

let pickMode = false;        // true while waiting for a canvas click to set the axis
let forcedSel = null;        // selection captured when leaving the 2D view
let modelRotUser = { x: 0, y: 0, z: 0 };  // extra rotation of the whole scene
const baseRotX = -Math.PI / 2;            // maps model thickness onto world "up"
let build123dAvailable = false;

// three.js viewer
let renderer = null, scene = null, camera = null, controls = null;
let axesObj = null, gridObj = null;
let viewerReady = false;

// Frontend mirror of the backend's primitive parameter specs (for the UI only).
const PRIM_PARAMS = {
  box: [["w", "Width", 40], ["h", "Height", 40], ["d", "Depth", 40]],
  sphere: [["r", "Radius", 25]],
  cylinder: [["r", "Radius", 20], ["h", "Height", 40]],
  cone: [["r", "Radius", 20], ["h", "Height", 40]],
  torus: [["R", "Major R", 30], ["r", "Minor R", 10]],
  capsule: [["r", "Radius", 15], ["h", "Height", 30]],
  pyramid: [["w", "Base", 40], ["h", "Height", 40]],
};
const PRIM_NAMES = {
  box: "Box", sphere: "Sphere", cylinder: "Cylinder", cone: "Cone",
  torus: "Torus", capsule: "Capsule", pyramid: "Pyramid",
};

// --------------------------------------------------------------------------
// Panel value helpers
// --------------------------------------------------------------------------
function opVal() { return ($("d3op").value || "extrude").toLowerCase(); }
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
function qualityVal() { return ($("d3quality").value || "medium").toLowerCase(); }
function primQualityVal() { return ($("d3primQuality").value || "medium").toLowerCase(); }

// --------------------------------------------------------------------------
// 2D selection (used by "Build selection")
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
  camera.up.set(0, 0, 1);                 // model Z (thickness) is "up"

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

  modelGroup = new THREE.Group();
  scene.add(modelGroup);

  // Spatial references (toggled via "Grid + axes"). Sized to the scene in frameScene.
  axesObj = new THREE.AxesHelper(1);
  axesObj.scale.setScalar(50);
  axesObj.visible = $("d3grid").checked;
  scene.add(axesObj);
  gridObj = new THREE.GridHelper(1, 10);
  gridObj.rotation.x = Math.PI / 2;
  gridObj.scale.set(150, 1, 150);
  gridObj.material.transparent = true;
  gridObj.material.opacity = 0.35;
  gridObj.visible = $("d3grid").checked;
  scene.add(gridObj);

  viewerReady = true;
  resizeViewer();
  window.addEventListener("resize", resizeViewer);
  renderer.domElement.addEventListener("click", onCanvasClick);
  animate();
}

function resizeViewer() {
  const cv = $("d3canvas");
  const w = cv.clientWidth || 320;
  const h = cv.clientHeight || 240;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  if (sceneItems.length) render();
}

function buildGeometry(pos, idx) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

// Click in the viewport to select the item under the cursor.
function onCanvasClick(ev) {
  if (!viewerReady || !sceneItems.length) return;
  const rect = renderer.domElement.getBoundingClientRect();
  const mouse = new THREE.Vector2(
    ((ev.clientX - rect.left) / rect.width) * 2 - 1,
    -((ev.clientY - rect.top) / rect.height) * 2 + 1,
  );
  const ray = new THREE.Raycaster();
  ray.setFromCamera(mouse, camera);
  const hits = ray.intersectObjects(sceneItems.map((it) => it.inner), false);
  if (hits.length) {
    const it = sceneItems.find((i) => i.inner === hits[0].object);
    if (it) selectItem(it.id);
  }
}

// Centre the camera on the combined scene bounding box.
function frameScene() {
  if (!viewerReady || !sceneItems.length) return;
  modelGroup.updateMatrixWorld(true);
  const box = new THREE.Box3();
  for (const it of sceneItems) box.expandByObject(it.obj3d);
  if (box.isEmpty()) return;
  const size = new THREE.Vector3(); box.getSize(size);
  const center = new THREE.Vector3(); box.getCenter(center);
  const maxDim = Math.max(size.x, size.y, size.z) || 1;

  if (axesObj) axesObj.scale.setScalar(maxDim * 0.55);
  if (gridObj) {
    gridObj.scale.set(maxDim * 3, 1, maxDim * 3);
    gridObj.position.set(center.x, center.y, box.min.z);
  }

  const dist = maxDim * 1.6 + 5;
  camera.position.set(center.x + dist * 0.6, center.y + dist * 0.6, center.z + dist * 0.8);
  controls.target.copy(center);
  controls.update();
}

function applySceneRotation() {
  if (!modelGroup) return;
  modelGroup.rotation.set(baseRotX + modelRotUser.x, modelRotUser.y, modelRotUser.z);
  render();
}

function render() {
  if (renderer) renderer.render(scene, camera);
}
function animate() {
  requestAnimationFrame(animate);
  if (!viewerReady) return;
  const view3d = document.getElementById("view3d");
  if (!view3d || !view3d.classList.contains("view--active")) return;
  if (controls) controls.update();
  render();
}

// --------------------------------------------------------------------------
// Scene items
// --------------------------------------------------------------------------
function defaultTransform(t) {
  return {
    pos: { x: 0, y: 0, z: 0, ...(((t || {}).pos) || {}) },
    rot: { x: 0, y: 0, z: 0, ...(((t || {}).rot) || {}) },
    scale: (t && t.scale != null) ? t.scale : 1,
  };
}

function addSceneItem(data, source, transform) {
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
  const inner = new THREE.Mesh(geom, mat);
  // Centre the geometry on its own centroid (build123d coordinates) so per-item
  // transforms rotate/scale about the item's centre, matching the export path.
  const cx = (b.min[0] + b.max[0]) / 2;
  const cy = (b.min[1] + b.max[1]) / 2;
  const cz = (b.min[2] + b.max[2]) / 2;
  inner.position.set(-cx, -cy, -cz);
  const obj3d = new THREE.Group();
  obj3d.add(inner);
  modelGroup.add(obj3d);

  const item = {
    id: "item" + (nextItemId++),
    source, transform: defaultTransform(transform),
    data, obj3d, inner, axisObj: null,
  };
  if (source.type === "sketch" && source.op === "revolve") attachAxis(item, data);
  sceneItems.push(item);
  applyTransform(item);
  if (data.build123d) build123dAvailable = true;
  return item;
}

function applyTransform(item) {
  const t = item.transform;
  item.obj3d.position.set(t.pos.x, t.pos.y, t.pos.z);
  item.obj3d.rotation.set(
    THREE.MathUtils.degToRad(t.rot.x),
    THREE.MathUtils.degToRad(t.rot.y),
    THREE.MathUtils.degToRad(t.rot.z),
  );
  const s = Number.isFinite(t.scale) ? t.scale : 1;
  item.obj3d.scale.set(s, s, s);
  render();
}

// Draw the Revolve spin axis (a vertical line at axis X) as a child of the
// item's centred mesh, so it inherits the same transform.
function attachAxis(item, data) {
  const ax = item.source.axis != null ? item.source.axis
           : (data.axis === undefined || data.axis === null ? null : data.axis);
  if (ax === null) return;
  const b = data.bbox;
  const minY = b.min[1], maxY = b.max[1];
  const h = Math.max(1e-3, maxY - minY);
  const maxDim = Math.max(b.size[0], b.size[1], b.size[2]) || 1;
  const r = Math.max(0.3, maxDim * 0.004);
  const geom = new THREE.CylinderGeometry(r, r, h, 8);
  const mat = new THREE.MeshBasicMaterial({ color: 0xff5a3c });
  const axisObj = new THREE.Mesh(geom, mat);
  axisObj.position.set(ax, (minY + maxY) / 2, 0);
  item.inner.add(axisObj);
  item.axisObj = axisObj;
}

function removeItem(item) {
  modelGroup.remove(item.obj3d);
  item.obj3d.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    if (o.material) o.material.dispose();
  });
  sceneItems = sceneItems.filter((i) => i !== item);
}

function clearScene() {
  for (const it of [...sceneItems]) removeItem(it);
  selectedId = null;
  setSelTransformVisible(false);
  refreshSceneList();
  updateSceneButtons();
  frameScene();
  render();
}

// --------------------------------------------------------------------------
// Selection + per-item transform UI
// --------------------------------------------------------------------------
function setSelTransformVisible(on) {
  const el = $("d3selTransform");
  if (el) el.style.display = on ? "" : "none";
}
function setSlider(axis, deg) {
  const s = $("rot" + axis);
  const v = $("rot" + axis + "Val");
  if (s) s.value = String(Math.round(deg));
  if (v) v.textContent = Math.round(deg) + "°";
}

function selectItem(id) {
  selectedId = id;
  for (const it of sceneItems) {
    if (it.inner.material.emissive) {
      it.inner.material.emissive.setHex(it.id === id ? 0x223a55 : 0x000000);
    }
  }
  refreshSceneList();
  const item = sceneItems.find((i) => i.id === id);
  if (item) {
    const t = item.transform;
    setSelTransformVisible(true);
    $("posX").value = t.pos.x;
    $("posY").value = t.pos.y;
    $("posZ").value = t.pos.z;
    $("scaleAll").value = t.scale;
    setSlider("X", t.rot.x);
    setSlider("Y", t.rot.y);
    setSlider("Z", t.rot.z);
  }
}

function deselect() {
  selectedId = null;
  for (const it of sceneItems) {
    if (it.inner.material.emissive) it.inner.material.emissive.setHex(0x000000);
  }
  setSelTransformVisible(false);
  refreshSceneList();
  setSlider("X", THREE.MathUtils.radToDeg(modelRotUser.x));
  setSlider("Y", THREE.MathUtils.radToDeg(modelRotUser.y));
  setSlider("Z", THREE.MathUtils.radToDeg(modelRotUser.z));
}

function deleteSelected() {
  const item = sceneItems.find((i) => i.id === selectedId);
  if (!item) return;
  removeItem(item);
  selectedId = null;
  setSelTransformVisible(false);
  refreshSceneList();
  updateSceneButtons();
  frameScene();
  render();
}

function onTransformInput() {
  if (!selectedId) return;
  const item = sceneItems.find((i) => i.id === selectedId);
  if (!item) return;
  const num = (id, d) => {
    const v = parseFloat($(id).value);
    return Number.isFinite(v) ? v : d;
  };
  item.transform.pos = { x: num("posX", 0), y: num("posY", 0), z: num("posZ", 0) };
  item.transform.scale = num("scaleAll", 1);
  applyTransform(item);
}

function itemName(it) {
  if (it.source.type === "primitive") return PRIM_NAMES[it.source.kind] || "Primitive";
  const op = (it.source.op || "extrude").toLowerCase();
  return "Sketch · " + (op === "revolve" ? "Revolve" : op === "sweep" ? "Sweep" : "Extrude");
}

function refreshSceneList() {
  const ul = $("d3scene");
  if (!ul) return;
  ul.innerHTML = "";
  if (!sceneItems.length) {
    const li = document.createElement("li");
    li.style.opacity = ".6";
    li.style.cursor = "default";
    li.textContent = "No items yet";
    ul.appendChild(li);
    return;
  }
  for (const it of sceneItems) {
    const li = document.createElement("li");
    li.dataset.id = it.id;
    if (it.id === selectedId) li.classList.add("selected");
    const span = document.createElement("span");
    span.textContent = itemName(it);
    li.appendChild(span);
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = (it.data.triangles || 0) + " △";
    li.appendChild(badge);
    li.addEventListener("click", () => selectItem(it.id));
    ul.appendChild(li);
  }
}

function updateSceneButtons() {
  const has = sceneItems.length > 0;
  $("d3stl").disabled = !has;
  $("d3step").disabled = !(has && build123dAvailable);
}

// --------------------------------------------------------------------------
// Status helpers
// --------------------------------------------------------------------------
function setStatus(msg) {
  if ($("d3status")) $("d3status").textContent = msg;
  if ($("sbMsg")) $("sbMsg").textContent = msg;
}
function setBusy(busy, message) {
  $("d3ExtrudeSel").disabled = busy;
  $("d3ExtrudeAll").disabled = busy;
  const has = sceneItems.length > 0;
  $("d3stl").disabled = busy || !has;
  $("d3step").disabled = busy || !(has && build123dAvailable);
  if (message !== undefined) setStatus(message);
}
function sceneTriangleTotal() {
  return sceneItems.reduce((s, it) => s + (it.data.triangles || 0), 0);
}

// --------------------------------------------------------------------------
// Build from the 2D sketch (adds a scene item)
// --------------------------------------------------------------------------
async function doBuild(useSelection) {
  const op = opVal();
  const ids = useSelection ? (forcedSel || selectionIds()) : null;
  lastIds = ids;
  lastOp = op;
  lastDepth = depthVal();
  lastAngle = angleVal();
  lastAxis = axisVal();
  lastQuality = qualityVal();
  setBusy(true, "Building…");
  try {
    const data = await api("POST", "/api/3d/extrude", {
      ids, op, depth: lastDepth, angle: lastAngle, axis: lastAxis,
      quality: lastQuality,
    });
    const source = {
      type: "sketch", op, depth: lastDepth, angle: lastAngle,
      axis: lastAxis, quality: lastQuality, ids,
    };
    addSceneItem(data, source, { pos: { x: 0, y: 0, z: 0 }, rot: { x: 0, y: 0, z: 0 }, scale: 1 });
    frameScene();
    updateSceneButtons();
    refreshSceneList();
    const b = data.bbox.size;
    const bd = data.build123d ? "build123d active" : "build123d not installed";
    const what = op === "revolve" ? `${lastAngle}° revolution`
               : op === "sweep" ? "swept along path"
               : `${lastDepth} mm thick`;
    const axisNote = op === "revolve"
      ? (lastAxis === null ? " · axis auto" : ` · axis ${lastAxis.toFixed(1)} mm`)
      : "";
    setStatus(`${sceneTriangleTotal()} triangles · ${b[0].toFixed(1)}×${b[1].toFixed(1)}×${b[2].toFixed(1)} mm · ${what}${axisNote} · ${bd} · ${sceneItems.length} item(s)`);
  } catch (err) {
    setStatus("Build failed: " + err.message);
  } finally {
    setBusy(false);
  }
}

// --------------------------------------------------------------------------
// Generative primitives (add a scene item)
// --------------------------------------------------------------------------
function renderPrimParams() {
  const kind = $("d3prim").value;
  const wrap = $("d3primParams");
  wrap.innerHTML = "";
  for (const [key, label, def] of (PRIM_PARAMS[kind] || [])) {
    const row = document.createElement("div");
    row.className = "vec-row";
    const lab = document.createElement("label");
    lab.textContent = label;
    const inp = document.createElement("input");
    inp.type = "number";
    inp.className = "field";
    inp.step = "1";
    inp.value = String(def);
    inp.dataset.primParam = key;
    row.appendChild(lab);
    row.appendChild(inp);
    wrap.appendChild(row);
  }
}

function readPrimParams() {
  const params = {};
  document.querySelectorAll("#d3primParams [data-prim-param]").forEach((inp) => {
    const v = parseFloat(inp.value);
    if (Number.isFinite(v)) params[inp.dataset.primParam] = v;
  });
  return params;
}

async function addPrim() {
  const kind = $("d3prim").value;
  const quality = primQualityVal();
  const params = readPrimParams();
  setBusy(true, `Adding ${kind}…`);
  try {
    const data = await api("POST", "/api/3d/primitive", { kind, params, quality });
    const source = { type: "primitive", kind, params, quality };
    const item = addSceneItem(
      data, source, { pos: { x: 0, y: 0, z: 0 }, rot: { x: 0, y: 0, z: 0 }, scale: 1 });
    // Stagger new items so they don't all land on top of each other.
    const n = sceneItems.length;
    item.transform.pos = { x: (n - 1) * 30, y: 0, z: 0 };
    applyTransform(item);
    frameScene();
    updateSceneButtons();
    refreshSceneList();
    selectItem(item.id);
    setStatus(`${kind} added · ${sceneTriangleTotal()} triangles · ${sceneItems.length} item(s)`);
  } catch (err) {
    setStatus("Add failed: " + err.message);
  } finally {
    setBusy(false);
  }
}

// --------------------------------------------------------------------------
// Export the whole scene as one merged STL/STEP
// --------------------------------------------------------------------------
async function download(format) {
  if (!sceneItems.length) {
    setStatus("Nothing to export — add a primitive or build from the sketch");
    return;
  }
  setBusy(true, "Exporting…");
  try {
    const items = sceneItems.map((it) => ({ source: it.source, transform: it.transform }));
    const res = await fetch("/api/3d/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format, items }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      setStatus("Export failed: " + (e.detail || res.statusText));
      return;
    }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "scene." + format;
    a.click();
    URL.revokeObjectURL(a.href);
    setStatus(`Exported scene.${format}`);
  } catch (err) {
    setStatus("Export failed: " + err.message);
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
  $("d3reset").addEventListener("click", () => { frameScene(); render(); });
  $("d3op").addEventListener("change", () => {
    const op = opVal();
    const isRev = op === "revolve";
    const isSweep = op === "sweep";
    if ($("d3depthWrap")) $("d3depthWrap").hidden = isRev || isSweep;
    if ($("d3angleWrap")) $("d3angleWrap").hidden = !isRev;
    if ($("d3axisWrap")) $("d3axisWrap").hidden = !isRev;
    if ($("d3pick")) $("d3pick").hidden = !isRev;
    if ($("d3sweepHint")) $("d3sweepHint").hidden = !isSweep;
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
    for (const it of sceneItems) it.inner.material.wireframe = $("d3wire").checked;
  });
  $("d3grid").addEventListener("change", () => {
    const on = $("d3grid").checked;
    if (axesObj) axesObj.visible = on;
    if (gridObj) gridObj.visible = on;
  });
  // Primitives
  $("d3prim").addEventListener("change", renderPrimParams);
  $("d3addPrim").addEventListener("click", addPrim);
  // Scene
  $("d3clearScene").addEventListener("click", clearScene);
  $("d3delete").addEventListener("click", deleteSelected);
  // Per-item transform inputs
  ["posX", "posY", "posZ"].forEach((id) => $(id).addEventListener("input", onTransformInput));
  $("scaleAll").addEventListener("input", onTransformInput);
  try {
    initViewer();
  } catch (e) {
    // WebGL unavailable: still wire the buttons; just no preview.
    setStatus("3D preview unavailable (WebGL off)");
  }
  renderPrimParams();
  setSelTransformVisible(false);
}

// --------------------------------------------------------------------------
// Workspace integration: called by view.js when the 3D tab is shown/hidden and
// when the user picks a view preset or rotates the model. Exported so the view
// module (which owns the tab switching) can drive the viewer without reaching
// into its internals.
// --------------------------------------------------------------------------

// Called by the view switcher when entering the 3D workspace.
export function show3D() {
  if (!viewerReady) {
    setStatus("3D preview unavailable (WebGL off)");
    return;
  }
  resizeViewer();
  render();
  if ($("sbView")) $("sbView").textContent = "Perspective";
}

// Snap the camera to a standard angle (Blender-like view presets).
export function setViewPreset(name) {
  if (!viewerReady || !sceneItems.length) return;
  modelGroup.updateMatrixWorld(true);
  const box = new THREE.Box3();
  for (const it of sceneItems) box.expandByObject(it.obj3d);
  if (box.isEmpty()) return;
  const size = new THREE.Vector3(); box.getSize(size);
  const center = new THREE.Vector3(); box.getCenter(center);
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const dist = maxDim * 1.6 + 5;
  let p = { x: center.x + dist * 0.6, y: center.y + dist * 0.6, z: center.z + dist * 0.8 };
  if (name === "top") p = { x: center.x, y: center.y + dist, z: center.z + 0.0001 };
  else if (name === "front") p = { x: center.x, y: center.y + 0.0001, z: center.z + dist };
  else if (name === "right") p = { x: center.x + dist, y: center.y + 0.0001, z: center.z };
  camera.up.set(0, 0, 1);
  camera.position.set(p.x, p.y, p.z);
  controls.target.copy(center);
  controls.update();
  render();
  if ($("sbView")) {
    const label = name.charAt(0).toUpperCase() + name.slice(1);
    $("sbView").textContent = label;
  }
}

// Remember the current 2D selection so "Build selection" keeps working after the
// 2D canvas is hidden by the workspace switch.
export function captureSelection() {
  forcedSel = selectionIds();
}

// Rotate the model from the Transform panel. With an item selected, the rotation
// applies to that item; otherwise it spins the whole scene.
export function setModelRotation(axis, deg) {
  if (selectedId) {
    const item = sceneItems.find((i) => i.id === selectedId);
    if (item) {
      item.transform.rot[axis] = deg;
      applyTransform(item);
      return;
    }
  }
  modelRotUser[axis] = THREE.MathUtils.degToRad(deg);
  applySceneRotation();
}

// Shading + material colour controls (apply to every item).
export function setShading(mode) {
  for (const it of sceneItems) {
    it.inner.material.flatShading = mode === "flat";
    it.inner.material.needsUpdate = true;
  }
  render();
}
export function setMaterialColor(hex) {
  for (const it of sceneItems) it.inner.material.color.set(hex);
  render();
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
