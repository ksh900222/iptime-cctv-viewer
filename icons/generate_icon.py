#!/usr/bin/env python3
"""Original CCTV launcher icon → PNG sizes + AppIcon.icns."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
MASTER = 2048
OUT_PNG = ROOT / "AppIcon-1024.png"
ICONSET = ROOT / "AppIcon.iconset"
ICNS = ROOT / "AppIcon.icns"

# Dark / tech palette
BG_TOP = (18, 32, 48, 255)
BG_BOT = (8, 14, 22, 255)
RING = (40, 70, 96, 255)
SILVER = (198, 210, 222, 255)
SILVER_DK = (132, 148, 164, 255)
BASE = (56, 72, 90, 255)
DOME = (156, 188, 210, 230)
DOME_DK = (28, 48, 64, 240)
LENS_OUTER = (12, 18, 26, 255)
LENS_RING = (0, 196, 220, 255)
LENS_IN = (6, 10, 16, 255)
LED = (255, 56, 56, 255)
LED_GLOW = (255, 80, 80, 90)
HIGHLIGHT = (255, 255, 255, 70)


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def vertical_gradient(size: int, top, bottom) -> Image.Image:
    strip = Image.new("RGBA", (1, size))
    pix = strip.load()
    for y in range(size):
        pix[0, y] = _lerp(top, bottom, y / (size - 1))
    return strip.resize((size, size), Image.Resampling.BILINEAR)


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return m


def circle(draw: ImageDraw.ImageDraw, cx, cy, r, fill, outline=None, width=1):
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline, width=width)


def add_glow(base: Image.Image, cx, cy, r, color, blur=40) -> Image.Image:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    circle(d, cx, cy, r, color)
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(base, layer)


def draw_camera(img: Image.Image) -> Image.Image:
    """Front-facing PTZ dome camera — readable at 32–128px."""
    s = img.size[0]
    k = s / MASTER
    cx, cy = s * 0.50, s * 0.47

    # Soft cyan glow behind the dome (surveillance / night-IR feel)
    img = add_glow(img, cx, cy, 560 * k, (0, 140, 170, 60), blur=110 * k)
    d = ImageDraw.Draw(img, "RGBA")

    # Wall / desk mount plate
    plate = (
        cx - 280 * k,
        cy + 310 * k,
        cx + 280 * k,
        cy + 520 * k,
    )
    d.rounded_rectangle(plate, radius=int(56 * k), fill=BASE, outline=SILVER_DK, width=max(2, int(8 * k)))

    # Stem
    stem = (cx - 90 * k, cy + 170 * k, cx + 90 * k, cy + 360 * k)
    d.rounded_rectangle(stem, radius=int(36 * k), fill=SILVER_DK, outline=(90, 104, 118, 255), width=max(2, int(6 * k)))

    # Outer dome shell — large enough to read at 32px
    dome_r = 430 * k
    circle(d, cx, cy - 30 * k, dome_r, fill=DOME_DK, outline=SILVER, width=max(3, int(14 * k)))

    # Tinted bubble
    circle(d, cx, cy - 40 * k, dome_r * 0.88, fill=DOME)

    # Inner camera module (the “eye”)
    eye_cx, eye_cy = cx + 22 * k, cy - 10 * k
    circle(d, eye_cx, eye_cy, 210 * k, fill=LENS_OUTER, outline=SILVER_DK, width=max(2, int(10 * k)))
    circle(d, eye_cx, eye_cy, 168 * k, fill=(8, 22, 30, 255), outline=LENS_RING, width=max(3, int(16 * k)))
    circle(d, eye_cx, eye_cy, 122 * k, fill=LENS_IN)
    circle(d, eye_cx, eye_cy, 54 * k, fill=(0, 170, 190, 255))
    circle(d, eye_cx, eye_cy, 28 * k, fill=(4, 8, 12, 255))

    # Specular highlights on glass
    hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hd.ellipse(
        (cx - 320 * k, cy - 360 * k, cx + 60 * k, cy - 40 * k),
        fill=HIGHLIGHT,
    )
    hd.ellipse(
        (eye_cx - 90 * k, eye_cy - 90 * k, eye_cx - 8 * k, eye_cy - 20 * k),
        fill=(255, 255, 255, 170),
    )
    hl = hl.filter(ImageFilter.GaussianBlur(max(1, int(10 * k))))
    img = Image.alpha_composite(img, hl)
    d = ImageDraw.Draw(img, "RGBA")

    # Rec LED on the base plate
    led_x, led_y = cx + 175 * k, cy + 415 * k
    img = add_glow(img, led_x, led_y, 40 * k, LED_GLOW, blur=22 * k)
    d = ImageDraw.Draw(img, "RGBA")
    circle(d, led_x, led_y, 22 * k, fill=LED)
    circle(d, led_x - 5 * k, led_y - 5 * k, 7 * k, fill=(255, 180, 180, 200))

    # IR windows on the plate
    for sign in (-1, 1):
        d.rounded_rectangle(
            (
                cx + sign * 185 * k - 22 * k,
                cy + 370 * k,
                cx + sign * 185 * k + 22 * k,
                cy + 418 * k,
            ),
            radius=int(10 * k),
            fill=(20, 28, 36, 255),
            outline=SILVER_DK,
            width=max(1, int(4 * k)),
        )

    return img


def make_master() -> Image.Image:
    bg = vertical_gradient(MASTER, BG_TOP, BG_BOT)
    # Top-edge sheen
    sheen = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    sd.ellipse((-200, -700, MASTER + 200, MASTER * 0.55), fill=(255, 255, 255, 28))
    bg = Image.alpha_composite(bg, sheen)

    # Inner ring so the plate reads as a device bezel
    d = ImageDraw.Draw(bg)
    pad = 70
    d.rounded_rectangle(
        (pad, pad, MASTER - pad - 1, MASTER - pad - 1),
        radius=360,
        outline=RING,
        width=10,
    )

    cam = draw_camera(bg)

    # macOS-like rounded square (icon grid ~22% corner)
    mask = rounded_mask(MASTER, radius=int(MASTER * 0.223))
    out = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    out.paste(cam, (0, 0), mask)
    return out


ICONSET_SIZES = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]


def write_iconset(master: Image.Image) -> None:
    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir()
    preview = master.resize((1024, 1024), Image.Resampling.LANCZOS)
    preview.save(OUT_PNG, "PNG")
    for px, name in ICONSET_SIZES:
        im = master.resize((px, px), Image.Resampling.LANCZOS)
        im.save(ICONSET / name, "PNG")


def build_icns() -> None:
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)
    if not ICNS.exists() or ICNS.stat().st_size < 1000:
        raise SystemExit(f"iconutil failed to produce {ICNS}")


def main() -> int:
    print("drawing master icon…", file=sys.stderr)
    master = make_master()
    write_iconset(master)
    build_icns()
    print(f"wrote {OUT_PNG}")
    print(f"wrote {ICNS} ({ICNS.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
