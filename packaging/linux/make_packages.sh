#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="0.7.11"
STAGE="$PROJECT_DIR/build/linux-package"
APPDIR="$PROJECT_DIR/build/TableScanLocal.AppDir"
RELEASE="$PROJECT_DIR/release"

rm -rf "$STAGE" "$APPDIR"
mkdir -p "$STAGE/opt/tablescan-local" "$STAGE/usr/bin" "$STAGE/usr/share/applications" "$STAGE/DEBIAN" "$RELEASE"
cp -R "$PROJECT_DIR/dist/TableScanLocal/." "$STAGE/opt/tablescan-local/"
cp "$PROJECT_DIR/packaging/linux/tablescan-local.desktop" "$STAGE/usr/share/applications/"
mkdir -p "$STAGE/usr/share/icons/hicolor/scalable/apps"
cp "$PROJECT_DIR/packaging/linux/tablescan-local.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/"
ln -s /opt/tablescan-local/TableScanLocal "$STAGE/usr/bin/tablescan-local"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: tablescan-local
Version: $VERSION
Section: science
Priority: optional
Architecture: amd64
Maintainer: TableScan Local contributors
Description: Offline table OCR and Excel export
EOF

dpkg-deb --build "$STAGE" "$RELEASE/TableScan-Local_${VERSION}_amd64.deb"
tar -C "$PROJECT_DIR/dist" -czf "$RELEASE/TableScan-Local_${VERSION}_linux-x64.tar.gz" TableScanLocal

mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -R "$PROJECT_DIR/dist/TableScanLocal/." "$APPDIR/usr/bin/"
cp "$PROJECT_DIR/packaging/linux/tablescan-local.desktop" "$APPDIR/"
cp "$PROJECT_DIR/packaging/linux/tablescan-local.desktop" "$APPDIR/usr/share/applications/"
cp "$PROJECT_DIR/packaging/linux/tablescan-local.svg" "$APPDIR/tablescan-local.svg"
cp "$PROJECT_DIR/packaging/linux/tablescan-local.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/TableScanLocal" "$@"
EOF
chmod +x "$APPDIR/AppRun"
if command -v appimagetool >/dev/null 2>&1; then
    ARCH=x86_64 appimagetool "$APPDIR" "$RELEASE/TableScan-Local_${VERSION}_x86_64.AppImage"
else
    tar -C "$(dirname "$APPDIR")" -czf "$RELEASE/TableScan-Local_${VERSION}_AppDir.tar.gz" "$(basename "$APPDIR")"
fi
sha256sum "$RELEASE"/* > "$RELEASE/SHA256SUMS"
