import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QDialog, QMessageBox

from tablescan_local.constraints import ValueConstraints
from tablescan_local.domain import CellResult, CellRuleRegion, JobResult, NormalizedRect, PageResult, TableTemplate
from tablescan_local.rule_editor import ValueRuleDialog
from tablescan_local.ui import TablePage, ReviewPage, create_application
import tablescan_local.ui as ui_module


def template():
    t = TableTemplate("t", "t", NormalizedRect(.1, .1, .8, .8), [.1, .3, .5, .7, .9], [.1, .3, .5, .7, .9])
    t.ensure_column_rules()
    return t


def test_preset_preview_comma_input_and_cancel_are_nonmutating(qtbot):
    source = ValueConstraints("numeric", maximum=200)
    dialog = ValueRuleDialog(source)
    qtbot.addWidget(dialog)
    dialog.preset.setCurrentIndex(1)
    assert dialog.read_rule().decimal_places == 1
    assert dialog.read_rule().maximum == 100
    assert "34.4" in dialog.preview.text()
    dialog.maximum.setText("99,5")
    assert dialog.read_rule().maximum == 99.5
    dialog.reject()
    assert source.maximum == 200 and source.decimal_places is None


def test_selection_snaps_inside_cells_and_ignores_shared_end_boundary(qtbot, monkeypatch):
    page = TablePage(); qtbot.addWidget(page)
    page.set_document(np.full((1000, 1000, 3), 255, np.uint8), template())
    selected = []
    monkeypatch.setattr(page, "_edit_rule", lambda region: selected.append(region))
    page._region_created("cell_rule", QRectF(320, 320, 380, 380))
    region = selected[0]
    assert (region.row_start, region.row_end, region.column_start, region.column_end) == (1, 2, 1, 2)
    page.tabs.setCurrentIndex(3)
    assert page.canvas.show_rules and not page.canvas.show_grid


def test_apply_and_reload_region_rules(qtbot, monkeypatch):
    page = TablePage(); qtbot.addWidget(page)
    t = template(); page.set_document(np.full((1000, 1000, 3), 255, np.uint8), t)
    page._rule_all_data()
    page._apply_quick_rule()
    assert len(t.cell_rules) == 1
    assert t.cell_rules[0].column_start == 1
    assert t.value_constraints(0, 1)[0].decimal_places == 1
    assert t.value_constraints(0, 1)[0].require_decimal
    assert page.rule_list.count() == 1
    copy = TableTemplate.from_dict(t.to_dict())
    page.set_document(page.image, copy)
    assert page.rule_list.count() == 1


def test_excel_like_selection_and_noncontiguous_rule_application(qtbot):
    page = TablePage(); qtbot.addWidget(page)
    t = template(); page.set_document(np.full((1000, 1000, 3), 255, np.uint8), t)
    page.tabs.setCurrentIndex(3)
    page.canvas.select_cells({(0, 1), (0, 2), (2, 1), (2, 2)})
    assert page.selection_label.text() == "Cells: 4"
    page._apply_quick_rule()
    assert len(t.cell_rules) == 2
    assert {region.address() for region in t.cell_rules} == {"B1:C1", "B3:C3"}
    assert all(region.constraints.maximum == 60 for region in t.cell_rules)


def test_selection_undo_redo_and_reapplying_same_range_replaces_rule(qtbot):
    page = TablePage(); qtbot.addWidget(page)
    t = template(); page.set_document(np.full((1000, 1000, 3), 255, np.uint8), t)
    page.tabs.setCurrentIndex(3)
    page.canvas.select_cells({(0, 1)})
    page.canvas.select_cells({(0, 1), (0, 2)})
    page.canvas.undo_selection()
    assert page.canvas.selected_cells == {(0, 1)}
    page.canvas.redo_selection()
    assert page.canvas.selected_cells == {(0, 1), (0, 2)}
    page._apply_quick_rule()
    page.quick_rule_name.setText("Уточнённое правило")
    page._apply_quick_rule()
    assert len(t.cell_rules) == 1
    assert t.cell_rules[0].name == "Уточнённое правило"


def test_rule_rejects_wrong_manual_confirmation_and_allows_corrected_value(qtbot, monkeypatch):
    review = ReviewPage(); qtbot.addWidget(review)
    t = template(); t.cell_rules = [CellRuleRegion("r", "Measurements", 0, 3, 1, 3, ValueConstraints("numeric", minimum=0, maximum=100, decimal_places=1))]
    cell = CellResult(0, 1, "344", "344", .99, flags=["rule_conflict"], applied_rule="Measurements")
    result = JobResult("source.png", t, [PageResult(0, "source.png", [cell], [])])
    review.set_result([np.full((100, 100, 3), 255, np.uint8)], result)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    review.confirm_current()
    assert warnings and cell.needs_review
    review.correct_value.setText("34,4")
    review.confirm_current()
    assert cell.final_text == "34.4" and cell.status == "corrected"


def test_editing_template_invalidates_current_result_without_mutating_snapshot(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path))
    app, window = create_application(); qtbot.addWidget(window)
    original = template()
    window.result = JobResult("source", original, [])
    edited = TableTemplate.from_dict(original.to_dict())
    edited.cell_rules.append(CellRuleRegion("r", "New", 0, 3, 1, 3))
    window._template_changed(edited)
    assert window.result is None
    assert original.cell_rules == []


def test_double_start_keeps_one_live_recognition_worker(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path))
    app, window = create_application(); qtbot.addWidget(window)
    t = template()
    window.images = [np.full((100, 100, 3), 255, np.uint8)]
    window.job_id = "double-start"
    window.source_path = "source.png"
    started = threading.Event()
    release = threading.Event()

    def slow_process(*_args, **_kwargs):
        started.set()
        release.wait(3)
        return JobResult("source.png", t, [PageResult(0, "source.png", [], [])])

    monkeypatch.setattr(ui_module, "process_document", slow_process)
    monkeypatch.setattr(window, "_recognition_done", lambda _result: None)
    window.start_recognition(t)
    assert started.wait(1)
    first = window.worker
    window.start_recognition(t)
    assert window.worker is first
    assert len(window._workers) == 1
    assert not window.table_page.continue_button.isEnabled()
    with qtbot.waitSignal(first.finished, timeout=4000):
        release.set()
    assert window.worker is None
    assert window._workers == []
    assert window.table_page.continue_button.isEnabled()
