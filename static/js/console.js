/* Python console (Hybrid mode) and the parametric template gallery. */

import { $ } from "./state.js";
import { api, loadDoc } from "./sync.js";
import { scheduleCommit } from "./persistence.js";

$("runBtn").addEventListener("click", async () => {
  const code = $("code").value;
  $("runStatus").textContent = "running…";
  try {
    const data = await api("POST", "/api/run", { code });
    await loadDoc(data);
    $("runStatus").textContent = `ok · ${data.objects.length} objects`;
    $("runStatus").style.color = "#6b7280";
    scheduleCommit();
  } catch (err) {
    $("runStatus").textContent = "error: " + err.message;
    $("runStatus").style.color = "#d9480f";
  }
});

$("resetBtn").addEventListener("click", async () => {
  const data = await api("GET", "/api/doc", null).catch(() => null);
  if (data) loadDoc(data);
});

// ---- M4: parametric template gallery (built from layout/connector helpers) --
// Each template is a small param schema + a generator that returns Python using
// the console helpers. Inputs feed the generator; the result is run like the
// console, so the layout stays fully editable afterwards.
const TEMPLATE_DEFS = {
  shelf: {
    params: [
      { key: "width", label: "Width (px)", def: 300, min: 80, max: 840, step: 10 },
      { key: "shelves", label: "Shelves", def: 3, min: 1, max: 8, step: 1 },
      { key: "height", label: "Height (px)", def: 380, min: 120, max: 660, step: 10 },
      { key: "thickness", label: "Board (px)", def: 14, min: 4, max: 40, step: 1 },
    ],
    generate(p) {
      const w = p.width | 0, n = p.shelves | 0, h = p.height | 0, t = p.thickness | 0;
      const postW = Math.max(10, Math.round(t * 0.9));
      const top = Math.round(350 - h / 2);
      const leftX = Math.round(450 - w / 2);
      let s = "clear()\n";
      const innerTop = top + 20;
      const stepY = n > 1 ? (h - 40) / (n - 1) : 0;
      for (let i = 0; i < n; i++) {
        const y = n > 1 ? Math.round(innerTop + i * stepY) : Math.round(top + h / 2);
        s += `rect(450, ${y}, ${w}, ${t}, fill="#d9b38c", stroke="#7a5230")\n`;
      }
      s += `left = rect(${leftX}, ${top}, ${postW}, ${h}, fill="#b07d4f", stroke="#7a5230")\n`;
      s += `right = rect(${leftX + w + postW}, ${top}, ${postW}, ${h}, fill="#b07d4f", stroke="#7a5230")\n`;
      s += `text(450, ${Math.max(24, top - 14)}, "Shelf", stroke="#333", fontSize=34)\n`;
      const dy = Math.min(680, top + h + 22);
      s += `dimension(${leftX}, ${dy}, ${leftX + w + 2 * postW}, ${dy}, offset=-24)\n`;
      s += `connect(left, right, "right", "left")\n`;
      return s;
    },
  },
  drillGrid: {
    params: [
      { key: "cols", label: "Columns", def: 6, min: 1, max: 20, step: 1 },
      { key: "rows", label: "Rows", def: 4, min: 1, max: 20, step: 1 },
      { key: "spacing", label: "Spacing (px)", def: 50, min: 20, max: 120, step: 5 },
      { key: "holeR", label: "Hole Ø/2 (px)", def: 9, min: 3, max: 30, step: 1 },
    ],
    generate(p) {
      const cols = p.cols | 0, rows = p.rows | 0, sp = p.spacing | 0, r = p.holeR | 0;
      const startX = Math.round(450 - (cols * sp - sp) / 2);
      const startY = Math.round(350 - (rows * sp - sp) / 2);
      let s = "clear()\nholes=[]\n";
      for (let rr = 0; rr < rows; rr++)
        for (let c = 0; c < cols; c++) {
          const x = startX + c * sp, y = startY + rr * sp;
          s += `holes.append(circle(${x}, ${y}, ${r}, stroke="#d9480f", fill=None))\n`;
        }
      s += `text(${startX}, ${Math.max(24, startY - 16)}, "Drill guide", stroke="#333", fontSize=28)\n`;
      return s;
    },
  },
  box: {
    params: [
      { key: "width", label: "Width (px)", def: 260, min: 80, max: 840, step: 10 },
      { key: "panels", label: "Panels", def: 4, min: 2, max: 8, step: 1 },
      { key: "gap", label: "Gap (px)", def: 150, min: 20, max: 300, step: 10 },
    ],
    generate(p) {
      const w = p.width | 0, n = p.panels | 0, g = p.gap | 0, t = 16;
      const total = n * t + (n - 1) * g;
      const top = Math.round(350 - total / 2);
      let s = "clear()\npanels=[]\n";
      for (let i = 0; i < n; i++) {
        const y = Math.round(top + i * (t + g));
        s += `panels.append(rect(450, ${y}, ${w}, ${t}, fill="#cbb89a", stroke="#7a5230"))\n`;
      }
      s += `text(450, ${Math.max(24, top - 14)}, "Box / crate", stroke="#333", fontSize=28)\n`;
      const dy = Math.min(680, top + total + 22);
      s += `dimension(${Math.round(450 - w / 2)}, ${dy}, ${Math.round(450 + w / 2)}, ${dy}, offset=24)\n`;
      for (let i = 0; i < n - 1; i++)
        s += `connect(panels[${i}], panels[${i + 1}], "bottom", "top")\n`;
      return s;
    },
  },
  mat: {
    params: [
      { key: "cols", label: "Columns", def: 4, min: 1, max: 16, step: 1 },
      { key: "rows", label: "Rows", def: 3, min: 1, max: 16, step: 1 },
      { key: "cell", label: "Cell (px)", def: 90, min: 30, max: 200, step: 10 },
      { key: "gap", label: "Gap (px)", def: 10, min: 0, max: 40, step: 2 },
    ],
    generate(p) {
      const cols = p.cols | 0, rows = p.rows | 0, cell = p.cell | 0, gap = p.gap | 0;
      const startX = Math.round(450 - (cols * (cell + gap) - gap) / 2);
      const startY = Math.round(350 - (rows * (cell + gap) - gap) / 2);
      let s = "clear()\n";
      for (let r = 0; r < rows; r++)
        for (let c = 0; c < cols; c++) {
          const x = startX + c * (cell + gap), y = startY + r * (cell + gap);
          s += `rect(${x}, ${y}, ${cell}, ${cell}, fill=None, stroke="#868e96")\n`;
        }
      const dy = Math.min(686, startY + rows * (cell + gap) - gap + 22);
      s += `dimension(${startX}, ${dy}, ${startX + cols * (cell + gap) - gap}, ${dy}, offset=-24)\n`;
      return s;
    },
  },
};

function renderTplParams() {
  const def = TEMPLATE_DEFS[$("tplSelect").value];
  const host = $("tplParams");
  host.innerHTML = "";
  def.params.forEach((pr) => {
    const lab = document.createElement("label");
    const span = document.createElement("span");
    span.textContent = pr.label;
    const inp = document.createElement("input");
    inp.type = "number";
    inp.min = pr.min; inp.max = pr.max; inp.step = pr.step;
    inp.value = pr.def; inp.id = "tpl_" + pr.key;
    lab.appendChild(span);
    lab.appendChild(inp);
    host.appendChild(lab);
  });
}

function runTemplate() {
  const def = TEMPLATE_DEFS[$("tplSelect").value];
  const p = {};
  def.params.forEach((pr) => {
    p[pr.key] = parseFloat($("tpl_" + pr.key).value);
  });
  const code = def.generate(p);
  api("POST", "/api/run", { code })
    .then((d) => { loadDoc(d); scheduleCommit(); })
    .catch((err) => alert("Template failed: " + err.message));
}

$("tplSelect").addEventListener("change", renderTplParams);
$("tplGenerate").addEventListener("click", runTemplate);
renderTplParams();

export { TEMPLATE_DEFS, renderTplParams, runTemplate };
