"""Use installed fonts when Qt renders Windows tests without a desktop."""
import os
from pathlib import Path
import sys


if sys.platform == "win32":
    # The offscreen plugin uses Qt's basic font database, not the native Windows
    # font service. PySide6 wheels do not bundle a lib/fonts directory. Without
    # this path every character is a missing-glyph box and layout tests measure
    # those boxes instead of the fonts installed on the target platform.
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if fonts.is_dir():
        os.environ.setdefault("QT_QPA_FONTDIR", str(fonts))
