"""PTZ visual-direction remapping after digital rotation (no live camera)."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ptz import remap_ptz_dir

# visual | n=0 | n=1 (+90 CW) | n=2 (+180) | n=3 (+270 CW)
EXPECTED = {
    0: {"UP": "UP", "RIGHT": "RIGHT", "DOWN": "DOWN", "LEFT": "LEFT"},
    90: {"UP": "LEFT", "RIGHT": "UP", "DOWN": "RIGHT", "LEFT": "DOWN"},
    180: {"UP": "DOWN", "RIGHT": "LEFT", "DOWN": "UP", "LEFT": "RIGHT"},
    270: {"UP": "RIGHT", "RIGHT": "DOWN", "DOWN": "LEFT", "LEFT": "UP"},
}

VISUAL_DIRS = ("UP", "RIGHT", "DOWN", "LEFT")


class RemapPtzDirTests(unittest.TestCase):
    def test_all_4x4_visual_to_camera(self) -> None:
        for rot, row in EXPECTED.items():
            for visual, cam in row.items():
                with self.subTest(rotation_deg=rot, visual=visual):
                    self.assertEqual(remap_ptz_dir(visual, rot), cam)

    def test_equivalent_angles_use_modulo(self) -> None:
        equivalents = {
            0: (0, 360, 720, -360),
            90: (90, 450, -270),
            180: (180, 540, -180),
            270: (270, 630, -90),
        }
        for base, angles in equivalents.items():
            for rot in angles:
                for visual in VISUAL_DIRS:
                    with self.subTest(base=base, rotation_deg=rot, visual=visual):
                        self.assertEqual(
                            remap_ptz_dir(visual, rot),
                            EXPECTED[base][visual],
                        )

    def test_stop_and_unknown_pass_through(self) -> None:
        for rot in (0, 90, 180, 270, 450, -90):
            self.assertEqual(remap_ptz_dir("STOP", rot), "STOP")
            self.assertEqual(remap_ptz_dir("stop", rot), "STOP")
        self.assertEqual(remap_ptz_dir("ZOOM", 90), "ZOOM")

    def test_lowercase_visual_dirs(self) -> None:
        self.assertEqual(remap_ptz_dir("up", 90), "LEFT")
        self.assertEqual(remap_ptz_dir(" right ", 180), "LEFT")


if __name__ == "__main__":
    unittest.main()
