// Cross-module call checker (v2).
// Phase 1: extract imports + local defs from the RAW source (so string stripping
// doesn't erase the `from "./x.js"` specifiers).
// Phase 2: strip comments/strings, then scan for `name(` call sites and flag any
// identifier that is neither local, nor imported, nor a known builtin/global.
// This catches a module calling another module's function without importing it
// (a runtime ReferenceError the import-audit and top-level smoke test miss).
import fs from "fs";
import path from "path";

const dir = path.resolve("static/js");
const files = ["state", "sync", "layers", "page", "tools", "console", "trace", "persistence", "app"];

const BUILTINS = new Set([
  "if", "for", "while", "switch", "catch", "function", "return", "await", "async", "new",
  "typeof", "instanceof", "delete", "void", "do", "else", "throw", "Math", "JSON",
  "Object", "Array", "Set", "Map", "Number", "String", "Boolean", "parseFloat",
  "parseInt", "isNaN", "confirm", "alert", "console", "fetch", "setTimeout",
  "clearTimeout", "Promise", "Date", "document", "window", "localStorage",
  "crypto", "FormData", "navigator", "requestAnimationFrame", "Symbol", "Proxy",
  "Intl", "RegExp", "Error", "encodeURIComponent",
]);

const defRe = [
  /function\s+([A-Za-z_$][\w$]*)/g,
  /const\s+([A-Za-z_$][\w$]*)\s*=/g,
  /let\s+([A-Za-z_$][\w$]*)\s*=/g,
  /export\s+function\s+([A-Za-z_$][\w$]*)/g,
  /export\s+const\s+([A-Za-z_$][\w$]*)\s*=/g,
  /async\s+function\s+([A-Za-z_$][\w$]*)/g,
  /export\s+async\s+function\s+([A-Za-z_$][\w$]*)/g,
  /async\s+const\s+([A-Za-z_$][\w$]*)\s*=/g,
];

function strip(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/\/\/.*$/gm, " ")
    .replace(/`(?:\\.|[^`\\])*`/g, " ")
    .replace(/"(?:\\.|[^"\\])*"/g, " ")
    .replace(/'(?:\\.|[^'\\])*'/g, " ");
}

const mods = {};
for (const f of files) {
  const raw = fs.readFileSync(path.join(dir, f + ".js"), "utf8");
  const local = new Set();
  for (const re of defRe) {
    let m;
    while ((m = re.exec(raw))) local.add(m[1]);
  }
  const imported = new Set();
  for (const m of raw.matchAll(/import\s*(?:\*\s+as\s+\w+|\{([^}]*)\}|(\w+))\s*from\s*["']([^"']+)["']/g)) {
    if (m[1]) m[1].split(",").forEach((p) => {
      const t = p.trim(); if (!t) return;
      imported.add((t.split(/\s+as\s+/)[1] || t.split(/\s+as\s+/)[0]).trim());
    });
    if (m[2]) imported.add(m[2]);
  }
  mods[f] = { src: strip(raw), local, imported };
}

let problems = 0;
for (const f of files) {
  const { src, local, imported } = mods[f];
  const callRe = /(^|[^\w$.])([A-Za-z_$][\w$]*)\s*\(/g;
  let m;
  const seen = new Set();
  while ((m = callRe.exec(src))) {
    const name = m[2];
    if (seen.has(name)) continue;
    seen.add(name);
    if (local.has(name) || imported.has(name) || BUILTINS.has(name)) continue;
    console.log(`${f}.js: possible unresolved call -> ${name}()`);
    problems++;
  }
}

console.log(problems === 0
  ? "CALL CHECK OK — every cross-module call is imported."
  : `\n${problems} candidate(s) to review (likely params like fn()).`);
process.exitCode = problems === 0 ? 0 : 1;
