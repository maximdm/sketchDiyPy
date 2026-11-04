// Pure unit-conversion helpers — no DOM / Fabric imports.
//
// Seed module for the frontend unit-test suite (review.md §6.3 M2). These mirror
// core/export.py's _UNIT_MM / _format_length so the on-screen readout and the
// backend exporters agree on units. Keep this file side-effect-free and
// framework-free so it stays trivially testable in Node.

export const UNIT_MM = { mm: 1.0, cm: 10.0, inch: 25.4 };

/** Convert a length in canvas pixels to the chosen unit (default mm). */
export function pxToUnit(px, units = "mm", pxPerUnit = 3.78) {
  const scale = pxPerUnit * (UNIT_MM[units] || 1.0);
  return scale ? px / scale : 0.0;
}

/** Human-readable length, e.g. "12.50 mm" / "10.0 cm" / "100 mm". */
export function formatLength(px, units = "mm", pxPerUnit = 3.78) {
  const v = pxToUnit(px, units, pxPerUnit);
  const digits = v >= 100 ? 0 : v >= 10 ? 1 : 2;
  return `${v.toFixed(digits)} ${units}`;
}
