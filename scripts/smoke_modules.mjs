// Runtime smoke test for the ES modules WITHOUT a browser.
// We mock just enough of the browser (fabric, document, window, localStorage,
// fetch, crypto) so that every module can be imported (running its top-level
// side effects: event-listener registration, template form build, boot()).
// This catches two things `node --check` cannot:
//   1. An import whose name is not actually exported (Node throws).
//   2. A top-level ReferenceError (e.g. code still using the old `doc`/`snapGrid`
//      globals instead of `state.*`).
// It does NOT verify rendering or interaction — only that the module graph
// evaluates cleanly.

function makeMock() {
  // A callable, indexable, settable no-op that returns more mocks.
  const fn = function () { return makeMock(); };
  return new Proxy(fn, {
    get(_t, prop) {
      if (prop === "then") return undefined; // never a thenable
      if (prop === Symbol.toPrimitive) return () => "";
      if (prop === Symbol.iterator) return function* () {};
      if (prop === "length") return 0;
      if (prop === "value") return "shelf"; // makes renderTplParams pick a valid key
      if (prop === "checked") return false;
      if (prop === "textContent") return "";
      if (prop === "innerHTML") return "";
      if (prop === "hidden") return false;
      if (prop === "files") return { length: 0 };
      if (prop === "style") return makeMock();
      if (prop === "classList") return { add() {}, remove() {}, toggle() {} };
      if (prop === "dataset") return {};
      return makeMock();
    },
    set() { return true; },
    apply() { return makeMock(); },
    construct() { return makeMock(); },
  });
}

function setGlobal(name, val) {
  try { globalThis[name] = val; }
  catch { /* read-only built-in (e.g. crypto, navigator) — leave as-is */ }
}

setGlobal("fabric", makeMock());
setGlobal("document", makeMock());
setGlobal("window", makeMock());
// (crypto intentionally left as Node's built-in; genId isn't called at import)
setGlobal("localStorage", {
  getItem() { return null; }, // force boot() down the /api/doc path
  setItem() {},
});
setGlobal("fetch", async () => ({
  ok: true, status: 200, statusText: "ok",
  json: async () => makeMock(),
}));
setGlobal("confirm", () => false);
setGlobal("alert", () => {});
setGlobal("FormData", class { append() {} });
setGlobal("navigator", makeMock());

// The canvas mock needs getContext/width/height used only inside handlers we
// don't trigger; the generic mock covers them. fabric.Canvas is called as
// `new fabric.Canvas("c", ...)` -> construct trap returns a mock. Good.

try {
  await import("../static/js/app.js");
  console.log("MODULES EVALUATED CLEANLY — no missing exports, no top-level ReferenceErrors.");
  process.exitCode = 0;
} catch (err) {
  console.error("MODULE EVALUATION FAILED:");
  console.error(err && err.stack ? err.stack : err);
  process.exitCode = 1;
}
