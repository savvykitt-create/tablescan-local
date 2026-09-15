"""Per-file orientation and geometry checks before any batch OCR starts."""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import cv2
from PySide6.QtCore import QThread, Signal

from .domain import TableTemplate
from .i18n import tr
from .imaging import detect_grid, load_document, rotate_document
from .template_fit import fit_template
from .template_matcher import rank_templates


@dataclass
class Compatibility:
    score: int | None
    reasons: list[str]
    template: TableTemplate | None
    error: str = ''
    rotation_degrees: int = 0
    preview_template: TableTemplate | None = None
    fit_status: str = 'needs_review'


@dataclass
class PreparedFile:
    path: str
    assessments: dict[str, Compatibility] = field(default_factory=dict)
    geometry: dict = field(default_factory=dict)
    error: str = ''
    pages: int = 0
    thumbnails: list = field(default_factory=list)


def assess_geometry(template, pages, *, auto_fit=True):
    """Use the weakest page, so an incompatible page cannot be averaged away."""
    scores, reasons, fitted = [], [], []
    ranking_template = TableTemplate.from_dict(template.to_dict())
    ranking_template.auto_fit_rows = auto_fit
    for index, (shape, detection) in enumerate(pages):
        if detection is None:
            return Compatibility(None, [], None, str(tr('Grid could not be detected. Open the file and align the protocol manually.')))
        match = rank_templates(shape, detection, [ranking_template])[0]
        scores.append(round(match.score * 100))
        reasons.extend(str(r) for r in match.reasons)
        reasons.extend(str(w) for w in detection.warnings)
        try:
            fitted.append(fit_template(template, detection, shape[1] / shape[0])
                          if auto_fit else TableTemplate.from_dict(template.to_dict()))
        except ValueError as exc:
            return Compatibility(min(scores), list(dict.fromkeys(reasons)), None,
                                 str(tr('Page {p0}: {p1}', p0=index + 1, p1=str(exc))))
    if not fitted:
        return Compatibility(None, [], None, str(tr('The document contains no pages.')))
    first = fitted[0]
    # The OCR result uses one grid for the entire document, just like auto-fit.
    if auto_fit and any(other.rows != first.rows or any(
            np.max(np.abs(np.array(a) - np.array(b))) > .002
            for a, b in ((first.row_guides, other.row_guides),
                         (first.column_guides, other.column_guides))) for other in fitted[1:]):
        return Compatibility(min(scores), list(dict.fromkeys(reasons)), None,
                             str(tr('Pages have different grids. Split the document into separate files.')))
    # The inspected geometry is the geometry used by OCR. Do not fit again in
    # the worker, which could otherwise move manually inspected cells.
    first.auto_fit_rows = False
    return Compatibility(min(scores), list(dict.fromkeys(reasons)), first,
                         rotation_degrees=template.rotation_degrees,
                         preview_template=first, fit_status='fitted' if auto_fit else 'manual')


def assess_orientations(template, geometry):
    candidates = []
    for rotation, pages in geometry.items():
        oriented = TableTemplate.from_dict(template.to_dict())
        oriented.rotation_degrees = rotation
        result = assess_geometry(oriented, pages)
        result.rotation_degrees = rotation
        result.preview_template = result.template or oriented
        candidates.append(result)
    candidates.sort(key=lambda c: (c.template is not None, c.score if c.score is not None else -1), reverse=True)
    best = candidates[0]
    valid = [c for c in candidates if c.template is not None]
    if len(valid) > 1 and (valid[0].score or 0) - (valid[1].score or 0) < 4:
        best.error = str(tr('Orientation is uncertain. Open the file and check which side is up.'))
        best.template = None
        best.fit_status = 'needs_review'
    if best.template:
        best.reasons.insert(0, str(tr('Auto-fit applied: {p0} rows × {p1} columns.', p0=best.template.rows, p1=best.template.columns)))
    best.reasons.insert(0, str(tr('Rotation: {p0}° counterclockwise.', p0=best.rotation_degrees)))
    return best


def prepare_file(path, templates, interrupted=lambda: False, progress=lambda message: None):
    item = PreparedFile(str(path))
    try:
        images = load_document(path)
        if not images:
            raise ValueError(tr('The document contains no pages.'))
        item.pages = len(images)
        for image in images:
            scale = min(1., 1000 / max(image.shape[:2]))
            item.thumbnails.append(cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA))
        # A saved rotation belongs to the reference scan, not to every file in
        # a batch. Check all four orientations independently for each file.
        for rotation in (0, 90, 180, 270):
            pages = []
            for index, image in enumerate(rotate_document(images, rotation)):
                if interrupted():
                    raise InterruptedError()
                progress(str(tr('Checking page {p0}: rotation {p1}°', p0=index + 1, p1=rotation)))
                try:
                    detection = detect_grid(image)
                except ValueError:
                    detection = None
                pages.append((image.shape, detection))
            item.geometry[rotation] = pages
        for template in templates:
            if interrupted():
                raise InterruptedError()
            item.assessments[template.id] = assess_orientations(template, item.geometry)
    except InterruptedError:
        raise
    except Exception as exc:
        item.error = str(exc)
    return item


class PreflightWorker(QThread):
    prepared = Signal(int, object)
    progress = Signal(int, str)

    def __init__(self, paths, templates, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.templates = [TableTemplate.from_dict(t.to_dict()) for t in templates]

    def run(self):
        for index, path in enumerate(self.paths):
            if self.isInterruptionRequested():
                return
            try:
                item = prepare_file(path, self.templates, self.isInterruptionRequested,
                                    lambda message: self.progress.emit(index, message))
            except InterruptedError:
                return
            self.prepared.emit(index, item)
