#!/usr/bin/env sh
set -eu
cd /app/share/hosty
exec python3 /app/share/hosty/hosty.py "$@"
