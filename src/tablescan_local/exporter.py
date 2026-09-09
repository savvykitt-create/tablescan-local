from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from .domain import CellResult, FieldRegion, JobResult, PageResult
from .ocr import is_simple_number


def _excel_value(text: str) -> Any:
    value = text.strip()
    if not value:
        return None
    if is_simple_number(value):
        normalized = value.replace("−", "-").replace(",", ".")
        number = float(normalized)
        return int(number) if number.is_integer() and "." not in normalized else number
    return value


def _field_result(page: PageResult, region: FieldRegion) -> str:
    result = next((item for item in page.fields if item.region_id == region.id), None)
    return result.final_text if result else ""


def _group_value(page: PageResult, regions: list[FieldRegion], column: int) -> str:
    for region in regions:
        if region.export_mode != "column_group":
            continue
        if region.column_start is not None and region.column_end is not None and region.column_start <= column <= region.column_end:
            return _field_result(page, region)
    return ""


def _cell(page: PageResult, row: int, column: int) -> CellResult | None:
    return page.cell(row, column)


def _separator_evidence(raw_text: str, final_text: str, flags: list[str]) -> str:
    if "," in raw_text:
        return "comma → canonical dot"
    if "." in raw_text:
        return "dot"
    if "separator_reclassified_from_one" in flags:
        return "OCR 1 → proposed dot from template"
    if "separator_inferred_from_rule" in flags:
        return "missing → proposed dot from template"
    if "." in final_text:
        return "dot not present in primary OCR"
    return ""


def _layout_sheet_title(page_index: int) -> str:
    return "Исходная таблица" if page_index == 0 else f"Исходная таблица {page_index + 1}"


