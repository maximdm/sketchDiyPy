"""UI smoke check for sketchDiy (Playwright).

Boots the real FastAPI server, loads the page in headless Chromium (no CDN
anymore — all assets are local), and fails on:
  * uncaught JavaScript exceptions (pageerror),
  * console.error messages (favicon noise ignored),
  * failed asset requests (proves every /static/* file loads),
  * missing required element IDs, or a zero-sized canvas.

This is OPTIONAL: it is skipped automatically when Playwright (or its browser)
is not installed, so the core `pytest` suite stays green without it.

Setup:
    pip install playwright
    playwright install chromium
Run with the rest of the suite, or alone:
    pytest tests/ui_smoke.py
"""

import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

playwright = pytest.importorskip("playwright")

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Every element id the UI must keep. If a refactor drops one, this fails.
REQUIRED_IDS = [
    "c", "dimBtn", "connectBtn", "deleteBtn", "clearBtn", "unlockAllBtn",
    "units", "alignSelect", "snapGrid", "snapObjects", "snapPage", "scaleReadout",
    "props", "propsBody", "pStroke", "pFill", "pFillNone", "pWidth",
    "pWidthVal", "pLock", "selSize",
    "pageW", "pageH", "pageBg", "pageRulers", "gridColor", "gridToggle", "themeSelect",
    "traceFile", "traceMode", "traceThreshold", "traceThresholdVal",
    "traceInvert", "traceSmooth", "traceRemBg", "traceBtn", "traceStatus",
    "tplSelect", "tplParams", "tplGenerate",
    "projName", "projSave", "projList",
    "code", "runBtn", "resetBtn", "runStatus",
    # workspace tabs + 3D Model view (added alongside the Blender-style layout)
    "view2d", "view3d", "d3canvas", "d3op", "d3depth", "d3angle", "d3axis",
    "d3pick", "d3quality", "d3wire", "d3grid", "d3ExtrudeSel", "d3ExtrudeAll",
    "d3reset", "d3stl", "d3step", "d3status", "d3sweepHint", "d3shade",
    "d3matcolor", "rotX", "rotY", "rotZ", "rotReset",
    "sbWorkspace", "sbView", "sbMsg", "statusbar",
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def base_url():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("server process exited before starting")
        try:
            import urllib.request
            urllib.request.urlopen(url + "/api/doc", timeout=2)
            break
        except Exception:
            time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError("server did not become reachable")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


@pytest.mark.skipif(
    not os.environ.get("UI_SMOKE_RUN"),
    reason="set UI_SMOKE_RUN=1 to exercise the (slower) Playwright UI check",
)
def test_ui_loads_without_errors_and_renders(base_url):
    from playwright.sync_api import sync_playwright

    page_errors = []
    console_errors = []
    failed_requests = []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:  # browser not installed
            pytest.skip(f"could not launch Chromium: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)

        # structural checks
        for elem_id in REQUIRED_IDS:
            assert page.locator(f"#{elem_id}").count() == 1, f"missing #{elem_id}"
        assert page.locator(".card").count() >= 5, "expected at least 5 cards"
        assert page.locator(".btn").count() >= 10, "expected toolbar/action buttons"

        # the Fabric canvas must actually have a rendered size
        box = page.locator("#c").bounding_box()
        assert box and box["width"] > 0 and box["height"] > 0, "canvas not sized"

        # record a screenshot (default repo path; override with UI_SMOKE_SHOT)
        shot = os.environ.get("UI_SMOKE_SHOT",
                              str(ROOT / "docs" / "ui-smoke.png"))
        pathlib.Path(shot).parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=shot, full_page=True)
        browser.close()

    # report helpers
    noise = lambda t: "favicon" in t.lower()  # ignore favicon.ico 404s
    real_console = [e for e in console_errors if not noise(e)]
    real_failed = [u for u in failed_requests if "favicon" not in u.lower()]

    assert not page_errors, "uncaught JS errors:\n" + "\n".join(page_errors)
    assert not real_console, "console errors:\n" + "\n".join(real_console)
    assert not real_failed, "failed asset requests:\n" + "\n".join(real_failed)


@pytest.mark.skipif(
    not os.environ.get("UI_SMOKE_RUN"),
    reason="set UI_SMOKE_RUN=1 to exercise the (slower) Playwright UI check",
)
def test_console_run_roundtrip(base_url):
    """Exercise the real browser -> server path (not just 'page loads').

    This was historically impossible in-sandbox because headless Chrome stalled
    on the Fabric CDN under --virtual-time-budget; now Fabric is vendored and we
    use real-time headless Chrome, so we can actually drive the UI.

    Types a console program, clicks Run, then proves the document really changed
    on the server and that an export reflects the new shapes.
    """
    from playwright.sync_api import sync_playwright

    page_errors, console_errors, failed_requests = [], [], []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:
            pytest.skip(f"could not launch Chromium: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)

        program = (
            "clear()\n"
            "rect(450, 350, 120, 80, fill='#ffe066')\n"
            "circle(620, 400, 40)\n"
        )
        page.fill("#code", program)
        page.click("#runBtn")

        # Run handler flips #runStatus to "ok · N objects" or "error: ...".
        page.wait_for_function(
            "() => { const t = document.getElementById('runStatus').textContent || '';"
            " return t.includes('ok') || t.includes('error'); }",
            timeout=15000,
        )
        status = page.locator("#runStatus").text_content() or ""
        assert "error" not in status.lower(), f"console run failed: {status}"

        # The document on the server must reflect the two shapes we just added.
        doc = page.request.get(base_url + "/api/doc").json()
        assert len(doc.get("objects", [])) >= 2, \
            "running console code did not change the server document"

        # And an export must contain those shapes.
        svg = page.request.get(base_url + "/api/export/svg").text()
        assert "<svg" in svg, "SVG export missing root element"
        assert "<rect" in svg, "SVG export missing the rect we drew"
        assert "<circle" in svg, "SVG export missing the circle we drew"

        browser.close()

    noise = lambda t: "favicon" in t.lower()
    real_console = [e for e in console_errors if not noise(e)]
    real_failed = [u for u in failed_requests if "favicon" not in u.lower()]
    assert not page_errors, "uncaught JS errors:\n" + "\n".join(page_errors)
    assert not real_console, "console errors:\n" + "\n".join(real_console)
    assert not real_failed, "failed asset requests:\n" + "\n".join(real_failed)


@pytest.mark.skipif(
    not os.environ.get("UI_SMOKE_RUN"),
    reason="set UI_SMOKE_RUN=1 to exercise the (slower) Playwright UI check",
)
def test_page_controls_persist(base_url):
    """Drive the Page card (size / colour / rulers) and confirm the settings
    actually reach the server document (and therefore exports + persistence)."""
    from playwright.sync_api import sync_playwright

    page_errors, console_errors, failed_requests = [], [], []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:
            pytest.skip(f"could not launch Chromium: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)

        # Page card inputs must exist (structural check for the new feature).
        for cid in ("pageW", "pageH", "pageBg", "pageRulers"):
            assert page.locator(f"#{cid}").count() == 1, f"missing #{cid}"

        # Size: set width/height in units; the handler converts to px via pxPerUnit.
        page.fill("#pageW", "200")
        page.fill("#pageH", "150")
        page.locator("#pageW").dispatch_event("change")
        page.locator("#pageH").dispatch_event("change")

        # Colour.
        page.fill("#pageBg", "#102030")

        # Rulers off.
        page.uncheck("#pageRulers")

        # The server document must reflect all three settings.
        d = page.request.get(base_url + "/api/doc").json()
        ppu = (d.get("px_per_unit") or 3.78) * {"mm": 1, "cm": 10, "inch": 25.4}.get(d.get("units"), 1)
        assert abs(d["width"] - 200 * ppu) < 0.5, f"width not applied: {d['width']}"
        assert abs(d["height"] - 150 * ppu) < 0.5, f"height not applied: {d['height']}"
        assert d["background"].lower() == "#102030", f"background not applied: {d['background']}"
        assert d["show_rulers"] is False, "rulers toggle not applied"

        # And the SVG export must now use the chosen background colour.
        svg = page.request.get(base_url + "/api/export/svg").text()
        assert 'fill="#102030"' in svg, "SVG export did not use the new background"

        browser.close()

    noise = lambda t: "favicon" in t.lower()
    real_console = [e for e in console_errors if not noise(e)]
    real_failed = [u for u in failed_requests if "favicon" not in u.lower()]
    assert not page_errors, "uncaught JS errors:\n" + "\n".join(page_errors)
    assert not real_console, "console errors:\n" + "\n".join(real_console)
    assert not real_failed, "failed asset requests:\n" + "\n".join(real_failed)


@pytest.mark.skipif(
    not os.environ.get("UI_SMOKE_RUN"),
    reason="set UI_SMOKE_RUN=1 to exercise the (slower) Playwright UI check",
)
def test_theme_and_grid(base_url):
    """Apply the Chalkboard theme + toggle the grid, and confirm both the board
    colours and the grid setting reach the server document."""
    from playwright.sync_api import sync_playwright

    page_errors, console_errors, failed_requests = [], [], []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:
            pytest.skip(f"could not launch Chromium: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)

        # The suite shares one server; reset the opt-in preference so the
        # default-off assertion stays deterministic across repeated runs.
        page.request.put(base_url + "/api/settings", data={"recolor_on_theme": False})
        page.reload(wait_until="networkidle")

        for cid in ("gridColor", "gridToggle", "themeSelect", "recolorOnTheme",
                    "gridSize", "resetTheme", "exportGrid"):
            assert page.locator(f"#{cid}").count() == 1, f"missing #{cid}"

        # Recolour-on-theme is opt-in and OFF by default: changing themes must not
        # touch existing artwork unless the user enables it.
        assert page.locator("#recolorOnTheme").is_checked() is False, \
            "recolor-on-theme should default to off"
        # Grid-in-export is OFF by default too.
        assert page.locator("#exportGrid").is_checked() is False, \
            "export-grid should default to off"

        # Enable the opt-in and add a shape so we can verify it repaints on theme change.
        page.check("#recolorOnTheme")
        page.click("[data-shape='rect']")
        page.wait_for_timeout(200)

        # Chalkboard theme -> black board + chalk palette (and the rect recolours).
        page.select_option("#themeSelect", "chalk")
        page.wait_for_timeout(200)
        d = page.request.get(base_url + "/api/doc").json()
        assert d["theme"] == "chalk", "theme not applied"
        assert d["background"].lower() == "#0e1116", f"chalk bg not applied: {d['background']}"
        assert d["grid_color"].lower() == "#2b3744", f"chalk grid not applied: {d['grid_color']}"
        assert page.evaluate("document.body.classList.contains('chalk')") is True, \
            "chalk UI skin class not applied"

        # Theme-aware toolbar swatch reflects the chalk rect palette (#8ab4ff).
        sw = page.evaluate(
            "getComputedStyle(document.querySelector('[data-shape=rect] .sw')).backgroundColor")
        assert sw == "rgb(138, 180, 255)", f"toolbar swatch not themed: {sw}"

        # Toggle the grid off.
        page.uncheck("#gridToggle")
        d = page.request.get(base_url + "/api/doc").json()
        assert d["show_grid"] is False, "grid toggle not applied"

        # Pick a custom grid colour.
        page.check("#gridToggle")
        page.fill("#gridColor", "#224466")
        d = page.request.get(base_url + "/api/doc").json()
        assert d["grid_color"].lower() == "#224466", f"grid colour not applied: {d['grid_color']}"

        # Grid spacing reaches the server and the canvas background-size.
        page.fill("#gridSize", "40")
        d = page.request.get(base_url + "/api/doc").json()
        assert d["grid_size"] == 40, f"grid size not applied: {d['grid_size']}"
        bg_size = page.evaluate(
            "getComputedStyle(document.getElementById('c')).backgroundSize")
        assert "40px 40px" in bg_size and "20px 20px" not in bg_size, \
            f"grid background size not applied: {bg_size}"

        # Grid option: OFF by default means the SVG export has no grid group.
        svg_default = page.request.get(base_url + "/api/export/svg").text()
        assert "<g stroke=" not in svg_default, "grid leaked into default SVG export"

        # Enabling it bakes the grid into the SVG export.
        page.check("#exportGrid")
        page.wait_for_timeout(150)
        svg_grid = page.request.get(base_url + "/api/export/svg").text()
        assert '<g stroke=' in svg_grid and 'stroke-width="1"' in svg_grid, \
            "grid not included in SVG export when option enabled"

        # SVG export must use the chalk board background.
        svg = page.request.get(base_url + "/api/export/svg").text()
        assert 'fill="#0e1116"' in svg, "SVG export did not use the chalk background"

        # Blueprint theme -> blue board + cyan palette + its own UI skin.
        page.select_option("#themeSelect", "blueprint")
        page.wait_for_timeout(200)
        d = page.request.get(base_url + "/api/doc").json()
        assert d["theme"] == "blueprint", "blueprint theme not applied"
        assert d["background"].lower() == "#0d2b52", f"blueprint bg not applied: {d['background']}"
        assert d["grid_color"].lower() == "#1e4f86", f"blueprint grid not applied: {d['grid_color']}"
        assert page.evaluate("document.body.classList.contains('blueprint')") is True, \
            "blueprint UI skin class not applied"
        # Existing artwork is recoloured to the theme palette (proves the theme
        # visibly transforms the drawing, not just the board).
        recolored = any(
            o.get("style", {}).get("stroke", "").lower() == "#d6ecff"
            for o in d["objects"]
        )
        assert recolored, "existing shapes were not recoloured to the theme palette"

        # Kraft + Neon themes also apply (board colour, UI skin, recolor).
        for theme, bg, skin, stroke in (
            ("kraft", "#efe2c4", "kraft", "#7a5230"),
            ("neon", "#0b0f14", "neon", "#39d0ff"),
        ):
            page.select_option("#themeSelect", theme)
            page.wait_for_timeout(200)
            d = page.request.get(base_url + "/api/doc").json()
            assert d["theme"] == theme, f"{theme} theme not applied"
            assert d["background"].lower() == bg, f"{theme} bg not applied: {d['background']}"
            assert page.evaluate(f"document.body.classList.contains('{skin}')") is True, \
                f"{theme} UI skin class not applied"
            assert any(o.get("style", {}).get("stroke", "").lower() == stroke
                       for o in d["objects"]), \
                f"existing shapes not recoloured for {theme}"

        # "Reset page to theme" restores the theme's default board after a manual tweak.
        page.fill("#pageBg", "#123456")
        page.wait_for_timeout(150)
        page.click("#resetTheme")
        page.wait_for_timeout(200)
        d = page.request.get(base_url + "/api/doc").json()
        assert d["background"].lower() == "#0b0f14", \
            f"reset-to-theme did not restore neon board: {d['background']}"

        browser.close()

    noise = lambda t: "favicon" in t.lower()
    real_console = [e for e in console_errors if not noise(e)]
    real_failed = [u for u in failed_requests if "favicon" not in u.lower()]
    assert not page_errors, "uncaught JS errors:\n" + "\n".join(page_errors)
    assert not real_console, "console errors:\n" + "\n".join(real_console)
    assert not real_failed, "failed asset requests:\n" + "\n".join(real_failed)


@pytest.mark.skipif(
    not os.environ.get("UI_SMOKE_RUN"),
    reason="set UI_SMOKE_RUN=1 to exercise the (slower) Playwright UI check",
)
def test_snap_preferences_persist(base_url):
    from playwright.sync_api import sync_playwright

    page_errors = []
    console_errors = []
    failed_requests = []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:  # browser not installed
            pytest.skip(f"could not launch Chromium: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)

        # Snap options default on.
        for cid in ("snapGrid", "snapObjects", "snapPage"):
            assert page.locator(f"#{cid}").count() == 1, f"missing #{cid}"
            assert page.locator(f"#{cid}").is_checked() is True, f"{cid} default on"

        # Turn Grid snap OFF and confirm it survives a reload (persisted on doc).
        # Wait past the 350ms autosave debounce so the change fully flushes before
        # the reload restores from localStorage.
        page.uncheck("#snapGrid")
        page.wait_for_timeout(600)
        page.reload(wait_until="networkidle")
        assert page.locator("#snapGrid").is_checked() is False, \
            "snapGrid not persisted across reload"
        d = page.request.get(base_url + "/api/doc").json()
        assert d["snap_grid"] is False, f"snap_grid not saved: {d.get('snap_grid')}"

        # Re-enable and confirm persistence the other way.
        page.check("#snapGrid")
        page.wait_for_timeout(600)
        page.reload(wait_until="networkidle")
        assert page.locator("#snapGrid").is_checked() is True, \
            "snapGrid re-enable not persisted"
        d = page.request.get(base_url + "/api/doc").json()
        assert d["snap_grid"] is True, f"snap_grid not restored: {d.get('snap_grid')}"

        browser.close()

    noise = lambda t: "favicon" in t.lower()
    real_console = [e for e in console_errors if not noise(e)]
    real_failed = [u for u in failed_requests if "favicon" not in u.lower()]
    assert not page_errors, "uncaught JS errors:\n" + "\n".join(page_errors)
    assert not real_console, "console errors:\n" + "\n".join(real_console)
    assert not real_failed, "failed asset requests:\n" + "\n".join(real_failed)


@pytest.mark.skipif(
    not os.environ.get("UI_SMOKE_RUN"),
    reason="set UI_SMOKE_RUN=1 to exercise the (slower) Playwright UI check",
)
def test_workspace_tab_switch(base_url):
    """Exercise the Blender-style 2D Design / 3D Model workspace switch.

    Verifies: two workspace tabs exist; the 2D view is active on load and the
    3D-only toolbar is hidden; clicking "3D Model" activates the 3D viewport
    (big canvas sized, status bar updates) and hides the 2D toolbar; a real
    "Build all" produces a mesh and enables STL export; switching back restores
    2D and the toolbar.
    """
    from playwright.sync_api import sync_playwright

    page_errors, console_errors, failed_requests = [], [], []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:
            pytest.skip(f"could not launch Chromium: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)

        # Three workspace tabs (2D Design / 3D Model / Plan).
        assert page.locator(".ws-tab").count() == 3, "expected three workspace tabs"
        assert page.locator('[data-view="2d"]').count() == 1
        assert page.locator('[data-view="3d"]').count() == 1

        # On load: 2D active, 3D hidden, 2D toolbar visible.
        assert page.evaluate(
            "document.getElementById('view2d').classList.contains('view--active')")
        assert not page.evaluate(
            "document.getElementById('view3d').classList.contains('view--active')")
        assert page.locator("#toolbar2d").is_visible(), "2D toolbar should show in 2D"
        assert not page.evaluate("document.body.classList.contains('view-3d')")

        # Switch to the 3D Model workspace.
        page.click('[data-view="3d"]')
        page.wait_for_timeout(300)

        assert page.evaluate(
            "document.getElementById('view3d').classList.contains('view--active')")
        assert not page.evaluate(
            "document.getElementById('view2d').classList.contains('view--active')")
        assert page.evaluate("document.body.classList.contains('view-3d')")
        # The 2D-only toolbar is hidden in the 3D workspace.
        assert not page.locator("#toolbar2d").is_visible(), \
            "2D toolbar should be hidden in the 3D workspace"
        # Status bar reflects the active workspace.
        assert (page.locator("#sbWorkspace").text_content() or "") == "3D Model"
        # The big 3D viewport canvas is actually sized.
        box = page.locator("#d3canvas").bounding_box()
        assert box and box["width"] > 50 and box["height"] > 50, \
            "3D viewport canvas not sized"

        # Build the whole sketch into a solid (explicit build button, as designed).
        page.click("#d3ExtrudeAll")
        page.wait_for_function(
            "() => { const t = document.getElementById('d3status').textContent || '';"
            " return t.includes('triangles'); }",
            timeout=30000,
        )
        # A successful build enables STL export.
        assert page.locator("#d3stl").is_enabled(), \
            "STL export should be enabled after a successful build"

        # View presets must not throw and should update the status bar.
        page.click('[data-preset="top"]')
        page.wait_for_timeout(150)
        assert (page.locator("#sbView").text_content() or "") == "Top"
        page.click('[data-preset="perspective"]')
        page.wait_for_timeout(150)

        # Transform rotation should not error.
        page.locator("#rotY").fill("45")
        page.locator("#rotY").dispatch_event("input")
        page.wait_for_timeout(150)

        # Switch back to 2D: the 2D view is active again and the toolbar returns.
        page.click('[data-view="2d"]')
        page.wait_for_timeout(300)
        assert page.evaluate(
            "document.getElementById('view2d').classList.contains('view--active')")
        assert not page.evaluate("document.body.classList.contains('view-3d')")
        assert page.locator("#toolbar2d").is_visible(), \
            "2D toolbar should return when back in the 2D workspace"
        assert (page.locator("#sbWorkspace").text_content() or "") == "2D Design"

        # The 2D Fabric canvas is still sized and usable.
        box = page.locator("#c").bounding_box()
        assert box and box["width"] > 0 and box["height"] > 0, "2D canvas not sized"

        browser.close()

    noise = lambda t: "favicon" in t.lower()
    real_console = [e for e in console_errors if not noise(e)]
    real_failed = [u for u in failed_requests if "favicon" not in u.lower()]
    assert not page_errors, "uncaught JS errors:\n" + "\n".join(page_errors)
    assert not real_console, "console errors:\n" + "\n".join(real_console)
    assert not real_failed, "failed asset requests:\n" + "\n".join(real_failed)


@pytest.mark.skipif(
    not os.environ.get("UI_SMOKE_RUN"),
    reason="set UI_SMOKE_RUN=1 to exercise the (slower) Playwright UI check",
)
def test_layers(base_url):
    """Exercise the Layers panel: add a layer, draw on it, move the selection to
    another layer, reorder (regression: a freshly-drawn shape must NOT vanish),
    toggle visibility, and solo — all without JS/console/asset errors."""
    from playwright.sync_api import sync_playwright

    page_errors, console_errors, failed_requests = [], [], []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:
            pytest.skip(f"could not launch Chromium: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)

        for cid in ("layerList", "layerAdd", "layerDelete", "layerSend", "layerTarget"):
            assert page.locator(f"#{cid}").count() == 1, f"missing #{cid}"

        # Add a second layer (becomes the active layer for new shapes).
        page.click("#layerAdd")
        page.wait_for_timeout(150)
        doc = page.request.get(base_url + "/api/doc").json()
        by_name = {l["name"]: l["id"] for l in doc["layers"]}
        assert "Layer 1" in by_name and "Layer 2" in by_name
        layer2_id = by_name["Layer 2"]
        layer1_id = by_name["Layer 1"]

        # Draw a rect — it lands on the active layer (Layer 2).
        page.click('[data-shape="rect"]')
        page.wait_for_timeout(200)
        doc = page.request.get(base_url + "/api/doc").json()
        new_rect = next(
            (o for o in doc["objects"]
             if o["type"] == "rect" and (o.get("meta") or {}).get("layer") == layer2_id),
            None,
        )
        assert new_rect, "drawn rect did not land on the active layer"

        # Move the selection (the just-drawn rect) to Layer 1 via the panel.
        page.select_option("#layerTarget", label="Layer 1")
        page.click("#layerSend")
        page.wait_for_timeout(200)
        doc = page.request.get(base_url + "/api/doc").json()
        moved = next((o for o in doc["objects"] if o["id"] == new_rect["id"]), None)
        assert moved and (moved.get("meta") or {}).get("layer") == layer1_id, \
            "selection was not moved to Layer 1"

        # Reorder Layer 1 to the bottom. This must NOT drop the freshly drawn
        # shape (the stale-doc data-loss regression).
        page.evaluate(
            "() => { const rows = [...document.querySelectorAll('#layerList .layer-row')];"
            " const row = rows.find(r => r.querySelector('.layer-name').value === 'Layer 1');"
            " row.querySelector('button[title^=\"Move layer down\"]').click(); }"
        )
        page.wait_for_timeout(200)
        doc = page.request.get(base_url + "/api/doc").json()
        survivors = [o for o in doc["objects"] if o["id"] == new_rect["id"]]
        assert len(survivors) == 1, "drawn rect lost on layer reorder (data-loss bug!)"

        # Visibility toggle reaches the server document.
        page.evaluate(
            "() => { const rows = [...document.querySelectorAll('#layerList .layer-row')];"
            " const row = rows.find(r => r.querySelector('.layer-name').value === 'Layer 1');"
            " row.querySelector('button[title^=\"Toggle visibility\"]').click(); }"
        )
        page.wait_for_timeout(150)
        doc = page.request.get(base_url + "/api/doc").json()
        l1 = next(l for l in doc["layers"] if l["name"] == "Layer 1")
        assert l1["visible"] is False, "layer visibility toggle not applied"

        # Solo toggles without error and updates the status line.
        page.evaluate(
            "() => { const rows = [...document.querySelectorAll('#layerList .layer-row')];"
            " const row = rows.find(r => r.querySelector('.layer-name').value === 'Layer 2');"
            " row.querySelector('button[title^=\"Solo\"]').click(); }"
        )
        page.wait_for_timeout(150)
        assert "solo" in (page.locator("#layerStatus").text_content() or "").lower()

        browser.close()

    noise = lambda t: "favicon" in t.lower()
    real_console = [e for e in console_errors if not noise(e)]
    real_failed = [u for u in failed_requests if "favicon" not in u.lower()]
    assert not page_errors, "uncaught JS errors:\n" + "\n".join(page_errors)
    assert not real_console, "console errors:\n" + "\n".join(real_console)
    assert not real_failed, "failed asset requests:\n" + "\n".join(real_failed)


@pytest.mark.skipif(
    not os.environ.get("UI_SMOKE_RUN"),
    reason="set UI_SMOKE_RUN=1 to exercise the (slower) Playwright UI check",
)
def test_plan(base_url):
    """Exercise the Plan workspace: switch to the Plan tab, add buy items
    (verifying the live total + per-line cost), add an assembly step, and
    confirm everything round-trips to the server document (/api/settings)."""
    from playwright.sync_api import sync_playwright

    page_errors, console_errors, failed_requests = [], [], []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:
            pytest.skip(f"could not launch Chromium: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)

        # Draw shapes in 2D so we can later derive a cut list from them.
        # Two identical rects should merge into one part (qty 2); a circle is a
        # separate part.
        page.click('[data-shape="rect"]'); page.wait_for_timeout(150)
        page.click('[data-shape="rect"]'); page.wait_for_timeout(150)
        page.click('[data-shape="circle"]'); page.wait_for_timeout(150)

        # All required Plan element ids must be present.
        for cid in ("viewplan", "planSummary", "buyList", "buyAdd", "buyTotal",
                    "stepList", "stepAdd", "planCurrency"):
            assert page.locator(f"#{cid}").count() == 1, f"missing #{cid}"

        # There are now three workspace tabs.
        assert page.locator(".ws-tab").count() == 3, "expected three workspace tabs"

        # Open the Plan tab.
        page.click('[data-view="plan"]')
        page.wait_for_timeout(200)
        assert page.evaluate(
            "document.getElementById('viewplan').classList.contains('view--active')")
        assert (page.locator("#sbWorkspace").text_content() or "") == "Plan"

        # Regenerate the cut list from the drawing: two identical rects collapse
        # to one panel (qty 2) plus a disc from the circle.
        page.click("#partRegen")
        # Wait past the debounced server push (400ms) before reading the doc.
        page.wait_for_timeout(700)
        part_rows = page.locator("#partList .part-row")
        assert part_rows.count() >= 2, "expected derived parts from the drawing"
        doc = page.request.get(base_url + "/api/doc").json()
        parts = (doc.get("plan") or {}).get("parts", [])
        assert len(parts) >= 2, "parts not derived/saved"
        assert any((p.get("qty") or 0) == 2 for p in parts), \
            "identical rects should merge into one qty-2 part"
        # Tag the first part with a material and confirm it persists to the server.
        part_rows.first.locator("select.field").select_option(label="Plywood")
        page.wait_for_timeout(600)
        doc = page.request.get(base_url + "/api/doc").json()
        parts = (doc.get("plan") or {}).get("parts", [])
        assert any(p.get("material") == "Plywood" for p in parts), \
            "part material not saved to server"

        # Add another identical rect and regenerate again: the existing part
        # stays in place (keeps its material) and only its quantity grows — it is
        # not dropped and recreated as a new line. (Draw in 2D first, since the
        # 2D toolbar is hidden on the Plan tab.)
        page.click('[data-view="2d"]'); page.wait_for_timeout(150)
        page.click('[data-shape="rect"]'); page.wait_for_timeout(150)
        page.click('[data-view="plan"]'); page.wait_for_timeout(150)
        page.click("#partRegen"); page.wait_for_timeout(700)
        doc = page.request.get(base_url + "/api/doc").json()
        parts = (doc.get("plan") or {}).get("parts", [])
        assert any(p.get("material") == "Plywood" for p in parts), \
            "part lost its material on a second regenerate"
        assert any((p.get("qty") or 0) == 3 for p in parts), \
            "merged rect quantity not updated in place"

        # Add two buy items with quantity + price; the total should sum live.
        page.click("#buyAdd")
        page.click("#buyAdd")
        page.wait_for_timeout(150)

        rows = page.locator("#buyList .buy-row")
        assert rows.count() == 2, "expected two buy rows"
        # Item 1: 4 screws @ $0.25 = $1.00
        r0 = rows.nth(0)
        r0.locator("input").nth(0).fill("Wood screws")
        r0.locator("input").nth(1).fill("4")
        r0.locator("input").nth(3).fill("0.25")
        # Item 2: 1 board @ $12.00 = $12.00
        r1 = rows.nth(1)
        r1.locator("input").nth(0).fill("Ply board")
        r1.locator("input").nth(1).fill("1")
        r1.locator("input").nth(3).fill("12")

        page.wait_for_timeout(250)
        total_txt = (page.locator("#buyTotal").text_content() or "").replace("$", "")
        assert abs(float(total_txt) - 13.0) < 0.001, \
            f"buy total wrong: expected 13.00 got {total_txt}"
        # Summary stat mirrors the total.
        stat = (page.locator("#planStatCost").text_content() or "").replace("$", "")
        assert abs(float(stat) - 13.0) < 0.001, f"summary cost wrong: {stat}"

        # Add an assembly step.
        page.click("#stepAdd")
        page.wait_for_timeout(150)
        step_rows = page.locator("#stepList .step-row")
        assert step_rows.count() == 1, "expected one step row"
        step_rows.nth(0).locator("textarea").fill("Cut the ply to size")
        page.wait_for_timeout(150)

        # Wait past the debounced server push, then confirm it persisted.
        page.wait_for_timeout(600)
        doc = page.request.get(base_url + "/api/doc").json()
        plan = doc.get("plan", {})
        assert len(plan.get("buy", [])) == 2, "buy items not saved to server"
        saved = {b.get("name"): b for b in plan["buy"]}
        # qty/price are persisted as strings (raw input values); the UI coerces
        # with Number() when totalling, so compare numerically here.
        assert abs(float(saved.get("Wood screws", {}).get("price")) - 0.25) < 1e-9, "price not saved"
        assert int(float(saved.get("Ply board", {}).get("qty"))) == 1, "qty not saved"
        assert len(plan.get("steps", [])) == 1, "step not saved to server"
        assert plan["steps"][0]["text"] == "Cut the ply to size", "step text not saved"

        # Switching away and back re-renders the saved plan.
        page.click('[data-view="2d"]')
        page.wait_for_timeout(150)
        page.click('[data-view="plan"]')
        page.wait_for_timeout(200)
        assert page.locator("#buyList .buy-row").count() == 2, \
            "buy rows lost on tab re-open"
        assert page.locator("#stepList .step-row").count() == 1, \
            "step row lost on tab re-open"

        # Notes: add a manual note and confirm it persists to the server and
        # survives opening the Plan tab again.
        page.click("#noteAdd")
        page.wait_for_timeout(150)
        note_rows = page.locator("#noteList .note-row")
        assert note_rows.count() == 1, "expected one note row"
        note_rows.nth(0).locator("textarea").fill("Pre-drill the cleats")
        page.wait_for_timeout(400)
        doc = page.request.get(base_url + "/api/doc").json()
        notes = (doc.get("plan") or {}).get("notes", [])
        manual = [n for n in notes if n.get("kind") == "manual"
                  and n.get("text") == "Pre-drill the cleats"]
        assert manual, "manual note not saved to server"
        page.click('[data-view="2d"]')
        page.wait_for_timeout(150)
        page.click('[data-view="plan"]')
        page.wait_for_timeout(200)
        assert page.locator("#noteList .note-row").count() == 1, \
            "note row lost on tab re-open"

        browser.close()

    noise = lambda t: "favicon" in t.lower()
    real_console = [e for e in console_errors if not noise(e)]
    real_failed = [u for u in failed_requests if "favicon" not in u.lower()]
    assert not page_errors, "uncaught JS errors:\n" + "\n".join(page_errors)
    assert not real_console, "console errors:\n" + "\n".join(real_console)
    assert not real_failed, "failed asset requests:\n" + "\n".join(real_failed)


@pytest.mark.skipif(
    not os.environ.get("UI_SMOKE_RUN"),
    reason="set UI_SMOKE_RUN=1 to exercise the (slower) Playwright UI check",
)
def test_freehand(base_url):
    """Exercise the freehand pencil: toggle it on, drag on the canvas to sketch a
    stroke, and confirm a `path` object (with geometry) is committed to the
    server. Toggling off must clear the active state."""
    from playwright.sync_api import sync_playwright

    page_errors, console_errors, failed_requests = [], [], []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:
            pytest.skip(f"could not launch Chromium: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)

        # The freehand tool lives in the 2D toolbar.
        assert page.locator('[data-shape="free"]').count() == 1, "missing freehand tool"
        # 2D is the default workspace; the toolbar must be visible.
        assert page.locator("#toolbar2d").is_visible(), "2D toolbar should be visible on load"

        # Toggle freehand ON.
        page.click('[data-shape="free"]')
        page.wait_for_timeout(150)
        assert page.locator('[data-shape="free"]').evaluate(
            "el => el.classList.contains('active')"), "freehand did not activate"

        # Drag on the canvas to sketch a freeform stroke. (The 2D canvas can sit
        # below the fold because the side panel is tall, so scroll it into view
        # first and re-read its viewport-relative box.)
        page.locator("#c").scroll_into_view_if_needed()
        page.wait_for_timeout(100)
        box = page.locator("#c").bounding_box()
        assert box, "2D canvas has no box"
        # Start the stroke well inside the canvas and *below* the tall sticky
        # header (~350px). At this viewport the header overlaps the top of the
        # canvas, so a stroke started near the canvas top would land on the header
        # and never reach the drawing surface (no path / note would be created).
        x0, y0 = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.6
        page.mouse.move(x0, y0)
        page.mouse.down()
        for dx, dy in ((80, 40), (40, 90), (-30, 60), (20, 30)):
            page.mouse.move(x0 + dx, y0 + dy, steps=8)
        page.mouse.up()
        page.wait_for_timeout(400)

        # A path object should now be on the server document.
        doc = page.request.get(base_url + "/api/doc").json()
        paths = [o for o in doc.get("objects", [])
                 if o.get("type") == "path" and (o.get("geometry") or {}).get("d")]
        assert paths, "freehand stroke was not committed as a path"

        # The sketch should also have been captured as a note in the Plan tab.
        notes = (doc.get("plan") or {}).get("notes", [])
        sketch_notes = [n for n in notes if n.get("kind") == "sketch"]
        assert sketch_notes, "freehand stroke did not add a sketch note to the plan"

        # Open the Plan tab and confirm the note renders there (with its badge).
        page.click('[data-view="plan"]')
        page.wait_for_timeout(200)
        assert page.locator("#noteList .note-row.note-sketch").count() >= 1, \
            "sketch note not rendered in the Notes card"

        # Toggle freehand OFF and confirm the active state clears.
        page.click('[data-view="2d"]')
        page.wait_for_timeout(150)
        page.click('[data-shape="free"]')
        page.wait_for_timeout(150)
        assert not page.locator('[data-shape="free"]').evaluate(
            "el => el.classList.contains('active')"), "freehand did not deactivate"

        browser.close()

    noise = lambda t: "favicon" in t.lower()
    real_console = [e for e in console_errors if not noise(e)]
    real_failed = [u for u in failed_requests if "favicon" not in u.lower()]
    assert not page_errors, "uncaught JS errors:\n" + "\n".join(page_errors)
    assert not real_console, "console errors:\n" + "\n".join(real_console)
    assert not real_failed, "failed asset requests:\n" + "\n".join(real_failed)


@pytest.mark.skipif(
    not os.environ.get("UI_SMOKE_RUN"),
    reason="set UI_SMOKE_RUN=1 to exercise the (slower) Playwright UI check",
)
def test_notes_project_scope(base_url):
    """Notes belong to the project: drawing a sketch note and then saving the
    project under a name must update the Notes card's scope label, and the notes
    must be part of the saved document (so they ride along in the project file)."""
    from playwright.sync_api import sync_playwright

    page_errors, console_errors, failed_requests = [], [], []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:
            pytest.skip(f"could not launch Chromium: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)

        # The Notes scope starts as the unsaved project.
        assert page.locator("#noteProject").text_content() == "Unsaved project", \
            "notes scope should start as Unsaved project"

        # Draw a freehand stroke -> auto-adds a sketch note.
        page.click('[data-shape="free"]')
        page.wait_for_timeout(150)
        page.locator("#c").scroll_into_view_if_needed()
        page.wait_for_timeout(100)
        box = page.locator("#c").bounding_box()
        # Start the stroke well inside the canvas and *below* the tall sticky
        # header (~350px). At this viewport the header overlaps the top of the
        # canvas, so a stroke started near the canvas top would land on the header
        # and never reach the drawing surface (no path / note would be created).
        x0, y0 = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.6
        page.mouse.move(x0, y0)
        page.mouse.down()
        for dx, dy in ((80, 40), (40, 90), (-30, 60), (20, 30)):
            page.mouse.move(x0 + dx, y0 + dy, steps=8)
        page.mouse.up()
        page.wait_for_timeout(400)

        # Save the project under a name.
        name = "Birdhouse v2"
        page.fill("#projName", name)
        page.click("#projSave")
        page.wait_for_timeout(400)

        # The Notes card should now scope to the saved project name.
        assert page.locator("#noteProject").text_content() == name, \
            "notes scope did not update to the project name"

        # And the notes (including the sketch note) are part of the saved doc,
        # confirming they are grouped with / saved alongside the project.
        doc = page.request.get(base_url + "/api/doc").json()
        notes = (doc.get("plan") or {}).get("notes", [])
        assert any(n.get("kind") == "sketch" for n in notes), \
            "sketch note missing from the saved project document"

        browser.close()

    noise = lambda t: "favicon" in t.lower()
    real_console = [e for e in console_errors if not noise(e)]
    real_failed = [u for u in failed_requests if "favicon" not in u.lower()]
    assert not page_errors, "uncaught JS errors:\n" + "\n".join(page_errors)
    assert not real_console, "console errors:\n" + "\n".join(real_console)
    assert not real_failed, "failed asset requests:\n" + "\n".join(real_failed)


@pytest.mark.skipif(
    not os.environ.get("UI_SMOKE_RUN"),
    reason="set UI_SMOKE_RUN=1 to exercise the (slower) Playwright UI check",
)
def test_3d_multi_object_scene(base_url):
    """Exercise the generative 3D primitives + multi-object scene: add several
    primitives, confirm they appear in the Scene list, select one to reveal its
    per-item transform controls, move it, and confirm STL export is enabled."""
    from playwright.sync_api import sync_playwright

    page_errors, console_errors, failed_requests = [], [], []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:
            pytest.skip(f"could not launch Chromium: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m:
                console_errors.append(m.text) if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)

        # 3D workspace required element ids for the new primitives + scene.
        for cid in ("d3prim", "d3primParams", "d3primQuality", "d3addPrim",
                    "d3scene", "posX", "posY", "posZ", "scaleAll", "d3delete",
                    "d3clearScene", "d3selTransform"):
            assert page.locator(f"#{cid}").count() == 1, f"missing #{cid}"

        page.click('[data-view="3d"]')
        page.wait_for_timeout(400)

        # Add two primitives of different kinds into the scene.
        page.select_option("#d3prim", "box")
        page.click("#d3addPrim")
        page.wait_for_timeout(300)
        page.select_option("#d3prim", "sphere")
        page.click("#d3addPrim")
        page.wait_for_timeout(300)

        # Both appear in the Scene list.
        items = page.locator("#d3scene li")
        assert items.count() >= 2, "expected at least 2 scene items"

        # Selecting the first item reveals its transform controls.
        items.first.click()
        page.wait_for_timeout(200)
        assert not page.locator("#d3selTransform").is_hidden(), \
            "per-item transform panel should show when an item is selected"

        # Move it along X via the position input; no error expected.
        page.fill("#posX", "120")
        page.locator("#posX").dispatch_event("input")
        page.wait_for_timeout(150)

        # A scene with items enables STL export.
        assert page.locator("#d3stl").is_enabled(), \
            "STL export should be enabled once the scene has items"

        # Clearing the scene empties the list and disables export again.
        page.click("#d3clearScene")
        page.wait_for_timeout(200)
        assert page.locator("#d3scene li").count() == 0 or \
            "No items yet" in (page.locator("#d3scene").text_content() or ""), \
            "scene should be empty after Clear scene"
        assert not page.locator("#d3stl").is_enabled(), \
            "STL export should be disabled when the scene is empty"

        browser.close()

    noise = lambda t: "favicon" in t.lower()
    real_console = [e for e in console_errors if not noise(e)]
    real_failed = [u for u in failed_requests if "favicon" not in u.lower()]
    assert not page_errors, "uncaught JS errors:\n" + "\n".join(page_errors)
    assert not real_console, "console errors:\n" + "\n".join(real_console)
    assert not real_failed, "failed asset requests:\n" + "\n".join(real_failed)
