"""Preparation exclusions must survive editing and never reach OCR or export."""
from unittest.mock import Mock

import numpy as np
import pytest
from openpyxl import load_workbook
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from tablescan_local.domain import CellAddress, JobResult, NormalizedRect, TableTemplate
from tablescan_local.ocr import OcrValue


def template():
    t = TableTemplate('exclude', 'Exclusions', NormalizedRect(.1, .1, .8, .8),
                      [.1, .3, .5, .7, .9], [.1, .3, .5, .7, .9],
                      header_rows=1, row_label_columns=1, detect_crossed_rows=False)
    t.ensure_column_rules()
    for rule in t.column_rules:
        rule.value_format = 'numeric'
    return t


def test_overlapping_axes_restore_one_cell_without_restoring_neighbors():
    t = template()
    t.set_cell_exclusions({(1, c) for c in range(4)}, True)
    t.set_cell_exclusions({(r, 2) for r in range(4)}, True)
    t.set_cell_exclusions({(3, 3)}, True)
    original = {(r, c) for r in range(4) for c in range(4) if t.is_excluded(r, c)}
    t.set_cell_exclusions({(1, 2)}, False)
    assert {(r, c) for r in range(4) for c in range(4) if t.is_excluded(r, c)} == original - {(1, 2)}
    t.validate_value_rules()
    restored = TableTemplate.from_dict(t.to_dict())
    assert restored.to_dict() == t.to_dict()
    restored.clear_exclusions()
    assert not any(restored.is_excluded(r, c) for r in range(4) for c in range(4))


def test_exclusion_precedence_and_legacy_ignored_column():
    t = template(); t.set_fixed_value(0, 2, 'Heading')
    t.column_rules[2].role = 'ignored'
    assert not t.is_excluded(0, 2)  # Existing templates retain fixed headings.
    t.set_cell_exclusions({(r, 2) for r in range(4)}, True)
    assert t.is_excluded(0, 2) and t.fixed_value(0, 2) == 'Heading'
    t.set_cell_exclusions({(2, 2)}, False)
    assert not t.is_excluded(2, 2) and t.is_excluded(0, 2) and t.is_excluded(3, 2)
    t.clear_exclusions()
    assert t.fixed_value(0, 2) == 'Heading'


def test_grid_resize_does_not_silently_move_exclusions():
    t = template(); t.set_cell_exclusions({(3, 2)}, True)
    t.resize_grid(3, 4)
    with pytest.raises(ValueError): t.validate_value_rules()
    assert t.excluded_cells == [CellAddress(3, 2)]
    t.clear_exclusions(); t.validate_value_rules()
    t.set_cell_exclusions({(1, c) for c in range(4)}, True)
    t.resize_grid(4, 5)
    assert t.is_excluded(1, 4)
    t.excluded_rows.append(True)
    with pytest.raises(ValueError): t.validate_value_rules()


def test_legacy_template_without_preparation_exclusions():
    data = template().to_dict()
    for key in ('excluded_rows', 'excluded_columns', 'excluded_cells'): data.pop(key)
    t = TableTemplate.from_dict(data)
    assert not any(t.is_excluded(r, c) for r in range(4) for c in range(4))


