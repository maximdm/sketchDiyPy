import { describe, it, expect } from "vitest";
import { UNIT_MM, pxToUnit, formatLength } from "../../static/js/units.js";

// First frontend unit test (review.md §6.3 M2). The pure converters in sync.js
// still need to be extracted before they can be covered the same way — this
// seeds the pattern with a genuinely pure, dependency-free module.
describe("units helpers", () => {
  it("maps unit names to millimetres", () => {
    expect(UNIT_MM.mm).toBe(1.0);
    expect(UNIT_MM.cm).toBe(10.0);
    expect(UNIT_MM.inch).toBeCloseTo(25.4);
  });

  it("converts pixels to the chosen unit", () => {
    // default 3.78 px per mm
    expect(pxToUnit(3.78, "mm", 3.78)).toBeCloseTo(1.0);
    expect(pxToUnit(37.8, "cm", 3.78)).toBeCloseTo(1.0);
  });

  it("falls back to mm scale for unknown units", () => {
    expect(pxToUnit(3.78, "furlong", 3.78)).toBeCloseTo(1.0);
  });

  it("formats a length with sensible precision", () => {
    expect(formatLength(3.78, "mm", 3.78)).toBe("1.00 mm");
    expect(formatLength(37.8, "mm", 3.78)).toBe("10.0 mm");
    expect(formatLength(378, "mm", 3.78)).toBe("100 mm");
  });
});
