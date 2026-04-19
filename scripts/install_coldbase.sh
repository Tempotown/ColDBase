#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${HOME}/.local/bin"
TARGET_PATH="${TARGET_DIR}/coldbase"

mkdir -p "${TARGET_DIR}"
ln -sf "${ROOT_DIR}/coldbase" "${TARGET_PATH}"

echo "Installed coldbase -> ${TARGET_PATH}"
echo ""
echo "If \`${TARGET_DIR}\` is not already on your PATH, add this to your shell profile:"
echo "export PATH=\"${TARGET_DIR}:\$PATH\""
