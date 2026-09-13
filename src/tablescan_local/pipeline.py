from __future__ import annotations

from .i18n import tr, fmt, join_text
import tempfile
from math import ceil
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .domain import CellResult, FieldRegion, FieldResult, JobResult, NormalizedRect, PageResult, TableTemplate
from .imaging import cell_crop_bundle, crop_normalized, detect_crossed_rows, write_image
from .ocr import LocalOcrEngine, is_complex_number, is_simple_number
from .table_checks import flag_table_outliers
from .writer_adapter import apply_writer_adaptation


ProgressCallback = Callable[[int, int, str], None]


def _non_numeric_mark_evidence(cell: CellResult) -> bool:
    """Recognize the OCR signature of a cancellation scribble, not a value."""
    raw = (cell.raw_text or "").strip()
    if not raw or is_complex_number(raw):
        return False

    def looks_non_numeric(value: str) -> bool:
        value = value.strip()
        if not value or is_complex_number(value):
            return False
        digits = sum(character.isdigit() for character in value)
        other = sum(
            character.isalpha() or character not in ".,+-−<>≤≥=%/ "
            for character in value
        )
        return other >= max(1, digits)

    alternatives = [value.strip() for value in cell.alternatives.split(" | ") if value.strip()]
    non_numeric_alternatives = sum(looks_non_numeric(value) for value in alternatives)
    unstable = (
        cell.confidence < .90
        or bool({"low_confidence", "unstable_consensus", "model_disagreement"} & set(cell.flags))
    )
    return looks_non_numeric(raw) and non_numeric_alternatives >= 2 and unstable


def detect_non_numeric_mark_rows(cells: list[CellResult], template: TableTemplate) -> list[int]:
    """Find rows whose numeric cells consistently look like words/scribbles.

    At least 80% of two or more numeric data cells must independently carry
    the same unstable non-numeric OCR signature.  This deliberately cannot
    turn one difficult handwritten value into an excluded row.
    """
    excluded: list[int] = []
    for row in range(template.rows):
        candidates = []
        for cell in cells:
            if cell.row != row or cell.column < template.row_label_columns or not cell.applied_rule:
                continue
            constraints, _ = template.value_constraints(cell.row, cell.column)
            if constraints.value_format in {"numeric", "integer", "complex_numeric"}:
                candidates.append(cell)
        if len(candidates) < 2:
            continue
        required = max(2, ceil(len(candidates) * .80))
        if sum(_non_numeric_mark_evidence(cell) for cell in candidates) >= required:
            excluded.append(row)
    return excluded


def _check_rule(value: str, template: TableTemplate, column: int) -> list[str]:
    if column >= len(template.column_rules):
        return []
    rule = template.column_rules[column].constraints()
    return rule.hard_errors(value) + rule.warnings(value)


