from pathlib import Path
import sys
import os

from PyInstaller.utils.hooks import collect_data_files


project_root = Path(SPECPATH).parent
sys.path.insert(0, str(project_root / "packaging"))
from collect_licenses import collect
license_directory = collect(project_root / "build" / "licenses")
signing_identity = os.getenv("TABLESCAN_CODESIGN_IDENTITY")
if sys.platform == "darwin" and os.getenv("TABLESCAN_REQUIRE_SIGNED") == "1" and not signing_identity:
    raise RuntimeError("Signed macOS mode requires TABLESCAN_CODESIGN_IDENTITY (Developer ID Application)")
rapid_data = collect_data_files("rapidocr_onnxruntime")

a = Analysis(
    [str(project_root / "tablescan_local_app.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=rapid_data + [
        (str(project_root / "src/tablescan_local/translations"), "tablescan_local/translations"),
        (str(project_root / "src/tablescan_local/assets"), "tablescan_local/assets"),
        (str(project_root / "src/tablescan_local/models"), "tablescan_local/models"),
        (str(project_root / "src/tablescan_local/slow_runner.py"), "tablescan_local"),
        (str(project_root / "LICENSE"), "."),
        (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
        (str(license_directory), "licenses"),
    ],
    hookspath=[str(project_root / "packaging" / "hooks")],
    hiddenimports=["rapidocr_onnxruntime", "onnxruntime", "pypdfium2", "cv2", "openpyxl"],
    excludes=["tkinter", "matplotlib", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TableScanLocal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    codesign_identity=signing_identity,
    icon=str(project_root / "src/tablescan_local/assets/savvykit.ico") if sys.platform == "win32" else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="TableScanLocal",
)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        icon=str(project_root / "src/tablescan_local/assets/savvykit.icns"),
        name=os.getenv("TABLESCAN_BUNDLE_NAME", "TableScan Local.app"),
        bundle_identifier=os.getenv("TABLESCAN_BUNDLE_ID", "org.tablescan.local"),
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.7.14",
            "CFBundleVersion": "0.7.14",
        },
    )
