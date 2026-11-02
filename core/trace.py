"""Image -> vector-path tracing (M2).

Pure-Python engine code: Pillow for image I/O, scikit-image for contour/edge
extraction. Optional `rembg` background removal is imported lazily and is NOT a
core dependency.

Usage:
    from core.trace import trace_from_bytes
    paths = trace_from_bytes(png_bytes, mode="outline", threshold=128)
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageOps

from .document import path

_WHITE = (255, 255, 255, 255)


def _load_rgb(data: bytes, remove_bg: bool) -> Image.Image:
    """Decode an image, optionally remove its background, and flatten to RGB."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)

    if remove_bg:
        try:
            from rembg import remove  # lazy, optional dependency
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Background removal needs rembg. Install with: "
                'pip install "rembg[cpu]"'
            ) from exc
        out = remove(img)
        img = out if isinstance(out, Image.Image) else Image.open(io.BytesIO(out))

    # flatten transparency onto white so contours are clean
    if img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    ):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, _WHITE)
        img = Image.alpha_composite(bg, img)
    return img.convert("RGB")


def _mask(gray: np.ndarray, mode: str, threshold: int, invert: bool) -> np.ndarray:
    """Build a 0/1 foreground mask for contour finding."""
    if mode == "edges":
        from skimage.feature import canny

        m = canny(gray / 255.0, sigma=1.2)
    else:  # outline / silhouette: dark pixels are the subject on a light bg
        m = gray < float(threshold)
    if invert:
        m = ~m
    return m.astype(np.float32)


def _poly_area(coords: np.ndarray) -> float:
    """Shoelace area of a (row, col) polygon."""
    x, y = coords[:, 1], coords[:, 0]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _smooth_d(pts: np.ndarray, closed: bool) -> str:
    """Catmull-Rom spline through `pts` as an SVG cubic-Bézier path.

    Turns the faceted polylines from marching squares into soft curves, which
    look much better for logos and line art.
    """
    n = len(pts)
    if n < 3:
        return ""
    # find_contours can repeat the first point at the end of a closed loop;
    # drop it so the spline doesn't get a zero-length segment.
    if closed and n > 3 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
        n -= 1
    last = n - 1

    def point(i: int) -> np.ndarray:
        if closed:
            return pts[i % n]
        return pts[max(0, min(last, i))]

    d = [f"M {pts[0, 0]:.2f},{pts[0, 1]:.2f}"]
    segments = n if closed else n - 1
    for i in range(segments):
        p0 = point(i - 1)
        p1 = point(i)
        p2 = point(i + 1)
        p3 = point(i + 2)
        c1 = p1 + (p2 - p0) / 6.0
        c2 = p2 - (p3 - p1) / 6.0
        d.append(
            f"C {c1[0]:.2f},{c1[1]:.2f} {c2[0]:.2f},{c2[1]:.2f} "
            f"{p2[0]:.2f},{p2[1]:.2f}"
        )
    if closed:
        d.append("Z")
    return " ".join(d)


def trace_from_bytes(
    data: bytes,
    *,
    mode: str = "outline",
    threshold: int = 128,
    invert: bool = False,
    simplify: float = 1.5,
    min_area: float = 10.0,
    smooth: bool = False,
    remove_bg: bool = False,
    max_width: int = 900,
    max_height: int = 700,
    margin: float = 20.0,
) -> list[dict]:
    """Trace an image into canonical `path` objects fitted to a canvas box."""
    from skimage.measure import approximate_polygon, find_contours

    img = _load_rgb(data, remove_bg)
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    mask = _mask(gray, mode, threshold, invert)

    raw = [c for c in find_contours(mask, 0.5) if len(c) >= 3]
    if not raw:
        return []

    # simplify and drop specks
    polys = []
    for c in raw:
        approx = approximate_polygon(c, tolerance=simplify)
        if len(approx) < 3:
            continue
        if mode != "edges" and _poly_area(approx) < min_area:
            continue
        polys.append(approx)
    if not polys:
        return []

    # fit every contour into the target box, preserving aspect ratio
    xs = np.concatenate([p[:, 1] for p in polys])
    ys = np.concatenate([p[:, 0] for p in polys])
    minx, maxx = float(xs.min()), float(xs.max())
    miny, maxy = float(ys.min()), float(ys.max())
    w = max(maxx - minx, 1.0)
    h = max(maxy - miny, 1.0)
    avail_w = max(max_width - 2 * margin, 1.0)
    avail_h = max(max_height - 2 * margin, 1.0)
    scale = min(avail_w / w, avail_h / h)
    ox = (max_width - w * scale) / 2 - minx * scale
    oy = (max_height - h * scale) / 2 - miny * scale

    closed = mode != "edges"
    out: list[dict] = []
    for p in polys:
        px = p[:, 1] * scale + ox
        py = p[:, 0] * scale + oy
        pts = np.column_stack([px, py])
        if smooth:
            d = _smooth_d(pts, closed=closed)
        else:
            body = " L ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
            d = f"M {body}" + (" Z" if closed else "")
        if not d:
            continue
        out.append(
            path(
                0,
                0,
                d,
                fill=None,
                stroke="#222222",
                strokeWidth=1.5,
                meta={"traced": True, "mode": mode, "smooth": smooth},
            )
        )
    return out
