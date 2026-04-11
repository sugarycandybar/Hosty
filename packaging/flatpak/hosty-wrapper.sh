#!/usr/bin/env sh
set -eu
# Ensure Hosty stores data under the sandboxed XDG data directory so
# the app does not need broad access to the user's $HOME.
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
HOSTY_DATA_DIR="${HOSTY_DATA_DIR:-$XDG_DATA_HOME/hosty}"
export HOSTY_DATA_DIR
# Create the data dir if it does not exist
mkdir -p "$HOSTY_DATA_DIR"

cd /app/share/hosty
exec python3 /app/share/hosty/hosty.py "$@"