def _add_layout_sheet(workbook: Workbook, page: PageResult, job: JobResult) -> None:
    """Add the reviewed cell matrix in the same row/column layout as the scan."""
    sheet = workbook.create_sheet(_layout_sheet_title(page.page_index))
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = f"A{job.template.header_rows + 1}" if job.template.header_rows else None

    thin = Side(style="thin", color="AEB7C4")
    grid_border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="E8EEF8")
    label_fill = PatternFill("solid", fgColor="F4F6F8")
    excluded_fill = PatternFill("solid", fgColor="ECEFF3")

    for row in range(job.template.rows):
        excluded = row in page.excluded_rows
        for column in range(job.template.columns):
            result = page.cell(row, column)
            is_excluded_value = bool(
                result and (result.status == "excluded" or (excluded and column >= job.template.row_label_columns))
            )
            value = None if result is None or is_excluded_value else _excel_value(result.final_text)
            cell = sheet.cell(row=row + 1, column=column + 1, value=value)
            cell.border = grid_border
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if row < job.template.header_rows:
                cell.fill = header_fill
                cell.font = Font(bold=True, color="1F2937")
            elif column < job.template.row_label_columns:
                cell.fill = label_fill
                cell.font = Font(bold=True, color="1F2937")
            elif is_excluded_value:
                cell.fill = excluded_fill
            if isinstance(value, float):
                cell.number_format = "0.0" if result and any(mark in result.final_text for mark in ".,") else "0.########"

    column_spans = [
        job.template.column_guides[index + 1] - job.template.column_guides[index]
        for index in range(job.template.columns)
    ]
    median_column = sorted(column_spans)[len(column_spans) // 2] if column_spans else 1
    for index, span in enumerate(column_spans, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = max(8, min(28, 12 * span / max(median_column, 1e-9)))

    row_spans = [
        job.template.row_guides[index + 1] - job.template.row_guides[index]
        for index in range(job.template.rows)
    ]
    median_row = sorted(row_spans)[len(row_spans) // 2] if row_spans else 1
    for index, span in enumerate(row_spans, start=1):
        sheet.row_dimensions[index].height = max(18, min(42, 21 * span / max(median_row, 1e-9)))

    sheet.print_area = f"A1:{get_column_letter(job.template.columns)}{job.template.rows}"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True


def export_job(job: JobResult, target: str | Path) -> Path:
    job.template.validate_value_rules()
    if job.unresolved_count:
        raise ValueError(f"{job.unresolved_count} values still need review")
    for page in job.pages:
        for cell in page.cells:
            if cell.applied_rule and cell.status != "excluded" and cell.row not in page.excluded_rows:
                rule, name = job.template.value_constraints(cell.row, cell.column)
                if rule.hard_errors(cell.final_text):
                    raise ValueError(f"R{cell.row + 1}C{cell.column + 1}: значение не соответствует правилу «{name}»")

    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    data_sheet = workbook.active
    data_sheet.title = "Data"

    repeated_regions = [region for region in job.template.fields if region.export_mode == "repeat"]
    group_regions = [region for region in job.template.fields if region.export_mode == "column_group"]
    repeated_names = list(dict.fromkeys(region.name for region in repeated_regions))
    headers = ["Source file", "Page", *repeated_names]
    if group_regions:
        headers.append("Side")
    headers.extend(["Source row", "Measurement", "Value", "Review status", "Confidence"])
    data_sheet.append(headers)

    for page in job.pages:
        repeated_values: dict[str, str] = {}
        for name in repeated_names:
            matching = next((region for region in repeated_regions if region.name == name), None)
            repeated_values[name] = _field_result(page, matching) if matching else ""

        for row in range(job.template.header_rows, job.template.rows):
            row_label_parts = []
            for column in range(job.template.row_label_columns):
                result = _cell(page, row, column)
                if result and result.final_text:
                    row_label_parts.append(result.final_text)
            row_label = " ".join(row_label_parts) or str(row + 1 - job.template.header_rows)

            if row in page.excluded_rows:
                record = [job.source_name, page.page_index + 1, *[repeated_values[name] for name in repeated_names]]
                if group_regions:
                    record.append("")
                record.extend([row_label, "", None, "excluded", 1.0])
                data_sheet.append(record)
                continue

            for column in range(job.template.row_label_columns, job.template.columns):
                rule = job.template.column_rules[column]
                if rule.role != "data":
                    continue
                result = _cell(page, row, column)
                if result is None or not result.final_text:
                    continue
                header_parts = []
                for header_row in range(job.template.header_rows):
                    header_cell = _cell(page, header_row, column)
                    if header_cell and header_cell.final_text:
                        header_parts.append(header_cell.final_text)
                measurement = rule.name if not rule.name.startswith("Column ") else " / ".join(header_parts) or str(column + 1)
                record = [job.source_name, page.page_index + 1, *[repeated_values[name] for name in repeated_names]]
                if group_regions:
                    record.append(_group_value(page, group_regions, column))
                record.extend([row_label, measurement, _excel_value(result.final_text), result.status, round(result.confidence, 4)])
                data_sheet.append(record)

    for page in job.pages:
        _add_layout_sheet(workbook, page, job)

    audit_sheet = workbook.create_sheet("Audit")
    audit_headers = [
        "Source file", "Page", "Kind", "Address", "Raw value", "Final value", "Confidence",
        "Flags", "Status", "Template", "Template version", "Model version", "Exported at UTC",
        "Decimal separator evidence", "Applied value rule", "Alternative readings",
    ]
    audit_sheet.append(audit_headers)
    exported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for page in job.pages:
        for item in page.fields:
            audit_sheet.append([
                job.source_name, page.page_index + 1, "field", item.name, item.raw_text, item.final_text,
                round(item.confidence, 4), ", ".join(item.flags), item.status, job.template.name,
                job.template.template_version, job.model_version, exported_at,
                _separator_evidence(item.raw_text, item.final_text, item.flags), "", item.alternatives,
            ])
        for item in page.cells:
            audit_sheet.append([
                job.source_name, page.page_index + 1, "cell", f"R{item.row + 1}C{item.column + 1}",
                item.raw_text, item.final_text, round(item.confidence, 4), ", ".join(item.flags), item.status,
                job.template.name, job.template.template_version, job.model_version, exported_at,
                _separator_evidence(item.raw_text, item.final_text, item.flags), item.applied_rule, item.alternatives,
            ])

    for sheet in (data_sheet, audit_sheet):
        # OCR and user text are data, never executable workbook formulas.
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1D4ED8")
            cell.alignment = Alignment(vertical="center")
        for column_cells in sheet.columns:
            width = min(36, max(10, max(len(str(cell.value or "")) for cell in column_cells) + 2))
            sheet.column_dimensions[column_cells[0].column_letter].width = width
        if sheet.max_row > 1:
            table = Table(displayName=f"{sheet.title.replace(' ', '')}Table", ref=sheet.dimensions)
            table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True, showFirstColumn=False, showLastColumn=False)
            sheet.add_table(table)
    workbook.save(target_path)
    load_workbook(target_path, read_only=True, data_only=False).close()
    with ZipFile(target_path) as archive:
        broken_member = archive.testzip()
        if broken_member:
            raise ValueError(f"Повреждён внутренний файл Excel: {broken_member}")
    return target_path
