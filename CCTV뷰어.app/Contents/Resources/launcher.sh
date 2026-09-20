#!/bin/bash
# CCTV뷰어.app entry point — runs python3 app.py from the project folder.
# The .app may sit next to app.py, or next to a viewer/ subdirectory.

export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/3.12/bin:$PATH"

_resolve_dir() {
  local src="$1"
  while [ -L "$src" ]; do
    local dir
    dir="$(cd "$(dirname "$src")" && pwd)"
    src="$(readlink "$src")"
    [[ "$src" != /* ]] && src="$dir/$src"
  done
  cd "$(dirname "$src")" && pwd
}

MACOS_DIR="$(_resolve_dir "${BASH_SOURCE[0]}")"
# launcher.sh lives in Contents/Resources (or Contents/MacOS); two levels up is the .app
APP_BUNDLE="$(cd "$MACOS_DIR/../.." && pwd)"
PARENT="$(cd "$APP_BUNDLE/.." && pwd)"

ROOT=""
if [[ -f "$PARENT/app.py" ]]; then
  ROOT="$PARENT"
elif [[ -f "$PARENT/viewer/app.py" ]]; then
  ROOT="$PARENT/viewer"
fi

_alert() {
  local msg="$1"
  /usr/bin/osascript -e "display dialog \"${msg}\" buttons {\"확인\"} default button 1 with title \"CCTV 뷰어\" with icon stop" >/dev/null
}

if [[ -z "$ROOT" ]]; then
  _alert "app.py를 찾을 수 없습니다. CCTV뷰어.app 을 app.py가 있는 프로젝트 폴더에 두세요."
  exit 1
fi

cd "$ROOT" || exit 1

if [[ ! -f "$ROOT/config.py" ]]; then
  _alert "config.py가 없습니다.\\n\\n터미널에서:\\ncp config.example.py config.py\\n후 계정/IP를 수정하세요."
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  _alert "python3를 찾을 수 없습니다. Python 3.12+ 를 설치하세요."
  exit 1
fi

python3 -u "$ROOT/app.py"
status=$?
if [[ $status -ne 0 ]]; then
  _alert "뷰어가 종료되었습니다 (코드 ${status}).\\n터미널에서 CCTV뷰어.command 를 실행하면 오류를 볼 수 있습니다."
fi
exit "$status"
