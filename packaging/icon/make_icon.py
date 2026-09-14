#!/usr/bin/env python3
"""Draw the JumpHeight icon: an up arrow.

Writes packaging/icon/JumpHeight.icns (via iconutil) and the two menu-bar
template images tools/puckd/assets/menubar.png (18 px) and menubar@2x.png
(36 px) — black on transparent, which macOS recolours for light/dark bars.
Deterministic: same inputs, same bytes. Run: python3 packaging/icon/make_icon.py
"""
from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
ASSETS = REPO / "tools" / "puckd" / "assets"

BG_TOP, BG_BOTTOM = (16, 38, 66), (7, 18, 34)     # deep water
ARC = (255, 255, 255)
PUCK = (86, 214, 255)                               # the one accent


def _arc_points(w: float, h: float, x0: float, x1: float, base_y: float, apex_y: float, n: int = 200):
    """A parabola from (x0, base_y) up to apex_y and back down to (x1, base_y)."""
    pts = []
    for i in range(n + 1):
        t = i / n
        x = x0 + (x1 - x0) * t
        y = base_y - (base_y - apex_y) * (1 - (2 * t - 1) ** 2)
        pts.append((x, y))
    return pts


def _arrow(d: "ImageDraw.ImageDraw", cx: float, cy: float, h: float, ink, weight: float = 0.30):
    """A solid up arrow: a triangular head over a rounded stem, centred on
    (cx, cy) with total height h. `weight` is the stem width as a fraction
    of h. Drawn as one silhouette so it stays solid mass at 16 px."""
    top = cy - h / 2
    bottom = cy + h / 2
    head_h = h * 0.52
    half_w = h * 0.50
    d.polygon([(cx, top), (cx - half_w, top + head_h), (cx + half_w, top + head_h)], fill=ink)
    sw = h * weight
    d.rounded_rectangle((cx - sw / 2, top + head_h * 0.72, cx + sw / 2, bottom),
                        radius=sw * 0.35, fill=ink)


def draw_app_icon(size: int = 1024) -> Image.Image:
    """The app icon: an up arrow on deep water, the accent dot at its tip
    is the puck at the top of the jump."""
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    margin = int(s * 0.10)
    box = (margin, margin, s - margin, s - margin)
    radius = int((s - 2 * margin) * 0.225)
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(box[1], box[3]):
        t = (y - box[1]) / (box[3] - box[1])
        c = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)) + (255,)
        gd.line([(box[0], y), (box[2], y)], fill=c)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    w = box[2] - box[0]
    _arrow(d, s / 2, s / 2 + w * 0.02, w * 0.56, ARC)
    return img


def draw_menubar_icon(size: int, state: str = "idle") -> Image.Image:
    """The arrow alone, black on transparent (a template image: macOS keeps
    only the alpha). State is encoded by redrawing the mark, never a badge,
    and the mark is solid mass so it survives a busy wallpaper under
    Tahoe's transparent bar.

        idle       the arrow                     puck attached, nothing to do
        dormant    the arrow at 35 % opacity     no puck (Apple's own "disabled" opacity)
        working    the arrow over a line         a job is running
        attention  the arrow over a dot          Nick needs to do one thing
    """
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink = (0, 0, 0, 90 if state == "dormant" else 255)
    if state in ("working", "attention"):
        _arrow(d, s / 2, s * 0.40, s * 0.62, ink)
        if state == "working":
            d.rounded_rectangle((s * 0.18, s * 0.82, s * 0.82, s * 0.82 + max(1.5, s * 0.10)),
                                radius=max(1, s * 0.04), fill=ink)
        else:
            r = s * 0.11
            d.ellipse((s / 2 - r, s * 0.86 - r, s / 2 + r, s * 0.86 + r), fill=ink)
    else:
        _arrow(d, s / 2, s * 0.50, s * 0.80, ink)
    return img


def main() -> int:
    master = draw_app_icon(1024)
    (HERE / "JumpHeight-1024.png").write_bytes(b"")  # placeholder so the path exists for a preview
    master.save(HERE / "JumpHeight-1024.png")
    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "JumpHeight.iconset"
        iconset.mkdir()
        for px in (16, 32, 128, 256, 512):
            master.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{px}x{px}.png")
            master.resize((px * 2, px * 2), Image.LANCZOS).save(iconset / f"icon_{px}x{px}@2x.png")
        out = HERE / "JumpHeight.icns"
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    for state in ("idle", "dormant", "working", "attention"):
        suffix = "" if state == "idle" else f"-{state}"
        draw_menubar_icon(18, state).save(ASSETS / f"menubar{suffix}.png")
        draw_menubar_icon(36, state).save(ASSETS / f"menubar{suffix}@2x.png")
    print("wrote", out, "and", ASSETS / "menubar.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
