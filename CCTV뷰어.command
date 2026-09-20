#!/bin/bash
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ ! -f config.py ]]; then
  echo "config.py 없음. 먼저: cp config.example.py config.py 후 수정"
  read -r _
  # still close this window after Enter
else
  /usr/bin/env python3 -u app.py
fi
# Close only this Terminal window when the process finishes
TTY_NAME=$(tty 2>/dev/null | sed 's|/dev/||')
osascript >/dev/null 2>&1 <<OSA || true
tell application "Terminal"
  repeat with w in windows
    try
      if tty of w contains "$TTY_NAME" then
        close w
        exit repeat
      end if
    end try
  end repeat
end tell
OSA
