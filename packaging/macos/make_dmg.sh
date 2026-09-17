#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
APP_PATH="${1:-$PROJECT_DIR/dist/TableScan Local.app}"
OUTPUT_DIR="$PROJECT_DIR/release"
OUTPUT_NAME="${2:-TableScan-Local-macOS.dmg}"

mkdir -p "$OUTPUT_DIR"
if [[ "${TABLESCAN_REQUIRE_SIGNED:-0}" == "1" ]]; then
  : "${TABLESCAN_CODESIGN_IDENTITY:?Developer ID Application identity is required}"
  spctl --assess --type execute --verbose=4 "$APP_PATH"
fi
hdiutil create -volname "TableScan Local" -srcfolder "$APP_PATH" -ov -format ULFO "$OUTPUT_DIR/$OUTPUT_NAME"
if [[ "${TABLESCAN_REQUIRE_SIGNED:-0}" == "1" ]]; then
  codesign --sign "$TABLESCAN_CODESIGN_IDENTITY" --timestamp "$OUTPUT_DIR/$OUTPUT_NAME"
  python "$PROJECT_DIR/packaging/macos/notarize.py" "$OUTPUT_DIR/$OUTPUT_NAME"
fi
(
  cd "$OUTPUT_DIR"
  shasum -a 256 "$OUTPUT_NAME" > "$OUTPUT_NAME.sha256"
)
