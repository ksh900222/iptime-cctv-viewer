#!/usr/bin/env python3
"""Stamp AppIcon.icns onto files via NSWorkspace + classic resource-fork (Rez/SetFile)."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from AppKit import NSImage, NSWorkspace

ICONS = Path(__file__).resolve().parent
DEFAULT_ICNS = ICONS / "AppIcon.icns"


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def set_icon(target: Path, icns: Path) -> None:
    target = target.resolve()
    icns = icns.resolve()
    if not target.exists():
        raise SystemExit(f"missing target: {target}")

    image = NSImage.alloc().initWithContentsOfFile_(str(icns))
    if image is None:
        raise SystemExit(f"could not load icns: {icns}")
    NSWorkspace.sharedWorkspace().setIcon_forFile_options_(image, str(target), 0)

    # Resource-fork stamp — more reliable for loose .command files in Finder.
    if target.is_file() and target.suffix == ".command":
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            stamped = td_path / "icon.icns"
            shutil.copy2(icns, stamped)
            _run(["sips", "-i", str(stamped)])
            rsrc = td_path / "icon.rsrc"
            with rsrc.open("w") as out:
                subprocess.run(
                    ["DeRez", "-only", "icns", str(stamped)],
                    check=True,
                    stdout=out,
                )
            _run(["Rez", "-append", str(rsrc), "-o", str(target)])
        _run(["SetFile", "-a", "C", str(target)])

    print(f"icon applied: {target}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"usage: {argv[0]} <file-or-app> [more...]", file=sys.stderr)
        return 2
    icns = DEFAULT_ICNS
    if not icns.is_file():
        raise SystemExit(f"missing {icns} — run generate_icon.py first")
    for raw in argv[1:]:
        set_icon(Path(raw), icns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
