#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
APP_PATH="${1:-$PROJECT_DIR/dist/TableScan Local.app}"
OUTPUT_DIR="$PROJECT_DIR/release"
OUTPUT_NAME="${2:-TableScan-Local-macOS.dmg}"

mkdir -p "$OUTPUT_DIR"
hdiutil create -volname "TableScan Local" -srcfolder "$APP_PATH" -ov -format UDZO "$OUTPUT_DIR/$OUTPUT_NAME"
shasum -a 256 "$OUTPUT_DIR/$OUTPUT_NAME" > "$OUTPUT_DIR/$OUTPUT_NAME.sha256"
