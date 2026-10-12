"""Per-file orientation and geometry checks before any batch OCR starts."""
from dataclasses import dataclass, field
from collections.abc import Sequence

import numpy as np
from PySide6.QtCore import QThread, Signal

from .domain import TableTemplate
from .i18n import tr
from .imaging import detect_grid, open_document, rotate_document
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
    thumbnails: Sequence = field(default_factory=list)


def assess_geometry(template, pages, *, auto_fit=True, all_templates=()):
    """Use the weakest page, so an incompatible page cannot be averaged away."""
    scores, reasons, fitted = [], [], []
    ranking_template = TableTemplate.from_dict(template.to_dict())
    ranking_template.auto_fit_rows = auto_fit
    for index, (shape, detection) in enumerate(pages):
        if detection is None:
            return Compatibility(None, [], None, str(tr('Grid could not be detected. Open the file and align the protocol manually.')))
        match = rank_templates(shape, detection, [ranking_template])[0]
        if all_templates:
            from .photo_preparation import title_matches
            if not title_matches(template,detection.title_text) and any(title_matches(t,detection.title_text) for t in all_templates):
                from .template_matcher import TemplateMatch
                match=TemplateMatch(match.template,match.score*.70,(*match.reasons,tr('Printed title points to another protocol')))
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
    first.page_transforms = {i:d.transform for i,(_,d) in enumerate(pages) if d and d.transform}
    first.auto_fit_rows = False
    return Compatibility(min(scores), list(dict.fromkeys(reasons)), first,
                         rotation_degrees=template.rotation_degrees,
                         preview_template=first, fit_status='fitted' if auto_fit else 'manual')


def assess_orientations(template, geometry, all_templates=()):
    candidates = []
    for rotation, pages in geometry.items():
        oriented = TableTemplate.from_dict(template.to_dict())
        oriented.rotation_degrees = rotation
        result = assess_geometry(oriented, pages, all_templates=all_templates)
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
        images = open_document(path, cache_pages=0, cancelled=interrupted)
        if not images:
            raise ValueError(tr('The document contains no pages.'))
        item.pages = len(images)
        # Preview pages remain lazy as well: a batch must not retain thousands
        # of thumbnails after the full-resolution geometry check completes.
        item.thumbnails = open_document(path, max_dimension=1000, cache_pages=0,
                                        cache_bytes=3 * 1000 ** 2)
        # A saved rotation belongs to the reference scan, not to every file in
        # a batch. Check all four orientations independently for each file.
        item.geometry = {rotation: [] for rotation in (0, 90, 180, 270)}
        for index, original in enumerate(images):
            for rotation, pages in item.geometry.items():
                if interrupted():
                    raise InterruptedError()
                progress(str(tr('Checking page {p0}: rotation {p1}°', p0=index + 1, p1=rotation)))
                image = rotate_document([original], rotation)[0]
                try:
                    from .photo_preparation import prepare_page
                    from pathlib import Path
                    image, detection = prepare_page(image, photograph=Path(path).suffix.lower() != '.pdf', title=bool(templates))
                except ValueError:
                    detection = None
                pages.append((image.shape, detection))
            del image, original
        for template in templates:
            if interrupted():
                raise InterruptedError()
            item.assessments[template.id] = assess_orientations(template, item.geometry, templates)
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
