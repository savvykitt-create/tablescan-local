"""Keep Qt platform/image support without unrelated PDF/virtual-keyboard plugins."""
from pathlib import Path
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# PDF input uses pypdfium2. The Widgets UI does not use Qt Virtual Keyboard.
# Drop these plugins before PyInstaller analyzes their binary dependencies.
binaries = [(source, target) for source, target in binaries
            if not any(part in Path(source).name.lower() for part in ("qpdf.", "qtvirtualkeyboard"))]
