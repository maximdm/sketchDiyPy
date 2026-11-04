import { defineConfig } from "vitest/config";

// Frontend unit tests (review.md §6.3 M2). The app is vanilla ES modules with no
// build step, so this only needs a Node environment — no browser/fabric.
export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/js/**/*.test.js"],
  },
});
