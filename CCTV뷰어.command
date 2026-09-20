#!/bin/bash
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ ! -f config.py ]]; then
  echo "config.py 없음. 먼저: cp config.example.py config.py 후 수정"
  read -r _
  exit 1
fi
exec /usr/bin/env python3 -u app.py
