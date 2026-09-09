"""Conservative table-level checks that flag, but never rewrite, OCR output."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from statistics import median

from .domain import PageResult, TableTemplate
from .ocr import is_simple_number


def _number(text: str) -> float | None:
    if not is_simple_number(text):
        return None
    try:
        value = Decimal(text.replace(",", ".").replace("−", "-"))
    except InvalidOperation:
        return None
    return float(value) if value.is_finite() else None


def flag_table_outliers(page: PageResult, template: TableTemplate) -> None:
    """Flag extreme within-row surprises when enough comparable cells exist.

    Cells are grouped by their applied template rule.  The threshold combines
    median absolute deviation with a wide relative floor, so this check catches
    gross digit confusions without assuming that normal biological variation is
    an OCR error.  It never changes ``final_text`` or confirmation status.
    """
    for row in range(template.header_rows, template.rows):
        row_cells = [
            cell for cell in page.cells
            if cell.row == row and cell.column >= template.row_label_columns and cell.status != "excluded"
        ]
        groups: dict[str, list[tuple[object, float]]] = {}
        for cell in row_cells:
            value = _number(cell.final_text)
            if value is not None and cell.applied_rule:
                groups.setdefault(cell.applied_rule, []).append((cell, value))
        for items in groups.values():
            if len(items) < 5:
                continue
            values = [value for _, value in items]
            center = float(median(values))
            deviation = float(median(abs(value - center) for value in values))
            threshold = max(10.0, abs(center) * .45, deviation * 1.4826 * 4.5)
            for cell, value in items:
                if abs(value - center) <= threshold:
                    continue
                cell.flags = sorted(set([*cell.flags, "table_outlier"]))
                constraints, _ = template.value_constraints(cell.row, cell.column)
                alternatives = [candidate.strip() for candidate in cell.alternatives.split(" | ")]
                if any(
                    (number := _number(candidate)) is not None
                    and not constraints.hard_errors(candidate)
                    and abs(number - center) <= threshold
                    for candidate in alternatives
                ):
                    cell.flags = sorted(set([*cell.flags, "table_outlier_with_plausible_alternative"]))
