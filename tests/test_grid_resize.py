import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest
from tablescan_local.domain import TableTemplate, NormalizedRect, CellRuleRegion
from tablescan_local.constraints import ValueConstraints
from tablescan_local.ui import TablePage

def template():
    t = TableTemplate('test', 'Test', NormalizedRect(0, 0, 1, 1),
                      [i / 22 for i in range(23)], [i / 13 for i in range(14)])
    t.cell_rules = [CellRuleRegion('measure', 'Measurements', 0, 21, 1, 12,
                    ValueConstraints('numeric', minimum=0, maximum=80, decimal_places=1))]
    return t

def test_outer_measurement_rules_follow_grid_and_survive_roundtrip():
    t = template()
    t.column_guides = [(i / 13) ** 1.1 for i in range(14)]
    columns = list(t.column_guides)
    t.resize_grid(20, 13)
    assert t.cell_rules[0].address() == 'B1:M20'
    assert t.column_guides == columns
    t.validate_value_rules()
    restored = TableTemplate.from_dict(t.to_dict())
    restored.resize_grid(22, 15)
    assert restored.cell_rules[0].address() == 'B1:O22'
    assert restored.value_constraints(21, 14)[0].maximum == 80
    restored.validate_value_rules()

def test_local_rule_does_not_move_and_fully_removed_rule_still_warns():
    t = template()
    t.cell_rules += [CellRuleRegion('local', 'Local', 4, 5, 3, 4),
                     CellRuleRegion('removed', 'Removed', 21, 21, 1, 1)]
    t.resize_grid(20, 6)
    assert t.cell_rules[1].address() == 'D5:E6'
    assert t.cell_rules[2].address() == 'B22'
    with pytest.raises(ValueError): t.validate_value_rules()

def test_editor_resizes_rules_and_repairs_old_partial_overflow(qtbot):
    t = template(); page = TablePage(); qtbot.addWidget(page)
    page.set_document(np.full((200, 200, 3), 255, np.uint8), t)
    page.rows_spin.setValue(20); page.columns_spin.setValue(6)
    assert t.cell_rules[0].address() == 'B1:F20'
    page.quick_maximum.setText('90')
    assert t.cell_rules[0].constraints.maximum == 90
    t.validate_value_rules()
    t.cell_rules[0].row_end = 21
    page.set_document(page.image, t)
    assert t.cell_rules[0].row_end == 19
    assert page.grid_warning.text()

def test_typing_grid_size_does_not_apply_intermediate_digits(qtbot):
    t = template(); page = TablePage(); qtbot.addWidget(page)
    page.set_document(np.full((200, 200, 3), 255, np.uint8), t)
    editor = page.rows_spin.lineEdit()
    editor.selectAll()
    qtbot.keyClicks(editor, '2')
    assert t.rows == 22
    qtbot.keyClicks(editor, '0')
    page.rows_spin.interpretText()
    assert t.rows == 20
    assert t.cell_rules[0].address() == 'B1:M20'
