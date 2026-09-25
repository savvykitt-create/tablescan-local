"""Template values that bypass recognition while retaining table coordinates."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout

from .domain import excel_column_name
from .i18n import tr
from .localized_widgets import QWidget, QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem


class FixedCellsPanel(QWidget):
    changed = Signal(object)

    def __init__(self, template, canvas):
        super().__init__()
        self.template = template
        self.canvas = canvas
        layout = QVBoxLayout(self)
        note = QLabel(tr('Select cells on the page and enter their constant value. These cells are excluded from OCR and filled from the template. Keep animal IDs and measurements in OCR mode.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.selection = QLabel(tr('No cells selected'))
        layout.addWidget(self.selection)
        self.value = QLineEdit()
        self.value.setPlaceholderText(tr('Constant value (may be empty)'))
        layout.addWidget(self.value)
        buttons = QHBoxLayout()
        self.apply = QPushButton(tr('Set template value'))
        self.apply.clicked.connect(self.set_values)
        self.restore = QPushButton(tr('Recognize with OCR'))
        self.restore.clicked.connect(self.restore_ocr)
        buttons.addWidget(self.apply); buttons.addWidget(self.restore)
        layout.addLayout(buttons)
        self.headers = QPushButton(tr('Fill column headers'))
        self.headers.clicked.connect(self.fill_headers)
        layout.addWidget(self.headers)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.cells = QListWidget()
        self.cells.currentItemChanged.connect(self.select_item)
        layout.addWidget(self.cells, 1)
        self.remove = QPushButton(tr('Remove selected template value'))
        self.remove.clicked.connect(self.remove_item)
        layout.addWidget(self.remove)
        canvas.cellSelectionChanged.connect(self.selection_changed)
        self.selection_changed(set())

    def selection_changed(self, cells):
        self.apply.setEnabled(bool(cells)); self.restore.setEnabled(bool(cells))
        self.selection.setText(tr('Selected cells: {count}', count=len(cells)) if cells else tr('No cells selected'))
        template = self.template()
        if template and cells:
            values = {template.fixed_value(r, c) for r, c in cells}
            self.value.setText(next(iter(values)) if len(values) == 1 and None not in values else '')

    def set_values(self):
        self._apply(str(self.value.text()))

    def restore_ocr(self):
        self._apply(None)

    def _apply(self, value):
        template = self.template()
        if template is None:
            return
        for r, c in self.canvas.selected_cells:
            template.set_fixed_value(r, c, value)
        self._changed()

    def fill_headers(self):
        template = self.template()
        if template is None or not template.header_rows:
            return
        template.ensure_column_rules()
        for c, rule in enumerate(template.column_rules):
            template.set_fixed_value(0, c, rule.name)
        self._changed()

    def select_item(self, item, previous):
        if item is not None:
            self.canvas.select_cells({tuple(item.data(Qt.ItemDataRole.UserRole))})

    def remove_item(self):
        item = self.cells.currentItem()
        if item is not None and self.template():
            r, c = item.data(Qt.ItemDataRole.UserRole)
            self.template().set_fixed_value(r, c, None)
            self._changed()

    def _changed(self):
        self.refresh()
        self.canvas.redraw()
        self.changed.emit(self.template())

    def refresh(self):
        template = self.template()
        self.cells.blockSignals(True)
        self.cells.clear()
        if template:
            for cell in sorted(template.fixed_cells, key=lambda item: (item.row, item.column)):
                item = QListWidgetItem(f'{excel_column_name(cell.column)}{cell.row + 1}  =  {cell.value!r}')
                item.setData(Qt.ItemDataRole.UserRole, (cell.row, cell.column))
                self.cells.addItem(item)
            self.summary.setText(tr('Fixed cells: {count}. They remain in exports but are not analyzed.', count=len(template.fixed_cells)))
        self.headers.setEnabled(bool(template and template.header_rows))
        self.cells.blockSignals(False)
