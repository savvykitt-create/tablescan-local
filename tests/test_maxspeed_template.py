"""MaxSpeed's printable form, OCR layout and upgrade behavior stay aligned."""
import json
from pathlib import Path

import pypdfium2
import pytest

from tablescan_local.domain import TableTemplate
from tablescan_local.imaging import detect_grid, load_document
from tablescan_local.storage import LocalStore
from tablescan_local.template_fit import fit_template
from tablescan_local.template_matcher import rank_templates

ROOT = Path(__file__).parents[1] / 'src/tablescan_local/default_templates'


def load_template():
    return TableTemplate.from_dict(json.loads((ROOT / 'maxspeed.json').read_text()))


@pytest.mark.parametrize('animals', [40, 25])
def test_maxspeed_print_fits_and_keeps_headers_rules_and_metadata(animals):
    template = load_template()
    original = template.to_dict()
    image = load_document(ROOT / 'maxspeed.pdf')[0]
    if animals < 40:
        # Retain a shorter, complete grid and its unchanged metadata.
        bottom = round(template.row_guides[animals + 1] * image.shape[0])
        image[bottom + 5:] = 255
    detection = detect_grid(image)
    fitted = fit_template(template, detection, image.shape[1] / image.shape[0])
    assert (fitted.rows, fitted.columns) == (animals + 1, 12)
    assert template.to_dict() == original
    headers = ['ID', *map(str, range(1, 11)), 'Notes']
    assert [fitted.fixed_value(0, column) for column in range(12)] == headers
    assert [rule.name for rule in fitted.column_rules] == headers
    for row in (1, animals):
        assert fitted.cell_constraints(row, 0)[0].hard_errors('')
        assert not fitted.cell_constraints(row, 0)[0].hard_errors(str(animals))
        for column in range(1, 11):
            rule, _ = fitted.cell_constraints(row, column)
            for value in ('', '0', '58', '1000', '32.5', '32,5'):
                assert not rule.hard_errors(value)
            for value in ('-1', 'refused'):
                assert rule.hard_errors(value)
        assert not fitted.cell_constraints(row, 11)[0].hard_errors('Refused trial 3')
    assert [(field.name, field.fixed_value) for field in fitted.fields if field.source == 'fixed'] == [('Assay', 'MaxSpeed')]
    defaults = [TableTemplate.from_dict(json.loads(path.read_text())) for path in ROOT.glob('*.json')]
    assert rank_templates(image.shape, detection, defaults)[0].template.id == template.id
    with pypdfium2.PdfDocument(ROOT / 'maxspeed.pdf') as pdf:
        assert len(pdf) == 1
        page = pdf[0]
        width, height = page.get_size()
        assert (width, height) == pytest.approx((595.28, 841.89), abs=.1)
        text = page.get_textpage()
        for region in fitted.fields:
            if region.source == 'fixed':
                continue
            assert region.printed_label == region.name
            index, count = text.search(region.name).get_next()
            for char in (index, index + count - 1):
                left, bottom, right, top = text.get_charbox(char)
                assert region.rect.x < left / width < right / width < region.rect.x + region.rect.width
                assert region.rect.y < (height - top) / height < (height - bottom) / height < region.rect.y + region.rect.height
            assert region.rect.height * height >= 34
            assert region.rect.y + region.rect.height < fitted.table_rect.y


def test_existing_install_adds_only_maxspeed_and_respects_deletion(tmp_path):
    store = LocalStore(tmp_path)
    (tmp_path / '.default-templates-installed').write_text('installed\n')
    (tmp_path / '.default-staircase-portrait-installed').write_text('installed\n')
    store.install_default_templates()
    assert [template.name for template in store.load_templates()] == ['MaxSpeed']
    saved = store.load_templates()[0]
    assert Path(saved.reference_source_path).read_bytes() == (ROOT / 'maxspeed.pdf').read_bytes()
    store.install_default_templates()
    assert len(store.load_templates()) == 1
    store.delete_template(saved.id)
    store.install_default_templates()
    assert store.load_templates() == []


def test_maxspeed_upgrade_preserves_an_existing_edited_family(tmp_path):
    store = LocalStore(tmp_path)
    (tmp_path / '.default-templates-installed').write_text('installed\n')
    (tmp_path / '.default-staircase-portrait-installed').write_text('installed\n')
    custom = load_template()
    custom.id = 'custom-version'
    custom.template_version = 2
    custom.name = 'My MaxSpeed'
    custom.column_rules[1].maximum = 70
    store.save_template(custom)
    store.save_draft('ongoing-job', custom)
    store.install_default_templates()
    assert [template.to_dict() for template in store.load_templates()] == [custom.to_dict()]
    assert store.load_draft('ongoing-job').to_dict() == custom.to_dict()
