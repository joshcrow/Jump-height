#!/usr/bin/env python3
"""Draw the JumpHeight icon: a jump's arc over dark water, the puck at the apex.

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


def _wing(cx: float, cy: float, span: float, n: int = 160):
    """A wing-foil wing seen head-on: a broad crescent, tips swept down,
    a short strut under the centre. Upper edge = arc of a big circle,
    lower edge = arc of a smaller one; the two meet at the tips."""
    pts = []
    for i in range(n + 1):                        # leading edge, left to right
        t = -1 + 2 * i / n
        x = cx + t * span / 2
        y = cy - (1 - t * t) * span * 0.34 + abs(t) ** 3 * span * 0.10
        pts.append((x, y))
    for i in range(n, -1, -1):                    # trailing edge, right to left
        t = -1 + 2 * i / n
        x = cx + t * span / 2
        y = cy - (1 - t * t) * span * 0.16 + abs(t) ** 3 * span * 0.10
        pts.append((x, y))
    return pts


def draw_app_icon(size: int = 1024) -> Image.Image:
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
    cx = s / 2
    # the wing, high in the frame
    d.polygon(_wing(cx, box[1] + w * 0.46, w * 0.74), fill=ARC)
    # centre strut
    d.rounded_rectangle((cx - w * 0.018, box[1] + w * 0.30, cx + w * 0.018, box[1] + w * 0.44),
                         radius=int(w * 0.018), fill=ARC)
    # the water, and the rider's arc: a small accent dot at its apex
    base_y = box[3] - w * 0.16
    d.line([(box[0] + w * 0.14, base_y), (box[2] - w * 0.14, base_y)], fill=(255, 255, 255, 120), width=int(s * 0.02))
    r = s * 0.04
    d.ellipse((cx - r, base_y - w * 0.22 - r, cx + r, base_y - w * 0.22 + r), fill=PUCK)
    return img


def draw_menubar_icon(size: int) -> Image.Image:
    """The wing alone, black on transparent (a template image)."""
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon(_wing(s / 2, s * 0.62, s * 0.92), fill=(0, 0, 0, 255))
    d.rectangle((s / 2 - max(1, s * 0.04), s * 0.45, s / 2 + max(1, s * 0.04), s * 0.62), fill=(0, 0, 0, 255))
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
    draw_menubar_icon(18).save(ASSETS / "menubar.png")
    draw_menubar_icon(36).save(ASSETS / "menubar@2x.png")
    print("wrote", out, "and", ASSETS / "menubar.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
