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


def test_same_nonuniform_row_structure_is_fitted_to_observed_lines():
    t = master()
    # A short printed header is not an extra animal and must not prevent fit.
    t.row_guides[1] -= .009
    d = GridDetection(t.table_rect, [v - .01 for v in t.row_guides],
                      [v + .01 for v in t.column_guides])
    old = t.to_dict()
    fitted = fit_template(t, d)
    assert fitted.row_guides == d.row_guides
    assert fitted.column_guides == d.column_guides
    assert t.to_dict() == old


@pytest.mark.parametrize('animals', range(1, 200))
def test_every_supported_animal_count_and_short_header(animals):
    t = master()
    t.fields = []
    master_heights = np.array([.6] + [1.] * 20)
    t.row_guides = (.25 + np.r_[0, np.cumsum(master_heights)] / master_heights.sum() * .63).tolist()
    # A short header followed by arbitrary count of equally tall data rows.
    heights = np.array([.6] + [1.] * animals)
    guides = .25 + np.r_[0, np.cumsum(heights)] / heights.sum() * .63
    d = GridDetection(t.table_rect, guides.tolist(), t.column_guides)
    f = fit_template(t, d)
    assert f.rows == animals + 1
    assert f.cell_rules[0].row_end == animals
    assert f.row_guides == d.row_guides


@pytest.mark.parametrize('animals', [1, 7, 19, 23, 41, 57, 99, 199])
@pytest.mark.parametrize('assay', ['von_frey', 'plantar', 'staircase'])
def test_detect_and_fit_unlisted_counts_from_pixels(animals, assay):
    import cv2, json
    from pathlib import Path
    from tablescan_local.imaging import detect_grid
    path = Path(__file__).parents[1] / 'src/tablescan_local/default_templates' / (assay + '.json')
    t = TableTemplate.from_dict(json.loads(path.read_text()))
    height = max(1600, (animals + 1) * 35)
    width = round(height * t.reference_page_aspect)
    # Render complete lines at adequate scan resolution; no row-count hints.
    image = np.full((height, width, 3), 255, np.uint8)
    x = [round(v * width) for v in t.column_guides]
    y = np.linspace(t.row_guides[0], t.row_guides[-1], animals + 2) * height
    for line in y:
        cv2.line(image, (x[0], round(line)), (x[-1], round(line)), (0, 0, 0), 2)
    for line in x:
        cv2.line(image, (line, round(y[0])), (line, round(y[-1])), (0, 0, 0), 2)
    d = detect_grid(image)
    f = fit_template(t, d, width / height)
    assert f.rows == animals + 1
    assert f.columns == t.columns
    for column in range(t.columns):
        assert f.cell_constraints(animals, column)[0] is not None
