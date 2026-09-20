"""Offscreen Viewer: PTZ pad/keys remap with current _rotation_deg."""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class ViewerPtzRemapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app import Viewer

        self.win = Viewer()
        self.win._start_timer.stop()
        self.sent: list[str] = []
        self.win.ptz.move = lambda d: self.sent.append(d)
        self.win.ptz.stop = lambda: self.sent.append("STOP")

    def tearDown(self) -> None:
        self.win._hold_cap.stop()
        self.win._nudge_timer.stop()
        self.win._start_timer.stop()
        self.win.close()
        self.win.deleteLater()

    def test_press_remaps_camera_dir_held_stays_visual(self) -> None:
        self.win._rotation_deg = 90
        self.win._ptz_press("UP")
        self.assertEqual(self.sent, ["LEFT"])
        self.assertEqual(self.win._ptz_held, "UP")
        self.win._ptz_release("UP")
        self.assertIsNone(self.win._ptz_held)

    def test_identity_at_zero_and_force_stop_unmapped(self) -> None:
        self.win._rotation_deg = 180
        self.win._ptz_press("LEFT")
        self.assertEqual(self.sent, ["RIGHT"])
        self.win._ptz_force_stop()
        self.assertEqual(self.sent[-1], "STOP")
        self.assertIsNone(self.win._ptz_held)

    def test_keyboard_uses_same_remap(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QKeyEvent

        self.win._rotation_deg = 270
        press = QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier
        )
        self.win.keyPressEvent(press)
        self.assertEqual(self.sent, ["LEFT"])
        self.assertEqual(self.win._ptz_held, "DOWN")


if __name__ == "__main__":
    unittest.main()
