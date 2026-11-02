// Static audit of the ES-module import/export graph under static/js/.
// `node --check` only parses syntax; it does NOT verify that an imported name
// is actually exported by the target module. This script does that check so we
// catch missing-export / typo bugs without a browser.
import { readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath, pathToFileURL } from "url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..", "static", "js");
const files = [
  "state", "sync", "layers", "page", "tools", "console", "trace", "persistence", "app",
];

const exportsByFile = {};
const importsByFile = {};

function collectExports(src, file) {
  const names = new Set();
  // export function/const/let/class NAME
  for (const m of src.matchAll(/export\s+(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z0-9_$]+)/g)) {
    names.add(m[1]);
  }
  // export { a, b as c }
  for (const m of src.matchAll(/export\s*\{([^}]*)\}/g)) {
    m[1].split(",").forEach((part) => {
      const trimmed = part.trim();
      if (!trimmed) return;
      const as = trimmed.split(/\s+as\s+/);
      names.add((as[1] || as[0]).trim());
    });
  }
  exportsByFile[file] = names;
}

function collectImports(src, file) {
  const imports = [];
  for (const m of src.matchAll(/import\s*(?:\*\s+as\s+[A-Za-z0-9_$]+|\{([^}]*)\}|[A-Za-z0-9_$]+)\s*from\s*["']([^"']+)["']/g)) {
    const spec = m[2];
    if (!spec.startsWith(".")) continue; // skip bare specifiers (fabric, etc.)
    const names = new Set();
    if (m[1]) {
      m[1].split(",").forEach((part) => {
        const trimmed = part.trim();
        if (!trimmed) return;
        const as = trimmed.split(/\s+as\s+/);
        names.add((as[1] || as[0]).trim());
      });
    }
    imports.push({ spec, names });
  }
  importsByFile[file] = imports;
}

let problems = 0;
for (const f of files) {
  const src = readFileSync(resolve(root, f + ".js"), "utf8");
  collectExports(src, f);
  collectImports(src, f);
}

for (const f of files) {
  for (const imp of importsByFile[f]) {
    const target = imp.spec.replace(/^\.\//, "").replace(/\.js$/, "");
    const tExports = exportsByFile[target];
    if (!tExports) {
      console.log(`MISSING MODULE: ${f} imports from ./${target}.js (not found in graph)`);
      problems++;
      continue;
    }
    for (const name of imp.names) {
      if (!tExports.has(name)) {
        console.log(`MISSING EXPORT: ${f} imports { ${name} } from ./${target}.js — not exported there`);
        problems++;
      }
    }
  }
}

if (problems === 0) {
  console.log("IMPORT GRAPH OK — every imported name is exported by its target module.");
} else {
  console.log(`\n${problems} problem(s) found.`);
  process.exitCode = 1;
}
