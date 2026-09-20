"""Display-path digital zoom/pan math. No Qt, no ffmpeg.

Pan is the offset of the view center from the image center, in source pixels
of the already-rotated frame. Zoom is a bounded scale factor (1.0 … 8.0).
"""

from __future__ import annotations

ZOOM_MIN = 1.0
ZOOM_MAX = 8.0
ZOOM_STEP = 1.25


def clamp_zoom(zoom: float) -> float:
    if zoom < ZOOM_MIN:
        return ZOOM_MIN
    if zoom > ZOOM_MAX:
        return ZOOM_MAX
    return float(zoom)


def clamp_pan(
    pan_x: float,
    pan_y: float,
    src_w: float,
    src_h: float,
    zoom: float,
) -> tuple[float, float]:
    """Keep the view rectangle inside the source image.

    At zoom 1.0 the only valid pan is (0, 0).
    """
    z = clamp_zoom(zoom)
    if src_w <= 0 or src_h <= 0 or z <= 1.0:
        return 0.0, 0.0
    max_x = max(0.0, (src_w - src_w / z) / 2.0)
    max_y = max(0.0, (src_h - src_h / z) / 2.0)
    return (
        min(max_x, max(-max_x, float(pan_x))),
        min(max_y, max(-max_y, float(pan_y))),
    )


def view_rect(
    src_w: float,
    src_h: float,
    zoom: float,
    pan_x: float,
    pan_y: float,
) -> tuple[float, float, float, float]:
    """Visible rectangle (x, y, w, h) in source pixels."""
    z = clamp_zoom(zoom)
    px, py = clamp_pan(pan_x, pan_y, src_w, src_h, z)
    w = src_w / z
    h = src_h / z
    x = src_w / 2.0 + px - w / 2.0
    y = src_h / 2.0 + py - h / 2.0
    return x, y, w, h


def view_rect_int(
    src_w: int,
    src_h: int,
    zoom: float,
    pan_x: float,
    pan_y: float,
) -> tuple[int, int, int, int]:
    x, y, w, h = view_rect(src_w, src_h, zoom, pan_x, pan_y)
    ix = max(0, min(src_w - 1, int(round(x))))
    iy = max(0, min(src_h - 1, int(round(y))))
    iw = max(1, min(src_w - ix, int(round(w))))
    ih = max(1, min(src_h - iy, int(round(h))))
    return ix, iy, iw, ih


def zoom_at(
    zoom: float,
    pan_x: float,
    pan_y: float,
    src_w: float,
    src_h: float,
    new_zoom: float,
    anchor_x: float,
    anchor_y: float,
) -> tuple[float, float, float]:
    """Zoom so (anchor_x, anchor_y) stays at the same relative place in the view."""
    new_zoom = clamp_zoom(new_zoom)
    vx, vy, vw, vh = view_rect(src_w, src_h, zoom, pan_x, pan_y)
    if vw <= 0 or vh <= 0:
        return new_zoom, 0.0, 0.0
    fx = (anchor_x - vx) / vw
    fy = (anchor_y - vy) / vh
    nvw = src_w / new_zoom
    nvh = src_h / new_zoom
    nvx = anchor_x - fx * nvw
    nvy = anchor_y - fy * nvh
    npx = nvx + nvw / 2.0 - src_w / 2.0
    npy = nvy + nvh / 2.0 - src_h / 2.0
    npx, npy = clamp_pan(npx, npy, src_w, src_h, new_zoom)
    return new_zoom, npx, npy


def pan_by(
    pan_x: float,
    pan_y: float,
    src_w: float,
    src_h: float,
    zoom: float,
    dx_src: float,
    dy_src: float,
) -> tuple[float, float]:
    """Move the view opposite a grab-drag, in source pixels."""
    return clamp_pan(pan_x - dx_src, pan_y - dy_src, src_w, src_h, zoom)


def reset_view() -> tuple[float, float, float]:
    return ZOOM_MIN, 0.0, 0.0


def oriented_size(src_w: float, src_h: float, rotation_deg: int) -> tuple[float, float]:
    if rotation_deg % 360 in (90, 270):
        return float(src_h), float(src_w)
    return float(src_w), float(src_h)


def source_to_oriented(
    sx: float, sy: float, src_w: float, src_h: float, rotation_deg: int
) -> tuple[float, float]:
    """Map a point in the unrotated image to Qt's clockwise, y-down rotation."""
    rot = rotation_deg % 360
    if rot == 90:
        return src_h - sy, sx
    if rot == 180:
        return src_w - sx, src_h - sy
    if rot == 270:
        return sy, src_w - sx
    return sx, sy


def oriented_to_source(
    x: float, y: float, src_w: float, src_h: float, rotation_deg: int
) -> tuple[float, float]:
    rot = rotation_deg % 360
    if rot == 90:
        return y, src_h - x
    if rot == 180:
        return src_w - x, src_h - y
    if rot == 270:
        return src_w - y, x
    return x, y


def remap_pan_for_rotation(
    pan_x: float,
    pan_y: float,
    src_w: float,
    src_h: float,
    old_rot: int,
    new_rot: int,
    zoom: float,
) -> tuple[float, float]:
    """Keep the same source content under the view center after a 90° step."""
    ow, oh = oriented_size(src_w, src_h, old_rot)
    cx = ow / 2.0 + pan_x
    cy = oh / 2.0 + pan_y
    sx, sy = oriented_to_source(cx, cy, src_w, src_h, old_rot)
    nx, ny = source_to_oriented(sx, sy, src_w, src_h, new_rot)
    nw, nh = oriented_size(src_w, src_h, new_rot)
    return clamp_pan(nx - nw / 2.0, ny - nh / 2.0, nw, nh, zoom)


def fitted_size(
    src_w: float, src_h: float, label_w: float, label_h: float
) -> tuple[float, float]:
    if src_w <= 0 or src_h <= 0 or label_w <= 0 or label_h <= 0:
        return 0.0, 0.0
    scale = min(label_w / src_w, label_h / src_h)
    return src_w * scale, src_h * scale


def label_to_oriented(
    lx: float,
    ly: float,
    src_w: float,
    src_h: float,
    label_w: float,
    label_h: float,
    zoom: float,
    pan_x: float,
    pan_y: float,
) -> tuple[float, float]:
    """Map a QLabel point to oriented-image pixels of the current view.

    Points in the letterbox (outside the fitted pixmap) map to the view center.
    """
    fw, fh = fitted_size(src_w, src_h, label_w, label_h)
    vx, vy, vw, vh = view_rect(src_w, src_h, zoom, pan_x, pan_y)
    if fw <= 0 or fh <= 0:
        return vx + vw / 2.0, vy + vh / 2.0
    fx = (lx - (label_w - fw) / 2.0) / fw
    fy = (ly - (label_h - fh) / 2.0) / fh
    if fx < 0.0 or fx > 1.0 or fy < 0.0 or fy > 1.0:
        return vx + vw / 2.0, vy + vh / 2.0
    return vx + fx * vw, vy + fy * vh


def label_delta_to_source(
    dx_label: float,
    dy_label: float,
    src_w: float,
    src_h: float,
    label_w: float,
    label_h: float,
    zoom: float,
) -> tuple[float, float]:
    fw, fh = fitted_size(src_w, src_h, label_w, label_h)
    if fw <= 0 or fh <= 0:
        return 0.0, 0.0
    z = clamp_zoom(zoom)
    return dx_label * ((src_w / z) / fw), dy_label * ((src_h / z) / fh)
