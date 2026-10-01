from __future__ import annotations

from .i18n import tr, fmt, join_text
import tempfile
import time
from math import ceil
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .domain import CellResult, FieldRegion, FieldResult, JobResult, NormalizedRect, PageResult, TableTemplate
from .imaging import cell_crop_bundle, crop_normalized, detect_crossed_rows, write_image
from .ocr import LocalOcrEngine, is_complex_number, is_simple_number
from .table_checks import flag_table_outliers


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
            if cell.row != row or cell.column < template.row_label_columns or not cell.applied_rule or template.fixed_value(cell.row, cell.column) is not None:
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
    # A shifted printed border can look exactly like a cancellation. Keep the
    # readings and require Review; only the reviewer may exclude a whole row.
    excluded_rows: list[int] = []
    total = sum(template.fixed_value(r, c) is None and template.column_rules[c].role != "ignored"
                for r in range(template.rows) for c in range(template.columns))
    total += sum(region.source != "fixed" for region in template.fields)
    completed = 0

    fields: list[FieldResult] = []
    for region in template.fields:
        crop_path = ""
        if region.source != "fixed":
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
        if region.source != "fixed":
            completed += 1
            if progress:
                progress(completed, total, tr('Recognizing field {p0}', p0=region.name))

    cells: list[CellResult] = []
    active_cell = [1, 1]
    def requests():
        for row in range(template.rows):
            for column in range(template.columns):
                fixed = template.fixed_value(row, column)
                if fixed is not None:
                    cells.append(CellResult(row, column, "", fixed, 1.0,
                                            flags=["template_fixed_value"], applied_rule="Value from template"))
                    continue
                if template.column_rules[column].role == "ignored":
                    cells.append(CellResult(row, column, "", "", 1.0, status="excluded"))
                    continue
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
                constraints, rule_name = template.cell_constraints(row, column)
                active = constraints is not None
                recognition_rule = constraints
                if recognition_rule is None and row >= template.header_rows and template.is_label_cell(column):
                    # A numeric recognizer still helps read digit-only identifiers.
                    # This is an OCR hint, not permission to coerce their stored
                    # text or reject a reviewer entering an alphanumeric label.
                    recognition_rule = rule.constraints()
                numeric = recognition_rule is not None and recognition_rule.value_format in {"numeric", "integer", "complex_numeric"}
                active_cell[:] = [row + 1, column + 1]
                yield (row, column, crop_path, preview_crop_path, active, rule_name, constraints), dict(
                    crop=crop, numeric=numeric, constraints=recognition_rule,
                    retry_crops=crop_variants[1:] if numeric else None,
                )

    from .parallel_ocr import CellWorkers
    workers = getattr(engine, '_cell_workers', None)
    def heartbeat():
        if progress:
            progress(completed, total, tr('Recognizing cell {p0}, {p1}', p0=active_cell[0], p1=active_cell[1]))
    stream = (workers.map(requests(), engine, heartbeat) if isinstance(workers, CellWorkers) else
              ((metadata, engine.recognize_cell(**args)) for metadata, args in requests()))
    try:
        for metadata, recognized in stream:
            row, column, crop_path, preview_crop_path, active, rule_name, constraints = metadata
            flags = list(recognized.flags or [])
            crossed_data_cell = row in suspected_rows and column >= template.row_label_columns
            if crossed_data_cell:
                flags.append("suspected_crossed_row")
            result = CellResult(
                row=row,
                column=column,
                raw_text=recognized.raw_text,
                final_text=recognized.text,
                confidence=recognized.confidence,
                crop_path=crop_path,
                flags=sorted(set(flags)),
                status="automatic",
                alternatives=recognized.alternative,
                applied_rule=f"{rule_name}: {constraints.summary()}" if active else "",
                suggested_text="",
                candidate_confidences=dict(recognized.candidate_confidences),
                candidate_scores=dict(recognized.candidate_scores),
                ranking_scores=dict(recognized.ranking_scores),
                preview_crop_path=preview_crop_path,
            )
            cells.append(result)
            completed += 1
            if progress:
                progress(completed, total, tr('Recognizing cell {p0}, {p1}', p0=row + 1, p1=column + 1))
    finally:
        stream.close()
    cells.sort(key=lambda cell: (cell.row, cell.column))

    # Repeated OCR failures are a reason to inspect a row, never proof that
    # measurements should disappear. This also catches wavy cancellations.
    for row in detect_non_numeric_mark_rows(cells, template) if template.detect_crossed_rows else []:
        for cell in cells:
            if cell.row != row or cell.column < template.row_label_columns or template.fixed_value(cell.row, cell.column) is not None:
                continue
            cell.flags = sorted(set([*cell.flags, "suspected_crossed_row", "non_numeric_mark_row"]))
    excluded_rows.sort()

    page = PageResult(page_index, source_path, cells, fields, excluded_rows)
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
    started = time.monotonic()
    from .template_fit import fit_document_template
    template = fit_document_template(template, images)
    template.ensure_column_rules()
    slow_config = None
    if slow_mode:
        from .slow_mode import runtime_config
        slow_config = runtime_config()
    needs_ocr = (any(field.source != "fixed" for field in template.fields)
                 or any(template.fixed_value(r, c) is None and template.column_rules[c].role != "ignored"
                        for r in range(template.rows) for c in range(template.columns)))
    engine = LocalOcrEngine(high_accuracy=high_accuracy or slow_mode) if needs_ocr else None
    model_version = engine.model_version if engine else "template-values"
    from .resource_policy import resources, cpu_workers
    from .parallel_ocr import CellWorkers
    from .ocr_runtime import acceleration_provider
    from .numeric_decoder import _native_search
    state = resources()
    count = 1
    pool = None
    # Test adapters/custom engines retain their own recognition semantics.
    from .ocr import LocalOcrEngine as NativeEngine
    if isinstance(engine, NativeEngine) and (high_accuracy or slow_mode):
        cells = sum(template.fixed_value(r, c) is None and template.column_rules[c].role != 'ignored'
                    for r in range(template.rows) for c in range(template.columns)) * len(images)
        count = cpu_workers(state, cells, accelerated=acceleration_provider() is not None)
        if count > 1:
            pool = CellWorkers(count)
            engine._cell_workers = pool
    performance = {'resources': state.to_dict(), 'cpu_workers': count,
                   'ocr_provider_available': (acceleration_provider() or ('CPUExecutionProvider',))[0],
                   'decoder': 'native' if _native_search else 'python', 'primary_pages_seconds': []}
    pages = []
    try:
        for index, image in enumerate(images):
            page_started = time.monotonic()
            page_crop_root = crop_root / f"page-{index + 1}" if crop_root else None
            pages.append(process_page(image, source_path, index, template, engine, progress, page_crop_root))
            performance['primary_pages_seconds'].append(time.monotonic() - page_started)
    finally:
        if pool:
            performance['cpu_fallbacks'] = pool.fallbacks
            performance['cpu_parallel_cells'] = pool.completed
            pool.close()
    # ONNX sessions must not compete with the much larger Slow models for RAM.
    # Finish primary OCR first, then release all sessions before starting Qwen/GLM.
    del engine
    if slow_mode:
        import gc
        from .slow_mode import refine_page, model_session
        gc.collect()
        with model_session():
            for index, (image, page) in enumerate(zip(images, pages)):
                if progress:
                    progress(0, 1, str(tr('Preparing Slow verification')))
                page_crop_root = crop_root / f"page-{index + 1}" if crop_root else None
                directory = (page_crop_root or Path(tempfile.mkdtemp(prefix='tablescan-slow-'))) / 'slow-mode'
                refine_page(image, page, template, directory, slow_config, progress)
                flag_table_outliers(page, template)
    performance['total_seconds'] = time.monotonic() - started
    if crop_root:
        import json
        crop_root.mkdir(parents=True, exist_ok=True)
        (crop_root / 'performance.json').write_text(json.dumps(performance, indent=2), encoding='utf-8')
    return JobResult(source_path, template, pages, model_version + ('+slow-qwen-glm-v1' if slow_mode else ''), performance)



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
