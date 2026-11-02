/* M2: image -> paths. Upload an image, send it to /api/trace, and commit the
   returned path objects to the document. */

import { $ } from "./state.js";
import { api, loadDoc } from "./sync.js";
import { scheduleCommit } from "./persistence.js";

$("traceThreshold").addEventListener("input", (e) => {
  $("traceThresholdVal").textContent = e.target.value;
});

$("traceBtn").addEventListener("click", async () => {
  const fileInput = $("traceFile");
  const status = $("traceStatus");
  const file = fileInput.files && fileInput.files[0];
  if (!file) {
    status.textContent = "choose an image first";
    status.style.color = "#d9480f";
    return;
  }

  const fd = new FormData();
  fd.append("file", file);
  fd.append("mode", $("traceMode").value);
  fd.append("threshold", $("traceThreshold").value);
  fd.append("invert", $("traceInvert").checked);
  fd.append("smooth", $("traceSmooth").checked);
  fd.append("remove_bg", $("traceRemBg").checked);

  status.textContent = "tracing…";
  status.style.color = "#6b7280";
  try {
    const res = await fetch("/api/trace", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    const objects = data.objects || [];
    if (!objects.length) {
      status.textContent = "no contours found — try another threshold or mode";
      status.style.color = "#d9480f";
      return;
    }
    // Commit every traced path to the backend, then rebuild from the doc.
    for (const o of objects) await api("POST", "/api/objects", o);
    await loadDoc(await api("GET", "/api/doc", null));
    scheduleCommit();
    status.textContent = `added ${objects.length} path(s)`;
    status.style.color = "#6b7280";
  } catch (err) {
    status.textContent = "error: " + err.message;
    status.style.color = "#d9480f";
  }
});
