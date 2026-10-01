import json
from pathlib import Path

import numpy as np
import pypdfium2
import pytest

from tablescan_local.domain import TableTemplate
from tablescan_local.imaging import detect_grid, load_document
from tablescan_local.storage import LocalStore
from tablescan_local.template_fit import fit_template

ROOT = Path(__file__).parents[1] / 'src/tablescan_local/default_templates'
OLD = Path(__file__).parent / 'fixtures/factory_templates_before_field_expansion'


@pytest.mark.parametrize('assay,maximum', [('plantar', 35), ('von_frey', 200)])
def test_measurement_limits_and_exact_precision_survive_fitting(assay, maximum):
    template = TableTemplate.from_dict(json.loads((ROOT / f'{assay}.json').read_text()))
    image = load_document(ROOT / f'{assay}.pdf')[0]
    fitted = fit_template(template, detect_grid(image), image.shape[1] / image.shape[0])
    for col in range(1, 11):
        rules = [fitted.column_rules[col].constraints(), fitted.cell_constraints(1, col)[0],
                 fitted.cell_constraints(fitted.rows - 1, col)[0]]
        for rule in rules:
            for valid in ('0.0', '12,3', f'{maximum}.0', ''):
                assert not rule.hard_errors(valid)
            for invalid in ('-0.1', f'{maximum}.1', '12', '12.34'):
                assert rule.hard_errors(invalid)
    assert not fitted.cell_constraints(1, 0)[0].hard_errors('36')
    assert not fitted.cell_constraints(1, 11)[0].hard_errors('Free text')
    assert [fitted.fixed_value(0, c) for c in range(fitted.columns)] == [r.name for r in fitted.column_rules]


@pytest.mark.parametrize('assay', ['plantar', 'von_frey', 'staircase', 'staircase_portrait'])
def test_metadata_captures_label_line_and_original_space_below(assay):
    template = TableTemplate.from_dict(json.loads((ROOT / f'{assay}.json').read_text()))
    old = TableTemplate.from_dict(json.loads((OLD / f'{assay}.json').read_text()))
    with pypdfium2.PdfDocument(ROOT / f'{assay}.pdf') as pdf:
        page = pdf[0]; text = page.get_textpage(); height = page.get_height()
        for region, previous in zip(template.fields, old.fields, strict=True):
            if region.source != 'ocr':
                continue
            index = text.search(region.name).get_next()[0]
            label_top = (height - text.get_charbox(index)[3]) / height
            assert region.rect.y < label_top
            assert region.rect.y * height >= 60
            # The border inset leaves up to two points outside the OCR crop.
            assert region.rect.y + region.rect.height >= previous.rect.y + previous.rect.height - 2 / height
            assert region.printed_label == region.name
            assert region.rect.y + region.rect.height < template.table_rect.y


@pytest.mark.parametrize('assay', ['plantar', 'von_frey', 'staircase', 'staircase_portrait'])
def test_old_factory_upgrade_keeps_custom_edits_and_job_drafts(tmp_path, assay):
    store = LocalStore(tmp_path)
    store.install_default_templates()
    old = TableTemplate.from_dict(json.loads((OLD / f'{assay}.json').read_text()))
    store.save_template(old)
    store.save_draft('old-job', old)
    store.install_default_templates()
    updated = next(t for t in store.load_templates() if t.id == old.id)
    assert updated.template_version > old.template_version
    assert updated.fields[0].printed_label == 'Date'
    assert updated.fixed_cells
    assert store.load_draft('old-job').to_dict() == old.to_dict()
    old.fields[0].rect.height += .01
    store.save_template(old)
    store.install_default_templates()
    assert next(t for t in store.load_templates() if t.id == old.id).to_dict() == old.to_dict()


def test_template_selector_shows_latest_family_and_loads_constants(qtbot):
    from tablescan_local.ui import TablePage
    page = TablePage(); qtbot.addWidget(page)
    old = TableTemplate.from_dict(json.loads((OLD / 'von_frey.json').read_text()))
    current = TableTemplate.from_dict(json.loads((ROOT / 'von_frey.json').read_text()))
    current.id = 'new-version'
    page.set_saved_templates([old, current])
    assert page.saved_template_select.count() == 2  # placeholder + latest version
    assert page.saved_template_select.itemData(1) == current.id
    page.set_document(np.full((600, 840, 3), 255, np.uint8), current)
    assert len(page.template.fixed_cells) == current.columns
    assert page.template.fixed_value(0, 1) == 'L1'


@pytest.mark.parametrize('layout', ['beside', 'below', 'combined', 'blank', 'similar'])
def test_expanded_field_removes_only_printed_caption_and_preserves_raw(layout):
    from tablescan_local.ocr import LocalOcrEngine
    engine = object.__new__(LocalOcrEngine)
    def box(x, y):
        return [[x, y], [x + 60, y], [x + 60, y + 16], [x, y + 16]]
    items = [(box(0, 15), 'Operator', .99)]
    expected = 'Savva'
    if layout == 'beside':
        items.append((box(80, 5), 'Savva', .97))
    elif layout == 'below':
        items.append((box(0, 45), 'Savva', .97))
    elif layout == 'combined':
        items = [(box(0, 15), 'Operator: Savva', .97)]
    elif layout == 'blank':
        expected = ''
    else:
        items = [(box(0, 15), 'Operatorson', .97)]
        expected = 'Operatorson'
    engine._engine = lambda *args, **kwargs: (items, None)
    result = engine.recognize_region(np.full((80, 250, 3), 100, np.uint8), printed_label='Operator')
    assert result.text == expected
    assert 'Operator' in result.raw_text
    assert result.flags == []
