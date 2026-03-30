#!/bin/bash
# Wrapper to launch node with windowsHide-equivalent (bash is non-console on Windows)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec node "$SCRIPT_DIR/run-inject.mjs"
