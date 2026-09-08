#!/usr/bin/env bash
# Start LiveKit dev server (requires livekit-server binary in PATH or /tmp/livekit-server)
set -euo pipefail

BIN="${LIVEKIT_SERVER_BIN:-/tmp/livekit-server}"
if ! command -v "$BIN" >/dev/null 2>&1 && [ ! -x "$BIN" ]; then
  echo "livekit-server not found. Download from https://github.com/livekit/livekit/releases"
  echo "Or: curl -sL .../livekit_*_linux_amd64.tar.gz | tar -xz -C /tmp"
  exit 1
fi

echo "Starting LiveKit dev server on ws://127.0.0.1:7880 (devkey/secret)"
exec "$BIN" --dev --bind 127.0.0.1
