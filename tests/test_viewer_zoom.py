"""Offscreen Viewer checks for zoom buttons, bounds, reset, and rotation compose."""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class ViewerZoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app import Viewer
        from PyQt6.QtGui import QColor, QImage

        self.win = Viewer()
        self.win._start_timer.stop()
        img = QImage(1280, 720, QImage.Format.Format_RGB888)
        img.fill(QColor(8, 16, 24))
        self.win._last_image = img
        self.win._paint_frame()

    def tearDown(self) -> None:
        self.win._start_timer.stop()
        self.win.close()
        self.win.deleteLater()

    def test_buttons_have_korean_labels(self) -> None:
        self.assertEqual(self.win.btn_zoom_in.text(), "+")
        self.assertEqual(self.win.btn_zoom_out.text(), "−")
        self.assertEqual(self.win.btn_zoom_reset.text(), "원본(1x)")

    def test_zoom_in_button_bounded(self) -> None:
        from zoom import ZOOM_MAX

        for _ in range(30):
            self.win.btn_zoom_in.click()
        self.assertEqual(self.win._zoom, ZOOM_MAX)
        self.assertEqual(self.win.zoom_label.text(), f"{ZOOM_MAX:.1f}x")

    def test_zoom_out_and_reset(self) -> None:
        from zoom import ZOOM_MIN

        self.win.btn_zoom_in.click()
        self.win.btn_zoom_in.click()
        self.assertGreater(self.win._zoom, ZOOM_MIN)
        self.win.btn_zoom_reset.click()
        self.assertEqual(self.win._zoom, ZOOM_MIN)
        self.assertEqual(self.win._pan_x, 0.0)
        self.assertEqual(self.win._pan_y, 0.0)
        self.assertEqual(self.win.zoom_label.text(), "1.0x")
        for _ in range(20):
            self.win.btn_zoom_out.click()
        self.assertEqual(self.win._zoom, ZOOM_MIN)

    def test_pan_clamped_after_paint(self) -> None:
        self.win._zoom = 2.0
        self.win._pan_x = 50_000.0
        self.win._pan_y = -50_000.0
        self.win._paint_frame()
        self.assertLessEqual(self.win._pan_x, 1280 / 4.0 + 1e-6)
        self.assertGreaterEqual(self.win._pan_y, -720 / 4.0 - 1e-6)

    def test_rotate_while_zoomed_still_paints(self) -> None:
        self.win._bump_zoom(2.0)
        self.assertGreater(self.win._zoom, 1.0)
        self.win._rotate_view(90)
        self.assertEqual(self.win._rotation_deg, 90)
        pix = self.win.video_label.pixmap()
        self.assertIsNotNone(pix)
        self.assertFalse(pix.isNull())
        self.win._reset_zoom()
        self.assertEqual(self.win._zoom, 1.0)


if __name__ == "__main__":
    unittest.main()
