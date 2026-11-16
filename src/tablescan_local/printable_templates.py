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
from openpyxl.comments import Comment
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.merge import MergedCellRange
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QSizePolicy, QVBoxLayout

from .domain import TableTemplate
from .button_help import HelpButton
from .exporter import _save_workbook
from .i18n import tr
from .localized_widgets import (QComboBox, QDialog, QFileDialog,
                                QGroupBox, QLabel, QPushButton)
from .ui_common import notify


@dataclass(frozen=True)
class PrintOptions:
    # Page geometry belongs to the recognition template. Reflow and pagination
    # would create a different form, so those options are deliberately refused.
    paper: str = "Template"
    orientation: str = "auto"
    single_page: bool = True


@dataclass(frozen=True)
class TemplatePage:
    width_mm: float
    height_mm: float
    paper: str

    @property
    def orientation(self):
        return "landscape" if self.width_mm > self.height_mm else "portrait"


def template_page(template: TableTemplate, options: PrintOptions = PrintOptions()) -> TemplatePage:
    if options.paper not in {"Template", "A4", "Letter"} or options.orientation not in {"auto", "portrait", "landscape"}:
        raise ValueError(tr("Choose a page layout that matches the template."))
    if not options.single_page:
        raise ValueError(tr("A reusable template must print on one page. Multiple-page scaling changes its geometry."))
    aspect = template.reference_page_aspect
    if aspect is None and template.reference_source_path:
        source = Path(template.reference_source_path)
        if source.is_file():
            from .imaging import open_document
            image = open_document(source, rotation=template.rotation_degrees, transforms=template.page_transforms)[0]
            aspect = image.shape[1] / image.shape[0]
    if aspect is None:
        # Old templates without a reference page used normalized A4 coordinates.
        aspect = 210 / 297
    if not math.isfinite(aspect) or not .1 <= aspect <= 10:
        raise ValueError(tr("The template page proportions are invalid."))
    orientation = "landscape" if aspect > 1 else "portrait"
    if options.orientation not in {"auto", orientation}:
        raise ValueError(tr("Changing the page orientation would make this form incompatible with its template."))
    candidates = [("A4", 210., 297.), ("Letter", 215.9, 279.4)]
    for name, width, height in candidates:
        if orientation == "landscape":
            width, height = height, width
        if abs(width / height / aspect - 1) <= .002 and options.paper in {"Template", name}:
            return TemplatePage(width, height, name)
    if options.paper != "Template":
        raise ValueError(tr("The selected paper proportions differ from the template. Use the template page layout."))
    # Preserve nonstandard source pages instead of squeezing them onto A4.
    if aspect > 1:
        height = min(210., 297. / aspect)
        return TemplatePage(height * aspect, height, "Custom")
    width = min(210., 297. * aspect)
    return TemplatePage(width, width / aspect, "Custom")


def _orientation(template: TableTemplate, options: PrintOptions) -> str:
    return template_page(template, options).orientation


