// Verify no bare (non-`state.`) references to the old top-level mutable globals
// remain. Every one of these must now be accessed as `state.<name>`.
import fs from "fs";
import path from "path";

const dir = path.resolve("static/js");
const files = ["state", "sync", "layers", "page", "tools", "console", "trace", "persistence", "app"]
  .map((f) => path.join(dir, f + ".js"));

const names = [
  "suppressSync", "pendingConnectors", "activeLayerId", "snapGrid", "snapObjects",
  "snapPage", "dimensionMode", "dimStart", "dimPreview", "snapGuides",
  "histIndex", "commitTimer",
];

let bad = 0;
for (const f of files) {
  const lines = fs.readFileSync(f, "utf8").split("\n");
  lines.forEach((raw, i) => {
    const code = raw.replace(/\/\/.*$/, ""); // drop line comments
    for (const n of names) {
      const re = new RegExp("(?<![\\w.$])" + n + "\\b", "g");
      let m;
      while ((m = re.exec(code))) {
        const before = code.slice(Math.max(0, m.index - 6), m.index);
        if (before.endsWith("state.")) continue; // correct: state.<name>
        // Ignore the property definitions inside the `state` object literal.
        if (/[{\s,]\s*$/.test(before) && /:\s*\S/.test(code.slice(m.index + n.length))) continue;
        // Ignore DOM-id lookups like $("snapGrid") — these refer to HTML elements.
        if (before.endsWith('$("') || before.endsWith("$('")) continue;
        console.log(`${path.basename(f)}:${i + 1}: bare '${n}' -> ${raw.trim().slice(0, 90)}`);
        bad++;
        break;
      }
    }
  });
}
console.log(bad === 0
  ? "NO BARE OLD-GLOBAL REFERENCES — all converted to state.*"
  : `\n${bad} bare reference(s) found`);
process.exitCode = bad === 0 ? 0 : 1;
