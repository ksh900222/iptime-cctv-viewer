# Launcher icon notes

## `.command` vs `.app`

`CCTV뷰어.command` is a **macOS shell script**. Double-clicking it opens Terminal and runs the script. It is **not** a compiled app binary. Finder therefore shows a generic document/script icon unless you stamp a custom Finder icon onto that file. Custom icons on a loose `.command` file often **do not survive** `git clone`, AirDrop, or copy to another volume.

`CCTV뷰어.app` is a real **application bundle** (AppleScript applet from `osacompile`, with a Mach-O `applet` stub). Finder / Dock treat it as an app. The camera icon is `Contents/Resources/applet.icns` (`CFBundleIconFile`). Copying the whole `.app` keeps the icon.

The applet runs `Contents/Resources/launcher.sh`, which `cd`s to the folder that contains `app.py` (the `.app` may sit next to `app.py`, or next to a `viewer/` subdirectory) and starts `python3 app.py`.

**더블클릭: `CCTV뷰어.app` 권장.** `CCTV뷰어.command` still works if you want the Terminal window.

First open on a new Mac: if Gatekeeper blocks it, right-click → **열기**.

## How the icon was made

1. Original artwork (PTZ dome camera + lens, dark/tech) drawn in `icons/generate_icon.py` with Pillow — not a downloaded brand logo.
2. `iconutil` builds `icons/AppIcon.icns` from a generated `.iconset`.
3. `icons/build_app.sh` runs `osacompile`, copies `launcher.sh` + icns into the bundle, then ad-hoc `codesign`s.

Regenerate:

```bash
python3 icons/generate_icon.py
bash icons/build_app.sh
```

## Finder custom icon on `.command` (this Mac only)

`icons/apply_finder_icon.py` uses AppKit `NSWorkspace.setIcon_forFile_` plus `sips`/`DeRez`/`Rez`/`SetFile` so Finder shows the same artwork on `CCTV뷰어.command`. That stamp is local metadata (resource fork); it is **not** in git. Re-run after a fresh clone:

```bash
python3 icons/apply_finder_icon.py "CCTV뷰어.command"
```
