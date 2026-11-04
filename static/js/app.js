/* sketchDiy frontend entry point.

   The app was previously one ~2 100-line app.js. It is now split into ES modules
   under static/js/ with no build step — the browser loads them directly (the
   server serves them over HTTP, so ES module imports work without a bundler or
   CDN). `state.js` creates the shared Fabric canvas and holds the mutable
   application state; every other module imports from it and from each other.

   Importing each module runs its top-level side effects (DOM event listeners, the
   template form). `boot()` then restores the canvas from localStorage / server. */

import "./state.js";   // creates the Fabric canvas; must come first
import "./sync.js";    // canonical<->Fabric + server sync
import "./layers.js";  // layers panel + listeners
import "./page.js";    // themes, page settings, readouts, add-shape + listeners
import "./tools.js";   // dimension/connector tools, snapping, align, delete, rulers
import "./console.js"; // Python console + template gallery
import "./trace.js";   // image -> paths
import "./threed.js";   // 3D extrude panel + viewer
import "./view.js";     // workspace tabs (2D Design / 3D Model / Plan) + 3D context panel
import "./plan.js";      // Plan workspace (shopping list + assembly steps)
import "./menu.js";       // top menu bar (Shapes / Materials / Tools / Edit)
import { boot } from "./persistence.js";

boot();
