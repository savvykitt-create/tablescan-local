from pathlib import Path
import sys
import os

from PyInstaller.utils.hooks import collect_data_files


project_root = Path(SPECPATH).parent
rapid_data = collect_data_files("rapidocr_onnxruntime")

a = Analysis(
    [str(project_root / "tablescan_local_app.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=rapid_data + [
        (str(project_root / "src/tablescan_local/models"), "tablescan_local/models"),
        (str(project_root / "LICENSE"), "."),
        (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
    ],
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
        name=os.getenv("TABLESCAN_BUNDLE_NAME", "TableScan Local.app"),
        bundle_identifier=os.getenv("TABLESCAN_BUNDLE_ID", "org.tablescan.local"),
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.6.5",
            "CFBundleVersion": "0.6.5",
        },
    )
