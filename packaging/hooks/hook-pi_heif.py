"""Collect HEIF decoder DLLs owned by the pinned Windows wheel.

The wheel installs mangled DLLs in site-packages, not a package .libs folder.
Use wheel metadata to avoid relying on the build host DLL search path.
"""
from importlib import metadata
import sys

if sys.platform == "win32":
    distribution = metadata.distribution("pi-heif")
    binaries = [(str(distribution.locate_file(entry)), ".")
                for entry in distribution.files or []
                if entry.suffix.lower() == ".dll"]
