#!/bin/bash
# Assemble CCTV뷰어.app as an osacompile applet (Mach-O stub → Finder treats it as an app).
set -euo pipefail
ICONS="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$ICONS/.." && pwd)"
APP="$ROOT/CCTV뷰어.app"

if [[ ! -f "$ICONS/AppIcon.icns" ]]; then
  python3 "$ICONS/generate_icon.py"
fi

chmod 755 "$ICONS/launcher.sh"

rm -rf "$APP"
/usr/bin/osacompile -o "$APP" "$ICONS/launcher.applescript"

mkdir -p "$APP/Contents/Resources"
cp "$ICONS/launcher.sh" "$APP/Contents/Resources/launcher.sh"
chmod 755 "$APP/Contents/Resources/launcher.sh"
# osacompile uses CFBundleIconFile=applet
cp "$ICONS/AppIcon.icns" "$APP/Contents/Resources/applet.icns"
cp "$ICONS/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

INFO="$APP/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleName -string "CCTV뷰어" "$INFO"
/usr/bin/plutil -replace CFBundleDisplayName -string "CCTV뷰어" "$INFO"
/usr/bin/plutil -replace CFBundleIdentifier -string "com.ksh900222.iptime-cctv-viewer" "$INFO"
/usr/bin/plutil -replace CFBundleShortVersionString -string "1.0" "$INFO" 2>/dev/null || \
  /usr/bin/plutil -insert CFBundleShortVersionString -string "1.0" "$INFO"
/usr/bin/plutil -replace CFBundleVersion -string "1" "$INFO" 2>/dev/null || \
  /usr/bin/plutil -insert CFBundleVersion -string "1" "$INFO"
/usr/bin/plutil -replace NSHighResolutionCapable -bool true "$INFO" 2>/dev/null || \
  /usr/bin/plutil -insert NSHighResolutionCapable -bool true "$INFO"
# Prefer our icns over the default applet Assets.car
/usr/bin/plutil -replace CFBundleIconFile -string "applet" "$INFO"
# Drop asset-catalog icon name so Finder uses Resources/applet.icns
/usr/bin/plutil -remove CFBundleIconName "$INFO" 2>/dev/null || true
rm -f "$APP/Contents/Resources/Assets.car"

# Ad-hoc sign after plist/resource edits (osacompile's signature is now invalid).
/usr/bin/codesign --force -s - "$APP" 2>/dev/null || true

/usr/bin/touch "$APP"
echo "built $APP"
