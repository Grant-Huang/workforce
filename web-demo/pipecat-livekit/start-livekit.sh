#!/usr/bin/env bash
# Start LiveKit dev server. Prefers native install, falls back to Docker.
set -euo pipefail

pick_livekit_bin() {
  if [ -n "${LIVEKIT_SERVER_BIN:-}" ] && command -v "$LIVEKIT_SERVER_BIN" >/dev/null 2>&1; then
    echo "$LIVEKIT_SERVER_BIN"
    return
  fi
  if command -v livekit-server >/dev/null 2>&1; then
    command -v livekit-server
    return
  fi
  return 1
}

if BIN="$(pick_livekit_bin)"; then
  echo "Using livekit-server: $BIN"
  echo "Dev credentials: devkey / secret  ->  ws://127.0.0.1:7880"
  exec "$BIN" --dev --bind 127.0.0.1
fi

if command -v docker >/dev/null 2>&1; then
  echo "livekit-server not in PATH, falling back to Docker..."
  exec docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp livekit/livekit-server --dev
fi

echo "livekit-server not found."
echo "  macOS (Homebrew): brew install livekit"
echo "  Or download: https://github.com/livekit/livekit/releases"
echo "  Or set LIVEKIT_SERVER_BIN=/path/to/livekit-server"
exit 1
