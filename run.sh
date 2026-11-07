#!/bin/bash
# DocPad Launcher for Linux Mint Cinnamon
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export PATH="$HOME/.local/bin:$PATH"
export GDK_RENDERING=similar
export PYTHONDONTWRITEBYTECODE=1

exec python3 -B "$SCRIPT_DIR/docpad.py" "$@"
