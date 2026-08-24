#!/usr/bin/env python3
"""Generate the Connect IQ store listing images.

Why these are DRAWN and not screenshotted: the simulator has no BLE, so a sim
screenshot of this field can only ever show "finding puck" — the one state the
listing should not lead with. These renders reproduce the REAL layouts from
JumpFieldView.mc (the full layout's header/big/footer rows, the half layout's
^best/n/dot row, and the connected-id header that replaced the search name),
using the numbers the OG actually measured on the bench. Regenerate with:

    python3 garmin/store-assets/make_assets.py

Store constraints (form, 2026-08-24): cover 500x500 JPG/GIF/PNG < 300 KB;
screen images < 150 KB.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent
S = 2  # supersample factor


def font(size: int, bold: bool = False):
    for name in (("Helvetica.ttc", 1 if bold else 0),
                 ("HelveticaNeue.ttc", 1 if bold else 0)):
        try:
            return ImageFont.truetype(f"/System/Library/Fonts/{name[0]}",
                                      size * S, index=name[1])
        except OSError:
            continue
    return ImageFont.load_default()


def center(d, xy, text, f, fill):
    d.text((xy[0] * S, xy[1] * S), text, font=f, fill=fill, anchor="mm")


def save(img, name, limit_kb):
    img = img.resize((img.width // S, img.height // S), Image.LANCZOS)
    p = OUT / name
    img.save(p, optimize=True)
    kb = p.stat().st_size / 1024
    assert kb < limit_kb, f"{name}: {kb:.0f} KB over the {limit_kb} KB limit"
    print(f"  {name}  {img.width}x{img.height}  {kb:.0f} KB")


def watch_face(size, bg, fg, ring=None):
    """A round watch face on a neutral backdrop."""
    img = Image.new("RGB", (size * S, size * S), "#1a1d23")
    d = ImageDraw.Draw(img)
    m = int(size * 0.04) * S
    if ring:
        d.ellipse([m - 6 * S, m - 6 * S, size * S - m + 6 * S,
                   size * S - m + 6 * S], fill=ring)
    d.ellipse([m, m, size * S - m, size * S - m], fill=bg)
    return img, d


def full_field(name, bg, fg, sub, accent, jumps, big, footer, connected=True):
    """JumpFieldView._drawFull: header (dot + id + count), LAST JUMP huge,
    footer row — chord-limited header per the real code."""
    img, d = watch_face(500, bg, fg, ring="#2e3138")
    cx = 250
    # header: state dot + connected id + count (one typographic row)
    dot = accent if connected else bg
    d.ellipse([(cx - 105) * S, 118 * S, (cx - 105 + 13) * S, 131 * S],
              outline=accent, width=2 * S, fill=dot)
    center(d, (cx - 55, 124), "E2C4", font(24), sub)
    center(d, (cx + 62, 124), jumps, font(24), sub)
    # LAST JUMP — largest font that fits
    center(d, (cx, 240), big, font(94, bold=True), fg)
    # footer
    center(d, (cx, 352), footer, font(26), sub)
    return img


def main():
    OUT.mkdir(exist_ok=True)

    # ---- cover, 500x500 ------------------------------------------------
    img = Image.new("RGB", (500 * S, 500 * S), "#0d2137")
    d = ImageDraw.Draw(img)
    # ballistic arc — the measurement itself
    pts = []
    for i in range(101):
        x = 60 + 3.8 * i
        t = i / 100.0
        y = 330 - 190 * (4 * t * (1 - t))  # symmetric parabola
        pts.append((x * S, y * S))
    d.line(pts, fill="#3fa7ff", width=7 * S)
    d.ellipse([(60 - 9) * S, (330 - 9) * S, (60 + 9) * S, (330 + 9) * S],
              fill="#3fa7ff")
    d.ellipse([(440 - 9) * S, (330 - 9) * S, (440 + 9) * S, (330 + 9) * S],
              fill="#3fa7ff")
    # apex height marker
    d.line([(250 * S, 140 * S), (250 * S, 330 * S)], fill="#2e4a63",
           width=3 * S)
    center(d, (250, 118), "4.2 ft", font(40, bold=True), "#ffffff")
    center(d, (250, 405), "JUMP HEIGHT", font(52, bold=True), "#ffffff")
    center(d, (250, 448), "wing foil jump tracker", font(24), "#8fb3cf")
    center(d, (250, 480), "REQUIRES CUSTOM BOARD SENSOR", font(17),
           "#e0a33f")
    save(img, "cover.png", 300)

    # ---- screen 1: Epix AMOLED, mid-session ---------------------------
    save(full_field("epix", "#000000", "#ffffff", "#9aa0a8", "#35c46a",
                    "3 jumps", "4.2 ft", "best 5.1 ft · air 1.02s"),
         "screen-epix-full.png", 150)

    # ---- screen 2: Instinct MIP (monochrome) --------------------------
    save(full_field("instinct", "#dfe3da", "#111111", "#3a3f38", "#111111",
                    "5 jumps", "5.0 ft", "best 5.0 ft · air 1.10s"),
         "screen-instinct-full.png", 150)

    # ---- screen 3: honest no-hardware state ---------------------------
    img, d = watch_face(500, "#000000", "#ffffff", ring="#2e3138")
    cx = 250
    d.ellipse([(cx - 122) * S, 118 * S, (cx - 122 + 13) * S, 131 * S],
              outline="#9aa0a8", width=2 * S)
    center(d, (cx - 28, 124), "JumpHeight", font(24), "#9aa0a8")
    center(d, (cx, 240), "finding puck", font(42), "#ffffff")
    center(d, (cx, 352), "requires the board sensor", font(24), "#9aa0a8")
    save(img, "screen-no-sensor.png", 150)


if __name__ == "__main__":
    main()
