#!/bin/sh
# Use only Codex's existing bundled runtime. Never install packages or start a network listener.
tutor_python="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [ ! -x "$tutor_python" ]; then
  echo 'Codex bundled Python is unavailable. Ask Codex to load its workspace dependencies; no separate Python installation is required.' >&2
  exit 1
fi
exec "$tutor_python" "$(dirname "$0")/course_server.py"
