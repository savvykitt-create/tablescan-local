"""Editable, printable forms from templates and original bundled workbooks."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QSizePolicy, QVBoxLayout

from .domain import TableTemplate
from .button_help import HelpButton
from .exporter import _save_workbook
from .i18n import tr
from .localized_widgets import (QComboBox, QDialog, QFileDialog, QFormLayout,
                                QGroupBox, QLabel, QPushButton)
from .ui_common import notify


@dataclass(frozen=True)
class PrintOptions:
    paper: str = "A4"
    orientation: str = "auto"
    single_page: bool = True


def _orientation(template: TableTemplate, options: PrintOptions) -> str:
    if options.orientation != "auto":
        return options.orientation
    aspect = template.reference_page_aspect
    if aspect is None:
        aspect = template.table_rect.width / max(template.table_rect.height, .001)
    return "landscape" if aspect > 1 else "portrait"


def export_template_excel(template: TableTemplate, target: str | Path,
                          options: PrintOptions = PrintOptions()) -> Path:
    """Write a blank form, keeping fixed text and relative grid proportions.

    OCR rules and recognition exclusions do not remove the printed grid or its
    fixed labels. Excluded empty cells receive a light fill. No scan pixels or
    previously recognized values are included. The source template is untouched.
    """
    template = deepcopy(template)
    if options.paper not in {"A4", "Letter"} or options.orientation not in {"auto", "portrait", "landscape"}:
        raise ValueError(tr("Choose A4 or Letter paper and a valid page orientation."))
    if not 1 <= template.rows <= 10000 or not 1 <= template.columns <= 256:
        raise ValueError(tr("The printable grid must contain 1–10000 rows and 1–256 columns."))
    for guides in (template.row_guides, template.column_guides):
        if any(not math.isfinite(value) for value in guides) or any(b <= a for a, b in zip(guides, guides[1:])):
            raise ValueError(tr("Grid lines must be in increasing order before exporting."))
    template.validate_value_rules()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Printable form"
    sheet.sheet_view.showGridLines = False
    last_column = get_column_letter(template.columns)

    def full_width_row(row: int, value: str, *, title: bool = False) -> None:
        if template.columns > 1:
            sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=template.columns)
        cell = sheet.cell(row, 1, value)
        cell.font = Font(name="Calibri", size=16 if title else 11, bold=title)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.row_dimensions[row].height = 30 if title else 25

    full_width_row(1, template.name, title=True)
    row = 2
    for field in template.fields:
        label = field.printed_label.strip() or field.name
        value = field.fixed_value if field.source == "fixed" else "________________________________"
        full_width_row(row, f"{label}: {value}")
        row += 1
    sheet.row_dimensions[row].height = 10
    grid_start = row + 1
    thin = Side(style="thin", color="444444")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    fixed = {(cell.row, cell.column): cell.value for cell in template.fixed_cells}
    excluded = template.exclusion_mask()
    for r in range(template.rows):
        for c in range(template.columns):
            value = fixed.get((r, c))
            if value is None and template.header_rows and r == template.header_rows - 1:
                value = template.column_rules[c].name
            cell = sheet.cell(grid_start + r, c + 1, value)
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.font = Font(name="Calibri", size=11, bold=r < template.header_rows)
            if r < template.header_rows:
                cell.fill = PatternFill("solid", fgColor="E9EDF2")
            elif (r, c) in excluded and value is None:
                cell.fill = PatternFill("solid", fgColor="F0F0F0")
    spans = [b - a for a, b in zip(template.column_guides, template.column_guides[1:])]
    # Excel widths use character units. Scaling the whole grid to a printable
    # page retains the source proportions without imposing an OCR-sized font.
    total_width = 140 if _orientation(template, options) == "landscape" else 95
    for c, span in enumerate(spans, 1):
        sheet.column_dimensions[get_column_letter(c)].width = max(2, total_width * span / sum(spans))
    heights = [b - a for a, b in zip(template.row_guides, template.row_guides[1:])]
    median = sorted(heights)[len(heights) // 2]
    for r, span in enumerate(heights, grid_start):
        sheet.row_dimensions[r].height = max(14, min(100, 27 * span / median))
    last_row = grid_start + template.rows - 1
    sheet.print_area = f"A1:{last_column}{last_row}"
    if template.header_rows:
        sheet.print_title_rows = f"{grid_start}:{grid_start + template.header_rows - 1}"
    sheet.freeze_panes = f"A{grid_start + template.header_rows}"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4 if options.paper == "A4" else sheet.PAPERSIZE_LETTER
    sheet.page_setup.orientation = _orientation(template, options)
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 1 if options.single_page else 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_options.horizontalCentered = True
    sheet.page_margins = PageMargins(left=.25, right=.25, top=.3, bottom=.3, header=.1, footer=.1)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        return _save_workbook(workbook, target)
    finally:
        workbook.close()


@dataclass(frozen=True)
class BundledForm:
    key: str
    name: str


_BUNDLED = Path(__file__).parent / "default_templates"


def bundled_forms() -> list[BundledForm]:
    """The same originals used by packaging/make_forms_archive.py on GitHub."""
    return sorted((BundledForm(path.stem, json.loads(path.read_text(encoding="utf-8"))["name"])
                   for path in _BUNDLED.glob("*.json") if path.with_suffix(".xlsx").is_file()),
                  key=lambda item: item.name.casefold())


def save_bundled_form(key: str, target: str | Path, extension: str = "xlsx") -> Path:
    if key not in {form.key for form in bundled_forms()} or extension not in {"xlsx", "pdf"}:
        raise ValueError(tr("Choose a bundled Excel or PDF form."))
    data = (_BUNDLED / f"{key}.{extension}").read_bytes()
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Preserve a previous download even when copying fails or the disk is full.
    with NamedTemporaryFile(dir=target.parent, prefix=f".{target.stem}-", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _help(parent, text):
    return HelpButton(text, parent)


class BundledFormsPanel(QGroupBox):
    """Save release workbooks locally, even without an internet connection."""
    def __init__(self, parent=None):
        super().__init__(tr("Ready-to-print forms"), parent)
        layout = QVBoxLayout(self)
        note = QLabel(tr("Original Excel and PDF forms from the TableScan GitHub release. Included with the app and available offline."))
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.forms = QComboBox()
        self.forms.setAccessibleName(tr("Ready-to-print form"))
        self.forms.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.forms.setMinimumContentsLength(12)
        self.forms.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for form in bundled_forms():
            self.forms.addItem(form.name, form.key)
        layout.addWidget(self.forms)
        self.save_excel = QPushButton(tr("Save Excel…"))
        self.save_pdf = QPushButton(tr("Save PDF…"))
        for button, extension, help_text in (
            (self.save_excel, "xlsx", tr("Save the original editable Excel workbook, with its print layout and formulas. This does not change your saved templates.")),
            (self.save_pdf, "pdf", tr("Save the original PDF form, ready to print without opening Excel.")),
        ):
            button.setToolTip(help_text)
            button.setProperty('hasActionHelp', True)
            button.clicked.connect(lambda _checked=False, ext=extension: self._save(ext))
            button.setEnabled(self.forms.count() > 0)
            row.addWidget(button)
            row.addWidget(_help(self, help_text))
        row.addStretch()
        layout.addLayout(row)

    def _save(self, extension):
        key = self.forms.currentData()
        file_filter = tr("Книга Excel (*.xlsx)") if extension == "xlsx" else tr("PDF (*.pdf)")
        target, _ = QFileDialog.getSaveFileName(self, tr("Save printable form"),
            str(Path.home() / f"TableScan-{key}.{extension}"), file_filter)
        if not target:
            return
        if not target.lower().endswith(f".{extension}"):
            target += f".{extension}"
        try:
            save_bundled_form(key, target, extension)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr("Could not save form"), str(exc))
            return
        notify(self, tr("Saved to {path}", path=target))


class PrintableTemplateDialog(QDialog):
    def __init__(self, template: TableTemplate, parent=None):
        super().__init__(parent)
        self.template = deepcopy(template)
        self.saved_path = None
        self.setWindowTitle(tr("Export printable Excel"))
        self.resize(520, 320)
        layout = QVBoxLayout(self)
        title = QLabel(template.name)
        title.setWordWrap(True)
        layout.addWidget(title)
        note = QLabel(tr("Creates an editable blank form with your grid, fixed text and fields. Recognition rules stay in TableScan. Review Print Preview in Excel before printing."))
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.paper = QComboBox()
        self.paper.addItems(["A4", "Letter"])
        self.orientation = QComboBox()
        self.orientation.addItem(tr("Automatic"), "auto")
        self.orientation.addItem(tr("Portrait"), "portrait")
        self.orientation.addItem(tr("Landscape"), "landscape")
        self.pagination = QComboBox()
        self.pagination.addItem(tr("Fit entire form on one page"), True)
        self.pagination.addItem(tr("One page wide; allow multiple pages"), False)
        form.addRow(tr("Paper size"), self.paper)
        form.addRow(tr("Orientation"), self.orientation)
        form.addRow(tr("Print scaling"), self.pagination)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.cancel_button = QPushButton(tr("Отмена"))
        self.cancel_button.setToolTip(tr("Close without exporting or changing the template."))
        self.cancel_button.setProperty('hasActionHelp', True)
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        actions.addWidget(_help(self, tr("Close without exporting or changing the template.")))
        actions.addStretch()
        self.save_button = QPushButton(tr("Save Excel…"))
        self.save_button.setProperty("primary", True)
        self.save_button.setProperty('hasActionHelp', True)
        help_text = tr("Choose where to save the printable workbook. Fixed values are kept; measurement cells remain blank for handwriting or editing in Excel.")
        self.save_button.setToolTip(help_text)
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        actions.addWidget(_help(self, help_text))
        layout.addLayout(actions)

    def _save(self):
        safe_name = "".join(character if character not in '/\\:*?"<>|' else "_" for character in self.template.name)
        target, _ = QFileDialog.getSaveFileName(self, tr("Export printable Excel"),
            str(Path.home() / f"{safe_name or 'TableScan'}-print.xlsx"), tr("Книга Excel (*.xlsx)"))
        if not target:
            return
        if not target.lower().endswith(".xlsx"):
            target += ".xlsx"
        try:
            self.saved_path = export_template_excel(self.template, target, PrintOptions(
                paper=self.paper.currentText(), orientation=self.orientation.currentData(),
                single_page=self.pagination.currentData()))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr("Could not save form"), str(exc))
            return
        self.accept()
        notify(self.parentWidget() or self, tr("Saved to {path}", path=str(self.saved_path)))
