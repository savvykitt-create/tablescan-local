"""Build a versioned offline license inventory; fail on missing/unreviewed licenses."""
from importlib import metadata
import hashlib
import json
from pathlib import Path
import shutil
import sys
import sysconfig
import tomllib

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
FALLBACKS = {
    **{(name, "6.11.2"): "qt-6.11.2" for name in
       ("pyside6", "pyside6-addons", "pyside6-essentials", "shiboken6")},
    ("rapidocr-onnxruntime", "1.4.4"): "rapidocr-1.4.4",
    ("flatbuffers", "25.12.19"): "flatbuffers-25.12.19",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    upstream = ROOT / "packaging/licenses"
    snapshots = json.loads((upstream / "sources.json").read_text(encoding="utf-8"))
    components, copied_groups = [], {}
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pending = list(config["project"]["dependencies"]) + ["pyinstaller"]
    environment = default_environment()
    environment["extra"] = ""
    seen = set()

    def copy(source, destination, provenance):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return {"path": str(destination.relative_to(output)), "sha256": digest(destination), "source": provenance}

    while pending:
        requirement = Requirement(pending.pop())
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        name = canonicalize_name(requirement.name)
        if name in seen:
            continue
        seen.add(name)
        distribution = metadata.distribution(name)
        pending.extend(distribution.requires or [])
        files = []
        for item in distribution.files or []:
            if not any(term in str(item).lower() for term in ("license", "licence", "copying", "copyright", "notice")):
                continue
            source = distribution.locate_file(item)
            if not source.is_file() or source.suffix.lower() in {".py", ".pyc", ".so", ".dll", ".dylib"}:
                continue
            files.append(copy(source, output / name / f"{len(files):03d}-{source.name}", str(item)))
        if not files:
            group = FALLBACKS.get((name, distribution.version))
            if not group:
                raise RuntimeError(f"No reviewed license files for {name}=={distribution.version}")
            if group not in copied_groups:
                records = [record for record in snapshots if record["group"] == group]
                if not records:
                    raise RuntimeError(f"Missing license snapshot {group}")
                for record in records:
                    source = upstream / record["file"]
                    if digest(source) != record["sha256"]:
                        raise RuntimeError(f"License snapshot checksum mismatch: {source}")
                copied_groups[group] = [copy(upstream / record["file"], output / "upstream" / record["file"], record["source"])
                                        for record in records]
            files = copied_groups[group]
        components.append({"name": name, "version": distribution.version,
                           "declared_license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License"),
                           "files": files})

    python_license = next((path for path in (Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
                                             Path(sys.base_prefix) / "LICENSE.txt") if path.is_file()), None)
    if python_license is None:
        raise RuntimeError("The Python runtime license could not be located")
    components.append({"name": "cpython", "version": sys.version.split()[0],
                       "files": [copy(python_license, output / "cpython/LICENSE.txt", "Build interpreter LICENSE.txt")]})
    components.append({"name": "tablescan-local-and-models", "version": config["project"]["version"], "files": [
        copy(ROOT / "LICENSE", output / "project/LICENSE", "Project LICENSE"),
        copy(ROOT / "THIRD_PARTY_NOTICES.md", output / "project/THIRD_PARTY_NOTICES.md", "Project notices"),
        copy(ROOT / "src/tablescan_local/models/README.md", output / "project/MODELS.md", "Bundled model provenance"),
    ]})
    manifest = {"schema_version": 1, "components": sorted(components, key=lambda item: item["name"])}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "README.txt").write_text(
        "TableScan Local third-party licenses\n\n"
        "manifest.json records exact build versions, source paths/URLs and SHA-256 hashes.\n"
        "upstream/ contains source-release notices for wheels without license files.\n"
        "Qt snapshots include notices for additional upstream components, not all of which are shipped.\n"
        "PyInstaller and its dependencies are listed for build/bootloader provenance.\n"
        "Optional slow-mode packages and weights are installed separately.\n", encoding="utf-8")
    verify(output)
    return output


def verify(output: Path) -> None:
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if not manifest["components"]:
        raise RuntimeError("Empty license inventory")
    for component in manifest["components"]:
        if not component["files"]:
            raise RuntimeError(f"No licenses for {component['name']}")
        for item in component["files"]:
            path = output / item["path"]
            if not path.is_file() or digest(path) != item["sha256"]:
                raise RuntimeError(f"Missing or changed bundled license: {item['path']}")


if __name__ == "__main__":
    destination = Path(sys.argv[-1]) if len(sys.argv) > 1 else ROOT / "build/licenses"
    if "--verify" in sys.argv:
        verify(destination)
    else:
        collect(destination)
