"""Verify the actual frozen output before it can become a release artifact."""
from pathlib import Path
import sys

from collect_licenses import verify


def verify_bundle(root: Path):
    manifests = list(root.rglob("licenses/manifest.json"))
    if not manifests:
        raise RuntimeError("No license inventory found in the packaged application")
    for manifest in manifests:
        verify(manifest.parent)
    for path in root.rglob("*"):
        name = path.name.lower()
        if "qtvirtualkeyboard" in name or name.startswith(("qpdf.", "libqpdf.")) or name == "qtpdf.framework":
            raise RuntimeError(f"Unused Qt plugin/module in the package: {path}")
    print(f"Verified {len(manifests)} packaged license inventories; unused Qt plugins absent")


if __name__ == "__main__":
    verify_bundle(Path(sys.argv[1]))
