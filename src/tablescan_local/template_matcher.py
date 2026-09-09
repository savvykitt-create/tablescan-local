"""Explainable, local-only matching of saved templates to a detected grid."""
from __future__ import annotations

from dataclasses import dataclass
from math import exp

from .domain import TableTemplate
from .imaging import GridDetection


@dataclass(frozen=True, slots=True)
class TemplateMatch:
    template: TableTemplate
    score: float
    reasons: tuple[str, ...]


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
) -> list[TemplateMatch]:
    """Rank the latest saved version in every family and explain each score."""
    height, width = image_shape[:2]
    page_aspect = width / max(1, height)
    matches = []
    for template in latest_template_versions(templates):
        row_score = _count_similarity(len(detection.row_guides) - 1, template.rows)
        column_score = _count_similarity(len(detection.column_guides) - 1, template.columns)
        rect_score = _rect_similarity(template, detection)
        row_shape = _guide_similarity(template.row_guides, detection.row_guides)
        column_shape = _guide_similarity(template.column_guides, detection.column_guides)
        if template.reference_page_aspect:
            aspect_score = max(0.0, 1.0 - abs(page_aspect - template.reference_page_aspect) / max(page_aspect, template.reference_page_aspect))
        else:
            aspect_score = 0.5
        score = (
            .27 * row_score + .31 * column_score + .15 * rect_score
            + .10 * aspect_score + .17 * ((row_shape + column_shape) / 2)
        )
        reasons = [
            f"сетка {template.rows}×{template.columns}",
            "число строк совпадает" if row_score == 1 else "число строк отличается",
            "число столбцов совпадает" if column_score == 1 else "число столбцов отличается",
        ]
        if aspect_score > .9:
            reasons.append("пропорции страницы совпадают")
        if rect_score > .85:
            reasons.append("положение таблицы похоже")
        matches.append(TemplateMatch(template, round(max(0.0, min(1.0, score)), 4), tuple(reasons)))
    return sorted(matches, key=lambda item: item.score, reverse=True)

