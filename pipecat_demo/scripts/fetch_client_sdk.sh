#!/usr/bin/env bash
# Vendor the LiveKit browser SDK into web/vendor/ so the demo page works offline.
#
# Optional: index.html falls back to the jsDelivr CDN when this hasn't been run. The
# vendored file is gitignored -- it's a build artifact, not source.
set -euo pipefail

VERSION="${LIVEKIT_CLIENT_VERSION:-2}"
DEST_DIR="$(cd "$(dirname "$0")/.." && pwd)/web/vendor"
URL="https://cdn.jsdelivr.net/npm/livekit-client@${VERSION}/dist/livekit-client.umd.min.js"

mkdir -p "$DEST_DIR"
echo "下载 livekit-client@${VERSION} → web/vendor/"
curl -sSfL "$URL" -o "$DEST_DIR/livekit-client.umd.min.js"
echo "完成：$DEST_DIR/livekit-client.umd.min.js"
