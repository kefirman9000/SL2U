#!/usr/bin/env bash
# Double-click (or run) to start Release Kit. First run creates a private Python environment.
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg is not installed. Mac: brew install ffmpeg   Linux: sudo apt install ffmpeg"
fi
if [ ! -d .venv ]; then
  python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
fi
./.venv/bin/python -m releasekit
