"""Zoom bounds, pan clamp, reset, and rotation remapping (no live camera)."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from zoom import (
    ZOOM_MAX,
    ZOOM_MIN,
    ZOOM_STEP,
    clamp_pan,
    clamp_zoom,
    label_to_oriented,
    oriented_size,
    oriented_to_source,
    pan_by,
    remap_pan_for_rotation,
    reset_view,
    source_to_oriented,
    view_rect,
    view_rect_int,
    zoom_at,
)

SRC_W, SRC_H = 1280.0, 720.0


def _inside(x: float, y: float, w: float, h: float, src_w: float, src_h: float) -> bool:
    return x >= -1e-9 and y >= -1e-9 and x + w <= src_w + 1e-9 and y + h <= src_h + 1e-9


class ZoomBoundsTests(unittest.TestCase):
    def test_clamp_zoom_floor_and_ceiling(self) -> None:
        self.assertEqual(clamp_zoom(0.1), ZOOM_MIN)
        self.assertEqual(clamp_zoom(0.0), ZOOM_MIN)
        self.assertEqual(clamp_zoom(-4.0), ZOOM_MIN)
        self.assertEqual(clamp_zoom(99.0), ZOOM_MAX)
        self.assertEqual(clamp_zoom(ZOOM_MAX + 0.01), ZOOM_MAX)

    def test_clamp_zoom_passthrough(self) -> None:
        self.assertEqual(clamp_zoom(1.0), 1.0)
        self.assertEqual(clamp_zoom(2.5), 2.5)
        self.assertEqual(clamp_zoom(ZOOM_MAX), ZOOM_MAX)

    def test_repeated_steps_never_leave_bounds(self) -> None:
        z = 1.0
        for _ in range(40):
            z = clamp_zoom(z * ZOOM_STEP)
        self.assertLessEqual(z, ZOOM_MAX)
        self.assertGreaterEqual(z, ZOOM_MIN)
        for _ in range(40):
            z = clamp_zoom(z / ZOOM_STEP)
        self.assertEqual(z, ZOOM_MIN)


class PanClampTests(unittest.TestCase):
    def test_zoom_1_forces_zero_pan(self) -> None:
        self.assertEqual(clamp_pan(400.0, -200.0, SRC_W, SRC_H, 1.0), (0.0, 0.0))
        self.assertEqual(clamp_pan(0.0, 0.0, SRC_W, SRC_H, 1.0), (0.0, 0.0))

    def test_zoom_2_max_offset_is_quarter(self) -> None:
        px, py = clamp_pan(10_000.0, -10_000.0, SRC_W, SRC_H, 2.0)
        self.assertAlmostEqual(px, SRC_W / 4.0)
        self.assertAlmostEqual(py, -SRC_H / 4.0)

    def test_view_rect_always_inside_image(self) -> None:
        for z in (1.0, 1.25, 2.0, 3.5, 8.0, 99.0):
            for raw in ((0, 0), (9999, -9999), (-400, 250), (SRC_W, SRC_H)):
                x, y, w, h = view_rect(SRC_W, SRC_H, z, raw[0], raw[1])
                self.assertTrue(_inside(x, y, w, h, SRC_W, SRC_H), (z, raw, x, y, w, h))

    def test_view_rect_int_stays_in_pixel_bounds(self) -> None:
        for z in (1.0, 2.0, 8.0):
            x, y, w, h = view_rect_int(1280, 720, z, 9999.0, -9999.0)
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + w, 1280)
            self.assertLessEqual(y + h, 720)
            self.assertGreaterEqual(w, 1)
            self.assertGreaterEqual(h, 1)

    def test_pan_by_clamps(self) -> None:
        px, py = pan_by(0.0, 0.0, SRC_W, SRC_H, 2.0, 10_000.0, 10_000.0)
        self.assertAlmostEqual(px, -SRC_W / 4.0)
        self.assertAlmostEqual(py, -SRC_H / 4.0)


class ResetTests(unittest.TestCase):
    def test_reset_view_is_1x_centered(self) -> None:
        self.assertEqual(reset_view(), (1.0, 0.0, 0.0))

    def test_reset_after_extreme_state(self) -> None:
        z, px, py = reset_view()
        px, py = clamp_pan(px, py, SRC_W, SRC_H, z)
        self.assertEqual((z, px, py), (1.0, 0.0, 0.0))
        x, y, w, h = view_rect(SRC_W, SRC_H, z, px, py)
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, 0.0)
        self.assertAlmostEqual(w, SRC_W)
        self.assertAlmostEqual(h, SRC_H)


class ZoomAtTests(unittest.TestCase):
    def test_center_zoom_keeps_center(self) -> None:
        z, px, py = zoom_at(1.0, 0.0, 0.0, SRC_W, SRC_H, 2.0, SRC_W / 2.0, SRC_H / 2.0)
        self.assertEqual(z, 2.0)
        self.assertAlmostEqual(px, 0.0)
        self.assertAlmostEqual(py, 0.0)

    def test_anchor_stays_in_same_view_fraction(self) -> None:
        # Zoom on a point in the upper-left quadrant.
        ax, ay = 320.0, 180.0
        z, px, py = zoom_at(1.0, 0.0, 0.0, SRC_W, SRC_H, 2.0, ax, ay)
        vx, vy, vw, vh = view_rect(SRC_W, SRC_H, z, px, py)
        self.assertAlmostEqual((ax - vx) / vw, 320.0 / SRC_W, places=6)
        self.assertAlmostEqual((ay - vy) / vh, 180.0 / SRC_H, places=6)

    def test_zoom_at_respects_bounds(self) -> None:
        z, px, py = zoom_at(1.0, 0.0, 0.0, SRC_W, SRC_H, 50.0, 10.0, 10.0)
        self.assertEqual(z, ZOOM_MAX)
        x, y, w, h = view_rect(SRC_W, SRC_H, z, px, py)
        self.assertTrue(_inside(x, y, w, h, SRC_W, SRC_H))


class RotationComposeTests(unittest.TestCase):
    def test_oriented_size_swaps_on_90_270(self) -> None:
        self.assertEqual(oriented_size(1280, 720, 0), (1280.0, 720.0))
        self.assertEqual(oriented_size(1280, 720, 90), (720.0, 1280.0))
        self.assertEqual(oriented_size(1280, 720, 180), (1280.0, 720.0))
        self.assertEqual(oriented_size(1280, 720, 270), (720.0, 1280.0))

    def test_source_oriented_roundtrip(self) -> None:
        sx, sy = 400.0, 200.0
        for rot in (0, 90, 180, 270):
            ox, oy = source_to_oriented(sx, sy, SRC_W, SRC_H, rot)
            back = oriented_to_source(ox, oy, SRC_W, SRC_H, rot)
            self.assertAlmostEqual(back[0], sx)
            self.assertAlmostEqual(back[1], sy)

    def test_remap_pan_keeps_source_center(self) -> None:
        # View center offset in unrotated space: look toward the right.
        z = 2.0
        pan_x, pan_y = clamp_pan(200.0, 40.0, SRC_W, SRC_H, z)
        ow, oh = oriented_size(SRC_W, SRC_H, 0)
        cx, cy = ow / 2.0 + pan_x, oh / 2.0 + pan_y
        src = oriented_to_source(cx, cy, SRC_W, SRC_H, 0)
        npx, npy = remap_pan_for_rotation(pan_x, pan_y, SRC_W, SRC_H, 0, 90, z)
        nw, nh = oriented_size(SRC_W, SRC_H, 90)
        ncx, ncy = nw / 2.0 + npx, nh / 2.0 + npy
        src2 = oriented_to_source(ncx, ncy, SRC_W, SRC_H, 90)
        self.assertAlmostEqual(src[0], src2[0], places=6)
        self.assertAlmostEqual(src[1], src2[1], places=6)
        x, y, w, h = view_rect(nw, nh, z, npx, npy)
        self.assertTrue(_inside(x, y, w, h, nw, nh))

    def test_four_90_steps_return_same_pan(self) -> None:
        z = 2.0
        px, py = 120.0, -30.0
        px, py = clamp_pan(px, py, SRC_W, SRC_H, z)
        orig = (px, py)
        rot = 0
        for _ in range(4):
            nxt = (rot + 90) % 360
            px, py = remap_pan_for_rotation(px, py, SRC_W, SRC_H, rot, nxt, z)
            rot = nxt
        self.assertAlmostEqual(px, orig[0], places=5)
        self.assertAlmostEqual(py, orig[1], places=5)


class LabelMappingTests(unittest.TestCase):
    def test_label_center_maps_to_view_center(self) -> None:
        ax, ay = label_to_oriented(
            640.0, 360.0, SRC_W, SRC_H, 1280.0, 720.0, 2.0, 0.0, 0.0
        )
        self.assertAlmostEqual(ax, SRC_W / 2.0)
        self.assertAlmostEqual(ay, SRC_H / 2.0)

    def test_letterbox_maps_to_center(self) -> None:
        # 16:9 image in a square label → horizontal letterbox is empty.
        ax, ay = label_to_oriented(10.0, 10.0, SRC_W, SRC_H, 720.0, 720.0, 1.0, 0.0, 0.0)
        self.assertAlmostEqual(ax, SRC_W / 2.0)
        self.assertAlmostEqual(ay, SRC_H / 2.0)


class OffscreenPaintTests(unittest.TestCase):
    """Crop + scale on a dummy QImage; requires PyQt6 offscreen plugin."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_crop_matches_view_rect_int(self) -> None:
        from PyQt6.QtGui import QImage, QColor

        img = QImage(1280, 720, QImage.Format.Format_RGB888)
        img.fill(QColor(10, 20, 30))
        z, px, py = 2.0, 100.0, -50.0
        px, py = clamp_pan(px, py, 1280, 720, z)
        x, y, w, h = view_rect_int(1280, 720, z, px, py)
        crop = img.copy(x, y, w, h)
        self.assertFalse(crop.isNull())
        self.assertEqual(crop.width(), w)
        self.assertEqual(crop.height(), h)
        self.assertLessEqual(w, 1280)
        self.assertLessEqual(h, 720)

    def test_reset_then_paint_uses_full_frame(self) -> None:
        from PyQt6.QtGui import QImage, QColor

        img = QImage(1280, 720, QImage.Format.Format_RGB888)
        img.fill(QColor(1, 2, 3))
        z, px, py = reset_view()
        x, y, w, h = view_rect_int(1280, 720, z, px, py)
        self.assertEqual((x, y, w, h), (0, 0, 1280, 720))
        self.assertEqual(img.copy(x, y, w, h).size(), img.size())


if __name__ == "__main__":
    unittest.main()
