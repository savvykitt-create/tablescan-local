from types import SimpleNamespace
import numpy as np
import pytest

from tablescan_local import adaptive_ocr, ocr
from tablescan_local.constraints import ValueConstraints
from tablescan_local.domain import CellResult, PageResult, TableTemplate, NormalizedRect
from tablescan_local.numeric_decoder import CtcCandidate


@pytest.fixture
def setup(monkeypatch):
    crop = np.full((40, 100, 3), 250, np.uint8)
    state = dict(calls=0, complete=0, raw='12.3', confidence=.99, margin=4,
                 separator=True, digits=3, conflict=False)
    def hypotheses(prepared, model, constraints):
        state['calls'] += 1
        raw = '12.8' if state['conflict'] and model == 'v6' else state['raw']
        return raw, state['confidence'], [CtcCandidate(raw, -1, .99),
                CtcCandidate('17.3', -1-state['margin'], .5)], None
    def complete(crop, constraints, retries, *, recognition_cache):
        state['complete'] += 1
        assert recognition_cache or constraints.value_format == 'complex_numeric'
        return ocr.OcrValue('12.8', .99, flags=['model_disagreement'])
    engine = SimpleNamespace(_engine='v5', _precision_engine='v6', _prepare=lambda x:x,
        _recognize_hypotheses=hypotheses, _recognize_numeric_slow=complete)
    monkeypatch.setattr(ocr, 'remove_edge_rules', lambda x:x)
    monkeypatch.setattr(ocr, 'isolate_blue_ink', lambda x:x)
    monkeypatch.setattr(ocr, 'detect_decimal_separator', lambda *args:
        ocr.DecimalEvidence(10, 2, .9) if state['separator'] else None)
    monkeypatch.setattr(adaptive_ocr, 'infer_numeric_geometry', lambda *args:
        SimpleNamespace(confidence=.9, digit_count=state['digits']))
    return engine, crop, state


def test_agreement_short_pass_requires_human_review_and_reuses_identical_views(setup):
    engine, crop, state = setup
    result = adaptive_ocr.recognize(engine, crop, ValueConstraints('numeric',decimal_places=1), [])
    assert result.text == '12.3' and state['complete'] == 0
    assert state['calls'] == 2  # two identical prepared views, two independent models
    cell = CellResult(0,0,result.raw_text,result.text,result.confidence,flags=result.flags)
    assert cell.needs_review
    cell.status = 'confirmed'
    assert not cell.needs_review


@pytest.mark.parametrize('key,value', [('confidence',.94),('confidence',float('nan')),
    ('margin',1),('separator',False),('digits',4),('conflict',True),('raw','letters')])
def test_ambiguity_escalates_without_losing_full_cascade_flags(setup,key,value):
    engine, crop, state = setup
    state[key] = value
    result = adaptive_ocr.recognize(engine,crop,ValueConstraints('numeric',decimal_places=1),[])
    assert result.text == '12.8' and state['complete'] == 1
    assert result.flags == ['model_disagreement','adaptive_full_pass']


def test_expected_range_is_not_enough_to_accept_short_pass(setup):
    engine,crop,state=setup
    adaptive_ocr.recognize(engine,crop,ValueConstraints('numeric',expected_maximum=10),[])
    assert state['complete'] == 1


def test_experimental_marker_alone_does_not_send_easy_cells_to_slow():
    from tablescan_local.slow_mode import eligible_cells
    template=TableTemplate('a','a',NormalizedRect(0,0,1,1),[0,1],[0,.5,1],header_rows=0,row_label_columns=0)
    template.ensure_column_rules()
    for rule in template.column_rules: rule.value_format='numeric'
    cells=[CellResult(0,c,'12.3','12.3',.99,flags=['adaptive_short_pass','adaptive_review_required']) for c in range(2)]
    cells[1].flags.append('table_outlier')
    assert eligible_cells(PageResult(0,'a',cells,[]),template)==[cells[1]]
