#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
NODE_VERSION="${LOBSTER_NODE_VERSION:-22.22.0}"
RUNTIME_ROOT="$ROOT/.runtime"
ARCH="$(uname -m)"

case "$ARCH" in
  x86_64|amd64) NODE_ARCH="x64" ;;
  aarch64|arm64) NODE_ARCH="arm64" ;;
  *)
    echo "[ERR] Unsupported architecture for Node.js: $ARCH" >&2
    exit 1
    ;;
esac

NODE_DIR="$RUNTIME_ROOT/node-v${NODE_VERSION}-linux-${NODE_ARCH}"
NODE_LINK="$RUNTIME_ROOT/node"
ARCHIVE="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
DOWNLOAD_URL="https://nodejs.org/dist/v${NODE_VERSION}/${ARCHIVE}"

mkdir -p "$RUNTIME_ROOT"
if [ ! -x "$NODE_DIR/bin/node" ]; then
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "$TMP_DIR"' EXIT
  echo "[Mastra] Downloading Node.js v${NODE_VERSION} (${NODE_ARCH}) ..."
  curl --fail --location --retry 3 --connect-timeout 15 "$DOWNLOAD_URL" -o "$TMP_DIR/$ARCHIVE"
  tar -xJf "$TMP_DIR/$ARCHIVE" -C "$RUNTIME_ROOT"
fi

ln -sfn "$NODE_DIR" "$NODE_LINK"
export PATH="$NODE_LINK/bin:$PATH"
"$NODE_LINK/bin/node" --version
"$NODE_LINK/bin/npm" --version
