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
