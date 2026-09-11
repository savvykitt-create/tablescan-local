import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from tablescan_local.domain import CellResult, PageResult, TableTemplate, NormalizedRect, CellRuleRegion
from tablescan_local.constraints import ValueConstraints
from tablescan_local import slow_mode as slow
from tablescan_local.slow_parsing import parse_table, row_values, num


def fixture_page():
    template = TableTemplate('x', 'Test', NormalizedRect(0, 0, 1, 1), [0, .5, 1], [0, .2, .6, 1])
    template.row_label_columns = 1
    template.ensure_column_rules()
    template.cell_rules = [CellRuleRegion('r', 'Values', 0, 1, 1, 2, ValueConstraints('numeric', maximum=70, decimal_places=1))]
    cells = [CellResult(r, c, '31.6', '31.6', .8, flags=['model_disagreement']) for r in range(2) for c in range(3)]
    return template, PageResult(0, 'source.pdf', cells, [])


def test_agreement_preserves_exclusions_manual_edits_and_evidence():
    t, p = fixture_page()
    p.excluded_rows = [1]
    p.cell(1, 1).status = 'excluded'; p.cell(1, 1).final_text = ''
    p.cell(0, 2).status = 'corrected'
    changed = slow.apply_agreement(p, t, {1: ['37.6', '37.6'], 2: ['37.6', '37.6']}, {0: ['37.6', '37.6'], 1: ['37.6', '37.6']})
    assert changed == 1
    assert p.cell(0, 1).final_text == '37.6'
    assert p.cell(0, 1).raw_text == '31.6' and p.cell(0, 1).confidence == .8
    assert p.cell(0, 1).needs_review
    assert p.cell(0, 2).final_text == p.cell(0, 0).final_text == '31.6'
    assert p.cell(1, 1).final_text == '' and p.cell(1, 2).final_text == '31.6'
    restored = PageResult.from_dict(p.to_dict())
    assert restored.cell(0, 1).slow_mode_evidence['baseline'] == '31.6'


def test_no_change_without_valid_agreement_or_original_review():
    t, p = fixture_page()
    p.cell(0, 1).flags = []
    assert slow.apply_agreement(p, t, {1: ['37.6', '99.9'], 2: ['37.6', None]}, {0: ['37.6', '99.9'], 1: ['38.6', None]}) == 0
    assert all(c.final_text == '31.6' for c in p.cells)


def test_only_changed_disputed_rows_are_routed():
    t, p = fixture_page()
    assert slow.disputed_rows(p, t, {1: ['31.60', '31.6'], 2: ['37.6', '31.6']}) == [1]
    p.excluded_rows = [1]
    assert slow.disputed_rows(p, t, {1: ['31.6', '31.6'], 2: ['37.6', '31.6']}) == []


def test_structure_parser_rejects_shifted_and_duplicate_rows():
    assert parse_table('[{"row":1,"values":["1","2"]},{"row":1,"values":["3","4"]}]', 2, 2) == {}
    assert parse_table('[{"row":1,"values":["1"]}]', 2, 2) == {}
    assert row_values('12.3 45.6 extra', 2) is None
    assert row_values('12.3 45.6 7.8', 2) is None
    assert row_values('12,3 | 45,6', 2) == ['12,3', '45,6']
    assert num('10.0') == '10'


def test_failure_keeps_baseline_and_cancellation_propagates(tmp_path, monkeypatch):
    t, p = fixture_page()
    original = p.to_dict()['cells']
    monkeypatch.setattr(slow, 'run_model', Mock(side_effect=RuntimeError('worker crashed')))
    slow.refine_page(np.full((100, 200, 3), 255, np.uint8), p, t, tmp_path, {})
    assert p.slow_mode['status'] == 'failed'
    assert p.to_dict()['cells'] == original
    monkeypatch.setattr(slow, 'run_model', Mock(side_effect=InterruptedError))
    with pytest.raises(InterruptedError):
        slow.refine_page(np.full((100, 200, 3), 255, np.uint8), p, t, tmp_path, {})


def test_worker_process_is_terminated_on_cancel(tmp_path):
    # Substitute only the child entry point; exercise real process ownership.
    runner = tmp_path / 'slow_runner.py'
    runner.write_text('import time\ntime.sleep(30)\n')
    from unittest.mock import patch
    with patch.object(slow, '__file__', str(tmp_path / 'slow_mode.py')):
        def cancel(*args):
            raise InterruptedError
        with pytest.raises(InterruptedError):
            slow.run_model('qwen', [{'id': 'table'}], tmp_path, {'python': sys.executable, 'qwen': 'unused'}, cancel)


def test_mode_toggle_requires_high_accuracy_and_defaults_off(qtbot):
    from tablescan_local.ui import TablePage
    page = TablePage(); qtbot.addWidget(page)
    assert not page.slow_mode.isChecked()
    page.high_accuracy.setChecked(False)
    page.slow_mode.setChecked(True)
    assert page.high_accuracy.isChecked()
    page.high_accuracy.setChecked(False)
    assert not page.slow_mode.isChecked()


def test_document_pipeline_uses_current_rules_and_runs_slow_on_every_page(tmp_path, monkeypatch):
    from tablescan_local import pipeline
    t, _ = fixture_page()
    seen = []
    engine = Mock(model_version='test')
    constructor = Mock(return_value=engine)
    monkeypatch.setattr(pipeline, 'LocalOcrEngine', constructor)
    monkeypatch.setattr(slow, 'runtime_config', lambda: {'runtime': 'test'})
    monkeypatch.setattr(pipeline, 'process_page', lambda image, source, index, *args: PageResult(index, source, [], []))
    def refine(image, page, template, directory, config, progress):
        seen.append((page.page_index, template.cell_rules[0].constraints.maximum, config))
        page.slow_mode = {'status': 'complete', 'changed': 0}
    monkeypatch.setattr(slow, 'refine_page', refine)
    t.cell_rules[0].constraints.maximum = 42
    result = pipeline.process_document([np.zeros((2, 2, 3), np.uint8)] * 2, 'test', t,
                                       crop_root=tmp_path, high_accuracy=False, slow_mode=True)
    constructor.assert_called_once_with(high_accuracy=True)
    assert [x[:2] for x in seen] == [(0, 42), (1, 42)]
    assert all(p.slow_mode['status'] == 'complete' for p in result.pages)
