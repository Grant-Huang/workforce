#!/usr/bin/env bash
# Download (once) and run a local LiveKit server in dev mode.
#
# Dev mode listens on 127.0.0.1:7880 with the well-known credentials
# devkey / secret, which are also this demo's config defaults -- so a local run needs
# no LiveKit account and no configuration. Do not use dev mode for anything reachable
# from outside the machine: those credentials are public.
set -euo pipefail

VERSION="${LIVEKIT_VERSION:-1.13.6}"
BIN_DIR="${LIVEKIT_BIN_DIR:-$HOME/.local/bin}"
BIN="$BIN_DIR/livekit-server"

case "$(uname -s)" in
  Darwin) OS="darwin" ;;
  Linux) OS="linux" ;;
  *) echo "不支持的系统：$(uname -s)（请手动安装 livekit-server）" >&2; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64 | amd64) ARCH="amd64" ;;
  arm64 | aarch64) ARCH="arm64" ;;
  *) echo "不支持的架构：$(uname -m)" >&2; exit 1 ;;
esac

if [ ! -x "$BIN" ]; then
  # Only linux/darwin release archives exist; macOS users can also `brew install livekit`.
  URL="https://github.com/livekit/livekit/releases/download/v${VERSION}/livekit_${VERSION}_${OS}_${ARCH}.tar.gz"
  echo "下载 livekit-server ${VERSION} (${OS}/${ARCH})…"
  mkdir -p "$BIN_DIR"
  TMP="$(mktemp -d)"
  curl -sSfL "$URL" -o "$TMP/livekit.tar.gz"
  tar -xzf "$TMP/livekit.tar.gz" -C "$TMP"
  mv "$TMP/livekit-server" "$BIN"
  chmod +x "$BIN"
  rm -rf "$TMP"
fi

echo "启动 livekit-server --dev（ws://127.0.0.1:7880，devkey/secret）"
exec "$BIN" --dev --bind 127.0.0.1
