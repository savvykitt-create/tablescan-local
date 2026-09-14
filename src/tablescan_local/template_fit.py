"""Conservative fitting of regular forms with a variable number of data rows."""
from __future__ import annotations

import numpy as np

from .domain import NormalizedRect, TableTemplate
from .imaging import GridDetection, detect_grid


def fit_template(template: TableTemplate, detection: GridDetection, page_aspect: float | None = None) -> TableTemplate:
    """Return an independent fitted copy; never mutate the saved master.

    Only complete, regular grids with matching column proportions are accepted.
    Metadata follows its nearest table edge and page scale. Row height may
    change independently when a shorter cohort fills the same table area.
    """
    if detection.warnings:
        raise ValueError("Auto-fit: reconstructed grid needs manual checking.")
    rows = len(detection.row_guides) - 1
    if len(detection.column_guides) != len(template.column_guides):
        raise ValueError("Auto-fit: column count differs from the template.")
    if not template.header_rows < rows <= 200:
        raise ValueError("Auto-fit: invalid number of data rows.")
    old_x = np.asarray(template.column_guides)
    new_x = np.asarray(detection.column_guides)
    old_y = np.asarray(template.row_guides)
    new_y = np.asarray(detection.row_guides)
    for axis in (old_x, new_x, old_y, new_y):
        if not np.all(np.isfinite(axis)) or np.any(np.diff(axis) <= 0):
            raise ValueError("Auto-fit: invalid grid coordinates.")
    if np.max(np.abs((old_x-old_x[0]) / np.ptp(old_x) - (new_x-new_x[0]) / np.ptp(new_x))) > .025:
        raise ValueError("Auto-fit: column proportions differ from the template.")
    # This first implementation intentionally rejects merged/nonuniform headers.
    for axis in (old_y, new_y):
        pitch = np.median(np.diff(axis))
        if np.max(np.abs(np.diff(axis) / pitch - 1)) > .15:
            raise ValueError("Auto-fit: uneven row spacing needs manual checking.")
    sx = np.ptp(new_x) / np.ptp(old_x)
    sy = np.median(np.diff(new_y)) / np.median(np.diff(old_y))
    if template.reference_page_aspect:
        sy = sx * (page_aspect or template.reference_page_aspect) / template.reference_page_aspect
    fitted = TableTemplate.from_dict(template.to_dict())
    for field in fitted.fields:
        r = field.rect
        if r.y >= old_y[-1]:
            y = new_y[-1] + (r.y - old_y[-1]) * sy
        else:
            if field.source != "fixed" and r.y + r.height > old_y[0]:
                raise ValueError("Auto-fit: OCR fields inside the table need manual alignment.")
            y = new_y[0] + (r.y - old_y[0]) * sy
        x = new_x[0] + (r.x - old_x[0]) * sx
        if x < -.002 or y < -.002 or x + r.width*sx > 1.002 or y + r.height*sy > 1.002:
            raise ValueError("Auto-fit: a metadata field falls outside the page.")
        field.rect = NormalizedRect(max(0., float(x)), max(0., float(y)), float(r.width*sx), float(r.height*sy))
    fitted.resize_grid(rows, template.columns)
    fitted.table_rect = detection.table_rect
    fitted.row_guides = list(detection.row_guides)
    fitted.column_guides = list(detection.column_guides)
    fitted.validate_value_rules()
    return fitted


def fit_document_template(template: TableTemplate, images: list[np.ndarray]) -> TableTemplate:
    if not template.auto_fit_rows:
        return TableTemplate.from_dict(template.to_dict())
    fitted = [fit_template(template, detect_grid(image), image.shape[1]/image.shape[0]) for image in images]
    first = fitted[0]
    # JobResult currently has one grid shared by all pages. Refuse mixed layouts.
    for other in fitted[1:]:
        if other.rows != first.rows or any(
            np.max(np.abs(np.array(a)-np.array(b))) > .002
            for a, b in ((first.row_guides, other.row_guides), (first.column_guides, other.column_guides))
        ):
            raise ValueError("Auto-fit: pages have different grids. Open them as separate files.")
    return first