def _write_crop(crop: np.ndarray, directory: Path, name: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    write_image(target, crop)
    return str(target)


def process_page(
    image: np.ndarray,
    source_path: str,
    page_index: int,
    template: TableTemplate,
    engine: LocalOcrEngine,
    progress: ProgressCallback | None = None,
    crop_directory: Path | None = None,
) -> PageResult:
    template.ensure_column_rules()
    template.validate_value_rules()
    crop_directory = crop_directory or Path(tempfile.mkdtemp(prefix="tablescan-crops-"))
    suspected_rows = detect_crossed_rows(image, template)
    # A row-wide connected stroke is stronger evidence than any per-cell OCR
    # guess.  Keep the OCR proposal for reversible inspection, but expose and
    # export the data cells as empty by default.
    excluded_rows: list[int] = sorted(set(suspected_rows))
    total = template.rows * template.columns + len(template.fields)
    completed = 0

    fields: list[FieldResult] = []
    for region in template.fields:
        crop = crop_normalized(image, region.rect)
        crop_path = _write_crop(crop, crop_directory, f"page-{page_index + 1}-field-{region.id}.png")
        if region.source == "fixed":
            value = region.fixed_value
            confidence = 1.0
            flags: list[str] = []
        else:
            numeric = region.kind in {"integer", "numeric", "complex_numeric"}
            recognized = engine.recognize_region(crop, numeric=numeric)
            value, confidence, flags = recognized.text, recognized.confidence, list(recognized.flags or [])
            if not region.required and not value:
                flags = [flag for flag in flags if flag != "empty_prediction"]
        flags.extend(region.hard_errors(value))
        fields.append(FieldResult(
            region.id, region.name, value if region.source == "fixed" else recognized.raw_text,
            value, confidence, crop_path, sorted(set(flags)),
            alternatives="" if region.source == "fixed" else recognized.alternative,
        ))
        completed += 1
        if progress:
            progress(completed, total, tr('Recognizing field {p0}', p0=region.name))

    cells: list[CellResult] = []
    for row in range(template.rows):
        for column in range(template.columns):
            crop_variants, context_crop = cell_crop_bundle(image, template, row, column)
            crop = crop_variants[0]
            # Keep ownership-masked context as OCR diagnostic data. The review
            # workspace independently crops the original loaded page, so masks
            # cannot replace or distort the source shown to the reviewer.
            crop_path = _write_crop(crop, crop_directory, f"page-{page_index + 1}-r{row + 1}-c{column + 1}.png")
            preview_crop_path = (
                _write_crop(context_crop, crop_directory, f"page-{page_index + 1}-r{row + 1}-c{column + 1}-context.png")
                if context_crop is not None else ""
            )
            rule = template.column_rules[column]
            if rule.role == "ignored":
                result = CellResult(row, column, "", "", 1.0, crop_path, [], "excluded")
            else:
                constraints, rule_name = template.cell_constraints(row, column)
                active = constraints is not None
                recognition_rule = constraints
                if recognition_rule is None and row >= template.header_rows and template.is_label_cell(column):
                    # A numeric recognizer still helps read digit-only identifiers.
                    # This is an OCR hint, not permission to coerce their stored
                    # text or reject a reviewer entering an alphanumeric label.
                    recognition_rule = rule.constraints()
                numeric = recognition_rule is not None and recognition_rule.value_format in {"numeric", "integer", "complex_numeric"}
                recognized = engine.recognize_cell(
                    crop,
                    numeric=numeric,
                    constraints=recognition_rule,
                    retry_crops=crop_variants[1:] if numeric else None,
                )
                flags = list(recognized.flags or [])
                crossed_data_cell = row in excluded_rows and column >= template.row_label_columns
                if crossed_data_cell:
                    flags.extend(("crossed_out_row", "auto_excluded_crossed_row"))
                result = CellResult(
                    row=row,
                    column=column,
                    raw_text=recognized.raw_text,
                    final_text="" if crossed_data_cell else recognized.text,
                    confidence=recognized.confidence,
                    crop_path=crop_path,
                    flags=sorted(set(flags)),
                    status="excluded" if crossed_data_cell else "automatic",
                    alternatives=recognized.alternative,
                    applied_rule=f"{rule_name}: {constraints.summary()}" if active else "",
                    suggested_text=recognized.text if crossed_data_cell else "",
                    candidate_confidences=dict(recognized.candidate_confidences),
                    candidate_scores=dict(recognized.candidate_scores),
                    ranking_scores=dict(recognized.ranking_scores),
                    preview_crop_path=preview_crop_path,
                )
            cells.append(result)
            completed += 1
            if progress:
                progress(completed, total, tr('Recognizing cell {p0}, {p1}', p0=row + 1, p1=column + 1))

    # Wavy cancellations are often broken at cell borders and therefore do
    # not form one row-wide geometric component.  OCR nevertheless gives a
    # very distinctive, repeated signature (words/symbols instead of numbers)
    # across the row.  Use that conservative consensus only after every cell
    # has been read, and keep the numeric proposals for reversible inspection.
    for row in detect_non_numeric_mark_rows(cells, template):
        if row not in excluded_rows:
            excluded_rows.append(row)
        for cell in cells:
            if cell.row != row or cell.column < template.row_label_columns:
                continue
            if cell.final_text:
                cell.suggested_text = cell.final_text
            cell.final_text = ""
            cell.status = "excluded"
            cell.flags = sorted(set([*cell.flags, "crossed_out_row", "auto_excluded_crossed_row", "non_numeric_mark_row"]))
    excluded_rows.sort()

    page = PageResult(page_index, source_path, cells, fields, excluded_rows)
    apply_writer_adaptation(page, template, getattr(engine, "_digit_verifier", None))
    flag_table_outliers(page, template)
    return page


def process_document(
    images: list[np.ndarray],
    source_path: str,
    template: TableTemplate,
    progress: ProgressCallback | None = None,
    crop_root: Path | None = None,
    high_accuracy: bool = True,
    slow_mode: bool = False,
) -> JobResult:
    slow_config = None
    if slow_mode:
        from .slow_mode import runtime_config
        slow_config = runtime_config()
    engine = LocalOcrEngine(high_accuracy=high_accuracy or slow_mode)
    pages = []
    for index, image in enumerate(images):
        page_crop_root = crop_root / f"page-{index + 1}" if crop_root else None
        page = process_page(image, source_path, index, template, engine, progress, page_crop_root)
        if slow_mode:
            from .slow_mode import refine_page
            directory = (page_crop_root or Path(tempfile.mkdtemp(prefix='tablescan-slow-'))) / 'slow-mode'
            refine_page(image, page, template, directory, slow_config, progress)
            flag_table_outliers(page, template)
        pages.append(page)
    return JobResult(source_path, template, pages, engine.model_version + ('+slow-qwen-glm-v1' if slow_mode else ''))


def suggest_standard_fields(image: np.ndarray, template: TableTemplate, engine: LocalOcrEngine | None = None) -> list[FieldRegion]:
    from uuid import uuid4

    engine = engine or LocalOcrEngine()
    height, width = image.shape[:2]
    suggestions: list[FieldRegion] = []
    for box, raw_text, _ in engine.detect_text_boxes(image):
        text = "".join(character for character in raw_text.upper() if character.isalpha())
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        x1, x2 = max(0, min(xs) - 8), min(width, max(xs) + 8)
        y1, y2 = max(0, min(ys) - 8), min(height, max(ys) + 8)
        if text == "PLANTAR":
            rect = (round(x1), round(y1), round(x2 - x1), round(y2 - y1))
            suggestions.append(FieldRegion(str(uuid4()), "Test", NormalizedRect.from_pixels(rect, image.shape), required=True, color="#DC2626"))
        elif text in {"DATE", "WEEK"}:
            available = width * (0.30 if text == "DATE" else 0.18)
            rect = (round(x2 + 5), round(y1 - 3), round(min(available, width - x2 - 5)), round(max(32, y2 - y1 + 6)))
            suggestions.append(FieldRegion(str(uuid4()), text.title(), NormalizedRect.from_pixels(rect, image.shape), kind="date" if text == "DATE" else "integer", recognition="handwritten", color="#2563EB"))
        elif text in {"LEFTHIND", "RIGHTHIND"}:
            left = text == "LEFTHIND"
            rect = (round(x1), round(y1), round(x2 - x1), round(y2 - y1))
            column_start = template.row_label_columns if left else max(template.row_label_columns, template.columns // 2)
            column_end = max(column_start, template.columns // 2 - 1) if left else template.columns - 1
            fixed = "Left Hind" if left else "Right Hind"
            suggestions.append(
                FieldRegion(
                    str(uuid4()),
                    "Side",
                    NormalizedRect.from_pixels(rect, image.shape),
                    source="fixed",
                    fixed_value=fixed,
                    export_mode="column_group",
                    column_start=column_start,
                    column_end=column_end,
                    color="#0F766E",
                )
            )
    return suggestions
