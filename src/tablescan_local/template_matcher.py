"""Explainable, local-only matching of saved templates to a detected grid."""
from __future__ import annotations

from .i18n import tr, fmt, join_text
from dataclasses import dataclass, replace
from itertools import chain
from math import exp

from .domain import TableTemplate
from .imaging import GridDetection, detect_grid
from .template_fit import fit_detected_pages


@dataclass(frozen=True, slots=True)
class TemplateMatch:
    template: TableTemplate
    score: float
    reasons: tuple[str, ...]
    fitted: TableTemplate | None = None
    fit_error: str = ''


def latest_template_versions(templates: list[TableTemplate]) -> list[TableTemplate]:
    latest: dict[str, TableTemplate] = {}
    for template in templates:
        family = template.family_id or template.id
        current = latest.get(family)
        if current is None or template.template_version > current.template_version:
            latest[family] = template
    return sorted(latest.values(), key=lambda item: item.name.casefold())


def _count_similarity(actual: int, expected: int) -> float:
    return exp(-abs(actual - expected) / max(1.0, expected * .18))


def _rect_similarity(template: TableTemplate, detection: GridDetection) -> float:
    a, b = template.table_rect, detection.table_rect
    differences = (
        abs(a.x - b.x), abs(a.y - b.y), abs(a.width - b.width), abs(a.height - b.height),
    )
    return max(0.0, 1.0 - sum(differences) / .8)


def _guide_similarity(first: list[float], second: list[float]) -> float:
    if len(first) != len(second) or len(first) < 2:
        return 0.0
    first_span = max(1e-9, first[-1] - first[0])
    second_span = max(1e-9, second[-1] - second[0])
    a = [(value - first[0]) / first_span for value in first]
    b = [(value - second[0]) / second_span for value in second]
    return max(0.0, 1.0 - sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a) * 4.0)


def rank_templates(
    image_shape: tuple[int, ...],
    detection: GridDetection,
    templates: list[TableTemplate],
    *,
    remaining_pages=(),
) -> list[TemplateMatch]:
    """Rank the latest saved version in every family and explain each score."""
    height, width = image_shape[:2]
    page_aspect = width / max(1, height)
    from .photo_preparation import title_matches
    title_candidates = {t.id for t in templates if title_matches(t, detection.title_text)} if detection.title_text else set()
    matches = []
    for template in latest_template_versions(templates):
        row_score = _count_similarity(len(detection.row_guides) - 1, template.rows)
        column_score = _count_similarity(len(detection.column_guides) - 1, template.columns)
        rect_score = _rect_similarity(template, detection)
        row_shape = _guide_similarity(template.row_guides, detection.row_guides)
        fitted = None
        fit_error = ''
        if template.auto_fit_rows:
            try:
                fitted = fit_detected_pages(template, chain([(page_aspect, detection)], remaining_pages))
            except ValueError as exc:
                fit_error = str(exc)
            else:
                row_score = row_shape = 1.0
        column_shape = _guide_similarity(template.column_guides, detection.column_guides)
        if template.reference_page_aspect:
            aspect_score = max(0.0, 1.0 - abs(page_aspect - template.reference_page_aspect) / max(page_aspect, template.reference_page_aspect))
        else:
            aspect_score = 0.5
        score = (
            .27 * row_score + .31 * column_score + .15 * rect_score
            + .10 * aspect_score + .17 * ((row_shape + column_shape) / 2)
        )
        if title_candidates:
            # Title disambiguates compatible layouts, never rescues a bad grid.
            score *= 1.0 if template.id in title_candidates else .70
        if fit_error:
            score *= .49
        elif detection.warnings:
            score = min(score, .49)
        reasons = []
        if template.id in title_candidates:
            reasons.append(tr('Printed title matches the protocol'))
        elif title_candidates:
            reasons.append(tr('Printed title points to another protocol'))
        reasons += [
            tr('сетка {p0}×{p1}', p0=template.rows, p1=template.columns),
            tr('число строк совпадает') if len(detection.row_guides) == len(template.row_guides) else
            (tr('число строк будет подогнано') if template.auto_fit_rows and row_score == 1 else tr('число строк отличается')),
            tr('число столбцов совпадает') if column_score == 1 else tr('число столбцов отличается'),
        ]
        if aspect_score > .9:
            reasons.append(tr('пропорции страницы совпадают'))
        if rect_score > .85:
            reasons.append(tr('положение таблицы похоже'))
        matches.append(TemplateMatch(template, round(max(0.0, min(1.0, score)), 4), tuple(reasons), fitted, fit_error))
    matches.sort(key=lambda item: (not item.fit_error, item.score), reverse=True)
    if len(matches)>1 and not matches[1].fit_error and abs(matches[0].score-matches[1].score)<.04:
        matches = [replace(m, reasons=(*m.reasons,tr('Similar layouts: confirm the protocol name.'))) for m in matches]
    return matches


def rank_document_templates(images, first_detection, templates):
    remaining = []
    if any(template.auto_fit_rows for template in templates):
        for index in range(1, len(images)):
            page = images[index]
            try:
                grid = detect_grid(page)
            except ValueError:
                grid = None
            remaining.append((page.shape[1] / page.shape[0], grid))
    return rank_templates(images[0].shape, first_detection, templates, remaining_pages=remaining)
