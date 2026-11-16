"""Skip controls distinguish selection from skipping and reset unsafe history."""
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QToolTip

from tablescan_local.button_help import button_with_help
from tablescan_local.domain import CellAddress, NormalizedRect, TableTemplate
from tablescan_local.exclusions import ExclusionsPanel
from tablescan_local.localized_widgets import QPushButton


class Canvas(QObject):
    cellSelectionChanged = Signal(object)

    def __init__(self):
        super().__init__()
        self.selected_cells = set()

    def select_cells(self, cells):
        self.selected_cells = cells
        self.cellSelectionChanged.emit(cells)

    def redraw(self):
        pass


def make_panel(qtbot):
    template = TableTemplate('test', 'Test', NormalizedRect(0, 0, 1, 1),
                             [0, .25, .5, .75, 1], [0, .25, .5, .75, 1])
    template.ensure_column_rules()
    canvas = Canvas()
    panel = ExclusionsPanel(lambda: template, canvas)
    qtbot.addWidget(panel)
    return template, canvas, panel


def test_skip_include_enable_only_changes_and_deselect_is_safe(qtbot):
    template, canvas, panel = make_panel(qtbot)
    assert not panel.exclude.isEnabled() and not panel.restore.isEnabled()
    canvas.select_cells({(1, 2)})
    assert panel.exclude.isEnabled() and not panel.restore.isEnabled()
    panel.exclude.click()
    assert template.is_excluded(1, 2)
    assert not panel.exclude.isEnabled() and panel.restore.isEnabled()
    panel.clear_selection.click()
    assert template.is_excluded(1, 2) and not canvas.selected_cells
    canvas.select_cells({(1, 2), (2, 2)})
    assert panel.exclude.isEnabled() and panel.restore.isEnabled()
    panel.restore.click()
    assert not template.exclusion_mask()


def test_history_resets_after_grid_changes(qtbot):
    template, canvas, panel = make_panel(qtbot)
    canvas.select_cells({(1, 2)})
    panel.exclude.click()
    assert panel.undo.isEnabled()
    template.resize_grid(3, 4)
    panel.refresh()
    assert not panel.undo.isEnabled()
    panel.move_history(-1)
    assert template.is_excluded(1, 2)


def test_identical_new_document_does_not_inherit_undo(qtbot):
    template, canvas, panel = make_panel(qtbot)
    canvas.select_cells({(1, 2)})
    panel.exclude.click()
    replacement = TableTemplate.from_dict(template.to_dict())
    panel.template = lambda: replacement
    panel.refresh()
    assert not panel.undo.isEnabled()


def test_include_listed_area_removes_obsolete_grid_entry(qtbot):
    template, canvas, panel = make_panel(qtbot)
    template.excluded_cells = [CellAddress(3, 2), CellAddress(1, 1)]
    template.resize_grid(3, 4)
    panel.refresh()
    panel.items.setCurrentRow(0)
    panel.include_area.click()
    assert template.excluded_cells == [CellAddress(1, 1)]
    template.validate_value_rules()
    panel.undo.click()
    assert CellAddress(3, 2) in template.excluded_cells


def test_include_listed_column_restores_overlap_but_preserves_other_cells(qtbot):
    template, canvas, panel = make_panel(qtbot)
    template.set_cell_exclusions({(1, c) for c in range(4)} | {(r, 2) for r in range(4)}, True)
    panel.refresh()
    panel.items.setCurrentRow(1)
    panel.include_area.click()
    assert template.exclusion_mask() == {(1, c) for c in (0, 1, 3)}


def test_help_remains_available_for_disabled_action(qtbot, monkeypatch):
    button = QPushButton('Example')
    button.setEnabled(False)
    wrapped = button_with_help(button, 'Why this action is unavailable.')
    qtbot.addWidget(wrapped)
    calls = []
    monkeypatch.setattr(QToolTip, 'showText', lambda *args: calls.append(args))
    wrapped.help_button.click()
    assert wrapped.help_button.isEnabled()
    assert calls[0][1] == wrapped.help_button.toolTip() == 'Why this action is unavailable.'
    assert button.toolTip() == ''
    assert button.accessibleDescription() == 'Why this action is unavailable.'


def test_hidden_action_does_not_leave_orphan_help(qtbot):
    button = QPushButton('Example')
    wrapped = button_with_help(button, 'Action help.')
    qtbot.addWidget(wrapped)
    wrapped.show()
    assert button.property('hasActionHelp') and wrapped.help_button.isVisible()
    button.hide()
    assert not wrapped.isVisible()
    button.show()
    assert wrapped.isVisible() and wrapped.help_button.isVisible()
    wrapped.hide()
    wrapped.show()
    assert button.isVisible() and wrapped.help_button.isVisible()
