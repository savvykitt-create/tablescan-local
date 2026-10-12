"""Lazy raster pages shared by preparation, recognition and review.

Native readers are opened for one operation and closed before returning pixels.
No PDFium/Pillow handle crosses a Qt thread or survives a cancelled job.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import RLock
import copy
import math
import operator

import cv2
import numpy as np
from PIL import Image, ImageOps
from pi_heif import register_heif_opener

from .i18n import tr


# Decode the original HEIF image locally, including its stored orientation.
# Depth maps, gain maps and thumbnails are not document pages.
register_heif_opener(thumbnails=False, depth_images=False, aux_images=False)

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".heif"}


def document_file_filter():
    return tr('Documents ({patterns})', patterns=' '.join(
        pattern for extension in sorted(SUPPORTED_EXTENSIONS)
        for pattern in ('*' + extension, '*' + extension.upper())))


PDF_LOCK = RLock()  # PDFium is not safe to use concurrently, even across documents.
RENDER_VERSION = 1


class DocumentPages(Sequence[np.ndarray]):
    """Random-access pages with an LRU bounded by both count and bytes.

    A single oversized page is returned without caching. The caller may retain
    its current page, but reading a long document never retains all its rasters.
    Slicing explicitly materializes the requested pages for legacy callers.
    """

    def __init__(self, path: str | Path, scale: float = 3.0, *, rotation: int = 0,
                 transforms: dict | None = None,
                 max_dimension: int | None = None, cache_pages: int = 2,
                 cache_bytes: int = 64 * 1024 ** 2,
                 cancelled: Callable[[], bool] | None = None):
        self.path = Path(path)
        if self.path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(tr('Unsupported file type: {p0}', p0=self.path.suffix.lower()))
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError('Page scale must be positive and finite')
        if rotation % 90:
            raise ValueError(tr('Rotation must be a multiple of 90 degrees'))
        if max_dimension is not None and max_dimension < 1:
            raise ValueError('Preview dimension must be positive')
        self.scale = scale
        self.rotation = rotation % 360
        self.transforms = {int(k): copy.deepcopy(v) for k,v in (transforms or {}).items()}
        from .photo_geometry import validate_transform
        for value in self.transforms.values():
            validate_transform(value)
        self.max_dimension = max_dimension
        self.cache_pages = max(0, cache_pages)
        self.cache_bytes = max(0, cache_bytes)
        self.cancelled = cancelled
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()
        self._cached_bytes = 0
        self._lock = RLock()
        self.check_cancelled()
        self._source_stat = self._stat_identity()
        self._primary_frame = 0
        if self.path.suffix.lower() == '.pdf':
            import pypdfium2 as pdfium
            with PDF_LOCK, pdfium.PdfDocument(str(self.path)) as document:
                self._length = len(document)
        else:
            with Image.open(self.path) as document:
                self._length = getattr(document, 'n_frames', 1)
                if document.format == 'HEIF':
                    self._primary_frame = document.tell()
        if any(index < 0 or index >= self._length for index in self.transforms):
            raise ValueError(tr('Invalid photograph alignment. Align the four corners again.'))
        self.check_cancelled()
        self.check_unchanged()

    def _stat_identity(self):
        stat = self.path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def check_unchanged(self):
        if self._stat_identity() != self._source_stat:
            raise OSError(tr('The source changed while it was open. Reopen the document before continuing.'))

    def check_cancelled(self):
        if self.cancelled and self.cancelled():
            raise InterruptedError()

    def __len__(self):
        return self._length

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        index = operator.index(index)
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        self.check_cancelled()
        self.check_unchanged()
        with self._lock:
            if index in self._cache:
                self._cache.move_to_end(index)
                return self._cache[index]
            image = self._render(index)
            self.check_cancelled()
            self.check_unchanged()
            if self.cache_pages and image.nbytes <= self.cache_bytes:
                while self._cache and (len(self._cache) >= self.cache_pages
                                      or self._cached_bytes + image.nbytes > self.cache_bytes):
                    _, removed = self._cache.popitem(last=False)
                    self._cached_bytes -= removed.nbytes
                self._cache[index] = image
                self._cached_bytes += image.nbytes
            return image

    def _render(self, index):
        if self.path.suffix.lower() == '.pdf':
            import pypdfium2 as pdfium
            with PDF_LOCK, pdfium.PdfDocument(str(self.path)) as document:
                page = document[index]
                try:
                    scale = self.scale
                    if self.max_dimension:
                        scale = min(scale, self.max_dimension / max(page.get_size()))
                    bitmap = page.render(scale=scale)
                    try:
                        rgb = np.asarray(bitmap.to_pil().convert('RGB'))
                        image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        else:
            with Image.open(self.path) as document:
                # HEIF may designate a nonzero frame as the main photograph.
                # Show it first, then other top-level images in container order.
                frame = (self._primary_frame if index == 0 else
                         index - 1 if index <= self._primary_frame else index)
                document.seek(frame)
                rgb = np.asarray(ImageOps.exif_transpose(document).convert('RGB'))
                image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if self.max_dimension and max(image.shape[:2]) > self.max_dimension:
            ratio = self.max_dimension / max(image.shape[:2])
            image = cv2.resize(image, None, fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA)
        if self.rotation:
            image = np.ascontiguousarray(np.rot90(image, self.rotation // 90))
        if index in self.transforms:
            from .photo_geometry import apply_transform
            image = apply_transform(image, self.transforms[index])
        return image

    def rotated(self, degrees):
        self.check_unchanged()
        if degrees % 90:
            raise ValueError(tr('Rotation must be a multiple of 90 degrees'))
        if degrees % 360 == 0:
            return self
        return DocumentPages(self.path, self.scale, rotation=self.rotation + degrees,
                             max_dimension=self.max_dimension, cache_pages=self.cache_pages,
                             cache_bytes=self.cache_bytes, cancelled=self.cancelled)

    @property
    def rendering(self):
        return dict(version=RENDER_VERSION, scale=self.scale, rotation=self.rotation,
                    max_dimension=self.max_dimension, pages=len(self), transforms=copy.deepcopy(self.transforms))

    @property
    def cached_bytes(self):
        with self._lock:
            return self._cached_bytes

    def clear_cache(self):
        with self._lock:
            self._cache.clear()
            self._cached_bytes = 0


def open_document(path: str | Path, scale: float = 3.0, **options) -> DocumentPages:
    return DocumentPages(path, scale, **options)
