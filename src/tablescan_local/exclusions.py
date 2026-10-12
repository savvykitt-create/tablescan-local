"""Reversible preparation exclusions, shared by document and template editors."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from .domain import CellAddress, excel_column_name
from .i18n import tr
from .localized_widgets import QWidget, QLabel, QPushButton, QListWidget, QListWidgetItem


class ExclusionsPanel(QWidget):
    changed = Signal(object)

    def __init__(self, template, canvas):
        super().__init__()
        self.template = template
        self.canvas = canvas
        self._history = []
        self._position = -1
        layout = QVBoxLayout(self)
        note = QLabel(tr('Select a column letter, a row number, or drag across cells. Ctrl/Cmd adds to the selection; Shift extends it. Excluded cells stay blank in exports and are skipped by all recognition modes.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.selection = QLabel()
        self.selection.setWordWrap(True)
        layout.addWidget(self.selection)
        actions = QHBoxLayout()
        self.exclude = QPushButton(tr('Exclude selection'))
        self.exclude.setProperty('primary', True)
        self.exclude.clicked.connect(lambda: self.apply(True))
        self.restore = QPushButton(tr('Restore selection'))
        self.restore.clicked.connect(lambda: self.apply(False))
        actions.addWidget(self.exclude); actions.addWidget(self.restore)
        layout.addLayout(actions)
        selection = QHBoxLayout()
        all_cells = QPushButton(tr('Select entire table'))
        all_cells.clicked.connect(self.select_all)
        clear = QPushButton(tr('Clear selection'))
        clear.clicked.connect(lambda: self.canvas.select_cells(set()))
        selection.addWidget(all_cells); selection.addWidget(clear)
        layout.addLayout(selection)
        history = QHBoxLayout()
        self.undo = QPushButton(tr('Undo exclusion change'))
        self.undo.clicked.connect(lambda: self.move_history(-1))
        self.redo = QPushButton(tr('Redo exclusion change'))
        self.redo.clicked.connect(lambda: self.move_history(1))
        history.addWidget(self.undo); history.addWidget(self.redo)
        layout.addLayout(history)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.items = QListWidget()
        self.items.setAccessibleName(tr('Excluded areas'))
        self.items.currentItemChanged.connect(self.select_item)
        layout.addWidget(self.items, 1)
        self.reset = QPushButton(tr('Clear all exclusions'))
        self.reset.clicked.connect(self.clear_all)
        layout.addWidget(self.reset)
        canvas.cellSelectionChanged.connect(self.selection_changed)
        self.refresh()

    def _state(self):
        template = self.template()
        if template is None:
            return None
        return (tuple(template.excluded_rows), tuple(template.excluded_columns),
                tuple((c.row, c.column) for c in template.excluded_cells),
                tuple(rule.role for rule in template.column_rules))

    def refresh(self, *, reset_history=False):
        state = self._state()
        # A different document/grid/column rule must never be undone by this panel.
        if reset_history or self._position < 0 or self._history[self._position] != state:
            self._history = [state]; self._position = 0
        self.items.blockSignals(True)
        self.items.clear()
        template = self.template()
        count = 0
        if template:
            count = sum(0 <= r < template.rows and 0 <= c < template.columns for r, c in template.exclusion_mask())
            entries = [(tr('Row {number}', number=r+1), ('row', r)) for r in template.excluded_rows]
            columns = sorted(set(template.excluded_columns) | {
                c for c, rule in enumerate(template.column_rules) if rule.role == 'ignored'})
            entries += [(tr('Column {name}', name=excel_column_name(c)), ('column', c)) for c in columns]
            entries += [(f'{excel_column_name(c.column)}{c.row+1}', ('cell', c.row, c.column)) for c in template.excluded_cells]
            for label, address in entries[:200]:
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, address)
                self.items.addItem(item)
            if len(entries) > 200:
                self.items.addItem(QListWidgetItem(tr('More exclusions are visible on the page.')))
        self.items.blockSignals(False)
        self.summary.setText(tr('Excluded cells: {count}. Their positions are preserved; no values are shifted.', count=count))
        self.undo.setEnabled(self._position > 0)
        self.redo.setEnabled(self._position < len(self._history)-1)
        self.reset.setEnabled(bool(template and (count or template.excluded_rows or template.excluded_columns or template.excluded_cells)))
        self.selection_changed(self.canvas.selected_cells)

    def selection_changed(self, cells):
        self.selection.setText(tr('Selected cells: {count}', count=len(cells)) if cells else tr('No cells selected'))
        self.exclude.setEnabled(bool(cells and self.template()))
        self.restore.setEnabled(bool(cells and self.template()))

    def select_all(self):
        template = self.template()
        if template:
            self.canvas.select_cells({(r, c) for r in range(template.rows) for c in range(template.columns)})

    def select_item(self, item, previous):
        address = item.data(Qt.ItemDataRole.UserRole) if item else None
        template = self.template()
        if address and template:
            kind, *indices = address
            if kind == 'row':
                cells = {(indices[0], c) for c in range(template.columns)}
            elif kind == 'column':
                cells = {(r, indices[0]) for r in range(template.rows)}
            else:
                cells = {tuple(indices)}
            self.canvas.select_cells(cells)

    def apply(self, excluded):
        if self.template() and self.canvas.selected_cells:
            self.refresh()
            self.template().set_cell_exclusions(set(self.canvas.selected_cells), excluded)
            self._record_change()

    def clear_all(self):
        if self.template():
            self.refresh()
            self.template().clear_exclusions()
            self._record_change()

    def _record_change(self):
        state = self._state()
        if self._history[self._position] != state:
            self._history = self._history[:self._position+1] + [state]
            self._position += 1
        self._changed()

    def move_history(self, delta):
        self.refresh()
        position = self._position + delta
        if self.template() and 0 <= position < len(self._history):
            rows, columns, cells, roles = self._history[position]
            template = self.template()
            template.excluded_rows = list(rows)
            template.excluded_columns = list(columns)
            template.excluded_cells = [CellAddress(*cell) for cell in cells]
            for rule, role in zip(template.column_rules, roles):
                rule.role = role
            self._position = position
            self._changed()

    def _changed(self):
        self.refresh()
        self.canvas.redraw()
        self.changed.emit(self.template())
