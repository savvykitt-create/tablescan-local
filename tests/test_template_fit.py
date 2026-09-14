import numpy as np
import pytest

from tablescan_local.domain import TableTemplate, NormalizedRect, CellRuleRegion, FieldRegion
from tablescan_local.imaging import GridDetection
from tablescan_local.template_fit import fit_template, fit_document_template


def master():
    t = TableTemplate('master','Animals',NormalizedRect(.1,.25,.8,.63),
                      np.linspace(.25,.88,22).tolist(),[.1,.3,.5,.7,.9],header_rows=1,auto_fit_rows=True)
    t.cell_rules = [CellRuleRegion('rule','All measurements',1,20,1,3)]
    t.fields = [FieldRegion('date','Date',NormalizedRect(.1,.15,.2,.05)),
                FieldRegion('notes','Notes',NormalizedRect(.1,.9,.4,.04))]
    return t


def detection(rows=16, dx=.02, dy=-.01):
    return GridDetection(NormalizedRect(.1+dx,.25+dy,.8,rows*.03),
                         [.25+dy+i*.03 for i in range(rows+1)],
                         [x+dx for x in (.1,.3,.5,.7,.9)])


def test_fewer_animals_preserves_master_and_rule_coverage_and_metadata():
    t=master(); old=t.to_dict(); f=fit_template(t,detection())
    assert t.to_dict()==old
    assert f.rows==16 and f.cell_rules[0].row_end==15
    assert f.fields[0].rect.y==pytest.approx(.14)
    assert f.fields[1].rect.y==pytest.approx(.74)
    assert f.fields[0].rect.x==pytest.approx(.12)
    assert TableTemplate.from_dict(f.to_dict()).auto_fit_rows


def test_expanding_rules_again():
    f=fit_template(fit_template(master(),detection()),detection(21))
    assert f.cell_rules[0].row_end==20


@pytest.mark.parametrize('bad',['columns','proportions','missing_line','warnings'])
def test_incompatible_grid_rejected_without_mutation(bad):
    d=detection();t=master();old=t.to_dict()
    if bad=='columns': d.column_guides.pop(1)
    if bad=='proportions': d.column_guides[1]+=.08
    if bad=='missing_line': d.row_guides.pop(5)
    if bad=='warnings': d.warnings=['reconstructed']
    with pytest.raises(ValueError): fit_template(t,d)
    assert t.to_dict()==old


def test_mixed_page_counts_blocked(monkeypatch):
    ds=iter([detection(21),detection(16)])
    monkeypatch.setattr('tablescan_local.template_fit.detect_grid',lambda _:next(ds))
    with pytest.raises(ValueError,match='separate files'):
        fit_document_template(master(),[np.zeros((2,2)),np.zeros((2,2))])


def test_legacy_template_is_not_changed():
    t=master();t.auto_fit_rows=False
    assert fit_document_template(t,[]).to_dict()==t.to_dict()


def test_taller_rows_do_not_move_metadata_on_same_page():
    t=master();t.reference_page_aspect=1.4
    d=detection(16,dx=0,dy=0)
    d.row_guides=np.linspace(.25,.88,17).tolist()
    d.table_rect=t.table_rect
    f=fit_template(t,d,page_aspect=1.4)
    assert f.fields[0].rect==t.fields[0].rect
    assert f.fields[1].rect==t.fields[1].rect