def _overlaps(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def export_template_excel(template: TableTemplate, target: str | Path,
                          options: PrintOptions = PrintOptions()) -> Path:
    """Print the same page-coordinate grid and field regions used for OCR.

    Worksheet rows/columns form a page lattice, not a reordered data table.
    Native cells are merged at original cell/field boundaries. No title rows,
    moved metadata, repeated headers, extra margins, or print scaling are added.
    Fixed-source metadata inside the table is semantic metadata, not another
    physical box: the grid's printed fixed cells retain precedence there.
    """
    template = deepcopy(template)
    page = template_page(template, options)
    if not 1 <= template.rows <= 10000 or not 1 <= template.columns <= 256:
        raise ValueError(tr("The printable grid must contain 1–10000 rows and 1–256 columns."))
    for guides in (template.row_guides, template.column_guides):
        if any(not math.isfinite(value) for value in guides) or any(b <= a for a, b in zip(guides, guides[1:])):
            raise ValueError(tr("Grid lines must be in increasing order before exporting."))
        if guides[0] < 0 or guides[-1] > 1:
            raise ValueError(tr("The grid and fields must fit inside the template page before exporting."))
    template.validate_value_rules()
    # Excel stores column widths in maximum-digit units. Courier New 10 has an
    # eight-pixel digit at 96 dpi, i.e. six points per stored width unit. Content
    # cells override this metric-only font. Quantizing cumulative boundaries
    # avoids accumulating each individual column's pixel-rounding error.
    # Stored width already includes padding (ECMA-376 spreadsheet col width):
    # https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.column
    width_px = math.floor(page.width_mm / 25.4 * 96)
    height_px = math.floor(page.height_mm / 25.4 * 96)
    def x(value): return round(value * width_px)
    def y(value): return round(value * height_px)
    grid_x = [x(v) for v in template.column_guides]
    grid_y = [y(v) for v in template.row_guides]
    if len(set(grid_x)) != len(grid_x) or len(set(grid_y)) != len(grid_y):
        raise ValueError(tr("Some grid cells are too small to print without changing the template geometry."))
    table_box = (grid_x[0], grid_y[0], grid_x[-1], grid_y[-1])
    fields = []
    for index, field in sorted(enumerate(template.fields), key=lambda item: item[1].source == "fixed"):
        r = field.rect
        if (not all(math.isfinite(v) for v in (r.x, r.y, r.width, r.height)) or
                r.x < 0 or r.y < 0 or r.width <= 0 or r.height <= 0 or
                r.x + r.width > 1.000001 or r.y + r.height > 1.000001):
            raise ValueError(tr("The grid and fields must fit inside the template page before exporting."))
        box = (x(r.x), y(r.y), x(min(1., r.x + r.width)), y(min(1., r.y + r.height)))
        if box[0] == box[2] or box[1] == box[3]:
            raise ValueError(tr("Some grid cells are too small to print without changing the template geometry."))
        # A field crop within the grid already reads from the printed table.
        # Printing another rectangle/value there would overwrite real cells.
        if _overlaps(box, table_box):
            if field.source == "fixed" or (box[0] >= table_box[0] and box[1] >= table_box[1]
                    and box[2] <= table_box[2] and box[3] <= table_box[3]):
                continue
            raise ValueError(tr("A field crosses the table boundary. Move it fully inside or outside the table before exporting."))
        if any(_overlaps(box, other[2]) for other in fields):
            if field.source == "fixed":
                continue
            raise ValueError(tr("Overlapping fields cannot be printed as separate editable boxes. Adjust their boundaries before exporting."))
        fields.append((index, field, box))
    x_edges = sorted({0, width_px, *grid_x, *(v for _, _, box in fields for v in (box[0], box[2]))})
    y_edges = sorted({0, height_px, *grid_y, *(v for _, _, box in fields for v in (box[1], box[3]))})
    # Excel caps a single row at 409.5 points. Split only blank lattice spans;
    # merged logical cells remain single editable cells across these splits.
    extra = []
    for start, end in zip(y_edges, y_edges[1:]):
        extra.extend(range(start + 500, end, 500))
    y_edges = sorted({*y_edges, *extra})
    x_indices = {value: index + 1 for index, value in enumerate(x_edges)}
    y_indices = {value: index + 1 for index, value in enumerate(y_edges)}
    workbook = Workbook()
    workbook._fonts[0] = Font(name="Courier New", size=10)
    workbook._named_styles["Normal"].font = Font(name="Courier New", size=10)
    sheet = workbook.active
    sheet.title = "Printable form"
    sheet.sheet_view.showGridLines = False
    for col, (a, b) in enumerate(zip(x_edges, x_edges[1:]), 1):
        sheet.column_dimensions[get_column_letter(col)].width = (b - a) / 8
    for row, (a, b) in enumerate(zip(y_edges, y_edges[1:]), 1):
        sheet.row_dimensions[row].height = (b - a) * .75
    thin = Side(style="thin", color="222222")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def write_box(box, value=None, *, heading=False, comment=None, top=False, defined_name=None):
        c1, r1 = x_indices[box[0]], y_indices[box[1]]
        c2, r2 = x_indices[box[2]] - 1, y_indices[box[3]] - 1
        if c1 != c2 or r1 != r2:
            sheet.merge_cells(start_row=r1, start_column=c1, end_row=r2, end_column=c2)
        cell = sheet.cell(r1, c1, value)
        cell.border = border
        cell.font = Font(name="Arial", size=10, bold=heading)
        cell.alignment = Alignment(horizontal="left" if top else "center",
                                   vertical="top" if top else "center", wrap_text=True)
        if heading:
            cell.fill = PatternFill("solid", fgColor="E9EDF2")
        if comment:
            cell.comment = Comment(comment, "TableScan")
        # Propagate borders to merged-cell edges after assigning the anchor.
        if c1 != c2 or r1 != r2:
            MergedCellRange(sheet, f"{get_column_letter(c1)}{r1}:{get_column_letter(c2)}{r2}").format()
        if defined_name:
            ref = f"'Printable form'!${get_column_letter(c1)}${r1}:${get_column_letter(c2)}${r2}"
            workbook.defined_names.add(DefinedName(defined_name, attr_text=ref))

    fixed = {(cell.row, cell.column): cell.value for cell in template.fixed_cells}
    for r in range(template.rows):
        for c in range(template.columns):
            value = fixed.get((r, c))
            if value is None and template.header_rows and r == template.header_rows - 1:
                value = template.column_rules[c].name
            write_box((grid_x[c], grid_y[r], grid_x[c+1], grid_y[r+1]), value,
                      heading=r < template.header_rows,
                      defined_name=f"TableScan_Cell_{r+1}_{c+1}")
    for index, field, box in fields:
        # Only labels explicitly configured for OCR label removal may be put
        # inside an OCR crop. User-facing field names remain Excel comments.
        label = field.printed_label.strip()
        value = field.fixed_value if field.source == "fixed" else ""
        text = "\n".join(part for part in (label, value) if part) or None
        write_box(box, text, comment=field.name, top=True,
                  defined_name=f"TableScan_Field_{index+1}")
    last_column = get_column_letter(len(x_edges) - 1)
    last_row = len(y_edges) - 1
    # Make the entire original page, including blank margins, an explicit
    # print area. Styling the final cell prevents applications trimming it.
    sheet.cell(last_row, len(x_edges) - 1).fill = PatternFill("solid", fgColor="FFFFFF")
    sheet.print_area = f"A1:{last_column}{last_row}"
    sheet.page_setup.orientation = page.orientation
    if page.paper == "A4":
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    elif page.paper == "Letter":
        sheet.page_setup.paperSize = sheet.PAPERSIZE_LETTER
    else:
        sheet.page_setup.paperWidth = f"{min(page.width_mm, page.height_mm):.6f}mm"
        sheet.page_setup.paperHeight = f"{max(page.width_mm, page.height_mm):.6f}mm"
    sheet.page_setup.scale = 100
    sheet.sheet_properties.pageSetUpPr.fitToPage = False
    sheet.print_options.horizontalCentered = False
    sheet.print_options.verticalCentered = False
    sheet.page_margins = PageMargins(left=0, right=0, top=0, bottom=0, header=0, footer=0)
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
            button.setAccessibleDescription(help_text)
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
        note = QLabel(tr("Keeps the grid, cell sizes and field positions on the original page so this template can recognize the printed form. Print at 100% without resizing rows or columns."))
        note.setWordWrap(True)
        layout.addWidget(note)
        page = template_page(template)
        dimensions = QLabel(tr("Template page: {width} × {height} mm. One page, original scale.",
                              width=f"{page.width_mm:.1f}", height=f"{page.height_mm:.1f}"))
        dimensions.setWordWrap(True)
        layout.addWidget(dimensions)
        actions = QHBoxLayout()
        self.cancel_button = QPushButton(tr("Отмена"))
        self.cancel_button.setProperty('hasActionHelp', True)
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        self.save_button = QPushButton(tr("Save Excel…"))
        self.save_button.setProperty("primary", True)
        self.save_button.setProperty('hasActionHelp', True)
        help_text = tr("Choose where to save the printable workbook. Fixed values are kept; measurement cells remain blank for handwriting or editing in Excel.")
        self.save_button.setAccessibleDescription(help_text)
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
            self.saved_path = export_template_excel(self.template, target)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr("Could not save form"), str(exc))
            return
        self.accept()
        notify(self.parentWidget() or self, tr("Saved to {path}", path=str(self.saved_path)))
