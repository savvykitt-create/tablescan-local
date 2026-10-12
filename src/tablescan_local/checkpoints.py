"""Durable, disposable recognition checkpoints, separate from reviewed results."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
from tempfile import NamedTemporaryFile
from threading import RLock

from .domain import PageResult


SCHEMA = 1
_digests = {}
_digest_lock = RLock()


def check_cancelled(cancelled):
    if cancelled():
        raise InterruptedError()


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_digest(path, cancelled=lambda: False):
    """Hash in bounded chunks; reuse only while file identity remains unchanged."""
    path = Path(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_dev, stat.st_ino, stat.st_size,
           stat.st_mtime_ns, stat.st_ctime_ns)
    check_cancelled(cancelled)
    # Rapid same-size writes can share an mtime on Windows/filesystems with
    # coarse timestamps. Rehash recently written files until that window passes.
    cacheable = time.time_ns() - stat.st_mtime_ns > 2_000_000_000
    with _digest_lock:
        if cacheable and key in _digests:
            return _digests[key]
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            check_cancelled(cancelled)
            digest.update(chunk)
    after = path.stat()
    if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
        raise OSError('A recognition input changed while it was being read')
    with _digest_lock:
        if len(_digests) >= 1024:
            _digests.clear()
        if cacheable:
            _digests[key] = digest.hexdigest()
    return digest.hexdigest()


def recognition_identity(high_accuracy, slow_mode, cancelled=lambda: False):
    """Include shipped implementation, weights, decoder and runtime versions.

    Hashing never loads model weights into RAM. A process-local file identity
    cache avoids rereading unchanged multi-gigabyte Slow weights for every job.
    """
    from . import __version__
    from .ocr_runtime import acceleration_provider
    import rapidocr_onnxruntime
    root = Path(__file__).parent
    files = sorted(root.glob('*.py')) + sorted(root.glob('*.pyc'))
    files += sorted(root.glob('*numeric_decoder*'))
    files += sorted((root / 'models').glob('*.onnx'))
    files += sorted((root / 'models').glob('*.txt'))
    if getattr(sys, 'frozen', False):
        # PyInstaller embeds most module bytecode in its executable/PYZ archive.
        files.append(Path(sys.executable))
    rapid_root = Path(rapidocr_onnxruntime.__file__).parent
    files += sorted(rapid_root.rglob('*.onnx')) + sorted(rapid_root.rglob('*.yaml'))
    versions = {}
    for package in ('numpy', 'opencv-python', 'Pillow', 'pi-heif', 'pypdfium2', 'rapidocr-onnxruntime',
                    'onnxruntime', 'onnxruntime-directml', 'onnxruntime-gpu'):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            pass
    identity = dict(app=__version__, python=sys.version, platform=platform.platform(),
                    high_accuracy=bool(high_accuracy or slow_mode), slow_mode=bool(slow_mode),
                    provider=acceleration_provider(), versions=versions)
    if slow_mode:
        from .slow_runtime import runtime_root
        config_path = runtime_root() / 'runtime.json'
        if config_path.is_file():
            files.append(config_path)
            try:
                config = json.loads(config_path.read_text(encoding='utf-8'))
                for name in ('qwen', 'glm'):
                    folder = Path(config[name])
                    files.extend(p for p in folder.rglob('*') if p.is_file() and '.cache' not in p.parts)
                executable = Path(config['python'])
                if executable.is_file():
                    files.append(executable)
                environment = executable.parent.parent
                files.extend(environment.glob('**/*.dist-info/METADATA'))
            except (KeyError, TypeError, ValueError):
                # Runtime validation happens after primary OCR and reports a
                # warning without discarding those completed primary results.
                pass
    identity['files'] = {str(path): file_digest(path, cancelled)
                         for path in dict.fromkeys(files) if path.is_file()}
    return identity


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.' + path.name,
                                suffix='.tmp', dir=path.parent, delete=False) as stream:
            pending = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


@dataclass
class SavedPage:
    page: PageResult
    seconds: float
    model_version: str


class AnalysisCheckpoint:
    """One file per page/stage; a corrupt page cannot invalidate its neighbours."""

    def __init__(self, directory, identity, template, source_path):
        self.directory = Path(directory)
        self.fingerprint = json_digest(identity)
        self.template = template
        self.source_path = source_path

    def _path(self, stage, index):
        if stage not in {'primary', 'slow'} or index < 0:
            raise ValueError('Invalid checkpoint stage or page')
        return self.directory / f'{stage}-{index + 1}.json'

    def load(self, stage, index, *, baseline=None):
        try:
            record = json.loads(self._path(stage, index).read_text(encoding='utf-8'))
            if (record['schema'] != SCHEMA or record['fingerprint'] != self.fingerprint
                    or record['baseline'] != baseline
                    or record['digest'] != json_digest(record['page'])):
                return None
            page = PageResult.from_dict(record['page'])
            if page.page_index != index or page.source_file != self.source_path:
                return None
            expected = {(r, c) for r in range(self.template.rows) for c in range(self.template.columns)}
            if len(page.cells) != len(expected) or {(c.row, c.column) for c in page.cells} != expected:
                return None
            if sorted(f.region_id for f in page.fields) != sorted(f.id for f in self.template.fields):
                return None
            # Checkpoints may outlive manually cleaned diagnostic crops.
            if any(path and not Path(path).is_file() for item in [*page.cells, *page.fields]
                   for path in (item.crop_path, getattr(item, 'preview_crop_path', ''))):
                return None
            seconds = float(record['seconds'])
            if not math.isfinite(seconds) or seconds < 0 or not isinstance(record['model_version'], str):
                return None
            return SavedPage(page, seconds, record['model_version'])
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
            return None

    def save(self, stage, page, seconds, model_version, *, baseline=None):
        data = page.to_dict()
        atomic_json(self._path(stage, page.page_index), dict(
            schema=SCHEMA, fingerprint=self.fingerprint, baseline=baseline,
            digest=json_digest(data), page=data, seconds=seconds, model_version=model_version))
