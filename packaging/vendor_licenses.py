"""Refresh reviewed upstream license snapshots. Not run by the application/CI.

Downloads only licensing/attribution documents from pinned release trees; stores
their immutable source URLs and SHA-256 for the offline packaging collector.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent / "licenses"
SOURCES = [
    ("qt-6.11.2", "pyside/pyside-setup", "v6.11.2"),
    ("qt-6.11.2", "qt/qtbase", "v6.11.2"),
    ("qt-6.11.2", "qt/qtdeclarative", "v6.11.2"),
    ("qt-6.11.2", "qt/qtsvg", "v6.11.2"),
    ("qt-6.11.2", "qt/qtimageformats", "v6.11.2"),
    ("qt-6.11.2", "qt/qttranslations", "v6.11.2"),
    ("rapidocr-1.4.4", "RapidAI/RapidOCR", "v1.4.4"),
    ("flatbuffers-25.12.19", "google/flatbuffers", "v25.12.19"),
]


def fetch(url):
    with urlopen(Request(url, headers={"User-Agent": "TableScan-license-maintenance"}), timeout=45) as response:
        return response.read()


def license_path(path):
    item = Path(path)
    name = item.name.lower()
    return (any(part.lower() in {"licenses", "licences"} for part in item.parts)
            or any(term in name for term in ("license", "licence", "copying", "copyright", "notice"))
            or name == "qt_attribution.json") and item.suffix.lower() not in {".png", ".jpg", ".svg", ".py", ".cpp", ".h"}


def main():
    ROOT.mkdir(exist_ok=True)
    records = []
    for group, repository, ref in SOURCES:
        tree = json.loads(fetch(f"https://api.github.com/repos/{repository}/git/trees/{ref}?recursive=1"))
        if tree.get("truncated"):
            raise RuntimeError(f"Incomplete source tree: {repository}")
        revision = tree["sha"]
        paths = [item["path"] for item in tree["tree"] if item["type"] == "blob" and license_path(item["path"])]
        if not paths:
            raise RuntimeError(f"No license files found: {repository}")
        def download(path):
            url = f"https://raw.githubusercontent.com/{repository}/{revision}/{path}"
            data = fetch(url)
            destination = ROOT / group / repository.split("/")[1] / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            return {"group": group, "file": str(destination.relative_to(ROOT)), "source": url,
                    "sha256": hashlib.sha256(data).hexdigest()}
        with ThreadPoolExecutor(max_workers=8) as pool:
            records.extend(pool.map(download, paths))
        print(f"{repository} {ref}: {len(paths)} license/attribution files", flush=True)
    (ROOT / "sources.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
