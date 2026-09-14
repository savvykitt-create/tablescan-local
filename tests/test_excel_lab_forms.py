"""Fit shipped Excel templates to PDFs printed from variable-length workbooks."""
import json
from pathlib import Path

import pytest

from tablescan_local.domain import TableTemplate
from tablescan_local.imaging import detect_grid, load_document
from tablescan_local.template_fit import fit_template

ROOT = Path(__file__).parents[1] / 'examples/lab_forms_excel'


@pytest.mark.parametrize('assay', ['von_frey', 'plantar', 'staircase'])
@pytest.mark.parametrize('rats', [5, 10, 15, 20, 30, 40])
def test_excel_print_fits_all_animals_and_rules(assay, rats):
    template = TableTemplate.from_dict(json.loads((ROOT / 'templates' / f'{assay}.json').read_text()))
    original = template.to_dict()
    image = load_document(ROOT / 'fixtures' / f'{assay}_{rats}.pdf')[0]
    fitted = fit_template(template, detect_grid(image), image.shape[1] / image.shape[0])
    assert fitted.rows == rats + 1
    assert fitted.columns == len(template.column_rules)
    assert template.to_dict() == original
    assert [field.name for field in fitted.fields if field.source != 'fixed'] == ['Date', 'Session', 'Operator']
    for column in range(fitted.columns):
        rule, name = fitted.cell_constraints(rats, column)
        assert rule is not None and name
        if column == 0:
            assert rule.hard_errors('') and not rule.hard_errors(str(rats))
        elif column == fitted.columns - 1:
            assert rule.value_format == 'text' and not rule.hard_errors('CHECK')
        else:
            assert rule.hard_errors('-1') and not rule.hard_errors('')