def test_excluded_cells_never_get_cropped_recognized_recovered_or_exported(tmp_path, monkeypatch):
    from tablescan_local import pipeline, slow_mode
    from tablescan_local.exporter import export_job
    t = template(); t.set_fixed_value(0, 2, 'unused')
    t.set_cell_exclusions({(1, c) for c in range(4)} | {(r, 2) for r in range(4)} | {(3, 3)}, True)
    engine = Mock(); engine.recognize_cell.return_value = OcrValue('7.5', .7, flags=['low_confidence'])
    original = pipeline.cell_crop_bundle; cropped = []
    def crop(image, template, r, c):
        cropped.append((r, c)); return original(image, template, r, c)
    monkeypatch.setattr(pipeline, 'cell_crop_bundle', crop)
    image = np.full((400, 400, 3), 100, np.uint8); progress = []
    page = pipeline.process_page(image, 'test.png', 0, t, engine, lambda *args: progress.append(args), tmp_path/'crops')
    active = {(r, c) for r in range(4) for c in range(4) if not t.is_excluded(r, c)}
    assert set(cropped) == active
    assert engine.recognize_cell.call_count == len(active)
    assert progress[-1][:2] == (len(active), len(active))
    assert all(cell.status == 'excluded' and not cell.crop_path and not cell.final_text
               for cell in page.cells if (cell.row, cell.column) not in active)
    assert {(cell.row, cell.column) for cell in slow_mode.eligible_cells(page, t)} <= active
    masked = slow_mode.mask_fixed_cells(image, t, page)
    assert (masked[120:160, 60:100] == 255).all()
    assert (masked[220:260, 220:260] == 255).all()
    assert (masked[220:260, 140:180] == 100).all()
    assert (image == 100).all()
    result = JobResult.from_dict(JobResult('test.png', t, [page]).to_dict())
    for cell in result.pages[0].cells:
        if cell.status != 'excluded':
            cell.status = 'confirmed'
    path = export_job(result, tmp_path/'export.xlsx', mode='compact')
    book = load_workbook(path); sheet = book.active
    assert sheet['C1'].value is None and sheet['B2'].value is None and sheet['D4'].value is None
    assert sheet['B3'].value == 7.5
    book.close()


def test_all_excluded_document_never_loads_ocr_or_slow(monkeypatch):
    from tablescan_local import pipeline, slow_mode, slow_setup
    # Qt can deliver unrelated settings refreshes during test teardown. Their
    # status probe must not call the analysis-only runtime_config sentinel.
    monkeypatch.setattr(slow_setup, 'setup_status', lambda: 'missing')
    t = template(); t.set_cell_exclusions({(r, c) for r in range(4) for c in range(4)}, True)
    engine = Mock(side_effect=AssertionError('No OCR required'))
    config = Mock(side_effect=AssertionError('No Slow required'))
    monkeypatch.setattr(pipeline, 'LocalOcrEngine', engine)
    monkeypatch.setattr(slow_mode, 'runtime_config', config)
    result = pipeline.process_document([np.zeros((400, 400, 3), np.uint8)], 'excluded', t, slow_mode=True)
    engine.assert_not_called(); config.assert_not_called()
    assert len(result.pages[0].cells) == 16 and result.unresolved_count == 0


def test_editor_header_selection_exclusion_restore_undo_and_new_document(qtbot):
    from tablescan_local.ui import TablePage
    page = TablePage(); qtbot.addWidget(page); page.resize(1200, 800); page.show()
    t = template(); page.set_document(np.full((500, 500, 3), 255, np.uint8), t)
    page.exclusions_button.click(); qtbot.wait(20)
    assert page.canvas.selection_enabled and page.tabs.currentWidget() is page.exclusions_panel
    point = page.canvas.mapFromScene(QPointF(300, 50-page.canvas._header_band()/2))
    QTest.mouseClick(page.canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert page.canvas.selected_cells == {(r, 2) for r in range(4)}
    with qtbot.waitSignal(page.templateChanged): page.exclusions_panel.exclude.click()
    assert t.excluded_columns == [2]
    page.canvas.select_cells({(2, 2)}); page.exclusions_panel.restore.click()
    assert not t.is_excluded(2, 2) and t.is_excluded(1, 2)
    page.exclusions_panel.undo.click(); assert t.is_excluded(2, 2)
    page.exclusions_panel.redo.click(); assert not t.is_excluded(2, 2)
    page.set_document(page.image, template())
    assert not page.exclusions_panel.undo.isEnabled()
    assert not page.template.excluded_cells


def test_headers_remain_selectable_when_grid_touches_page_edges(qtbot):
    from tablescan_local.ui import TablePage
    t = template(); t.row_guides = t.column_guides = [0, .25, .5, .75, 1]
    t.table_rect = NormalizedRect(0, 0, 1, 1)
    page = TablePage(); qtbot.addWidget(page); page.resize(1200, 800); page.show()
    page.set_document(np.full((500, 500, 3), 255, np.uint8), t)
    page.exclusions_button.click(); page.canvas.fit_document(); qtbot.wait(20)
    point = page.canvas.mapFromScene(QPointF(-page.canvas._header_band()/2, 180))
    assert page.canvas.viewport().rect().contains(point)
    QTest.mouseClick(page.canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert page.canvas.selected_cells == {(1, c) for c in range(4)}
