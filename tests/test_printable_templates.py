import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from tablescan_local.domain import CellAddress, FieldRegion, NormalizedRect, TableTemplate
from tablescan_local.printable_templates import (
    BundledFormsPanel, PrintableTemplateDialog, PrintOptions, bundled_forms,
    export_template_excel, save_bundled_form,
)


SOURCE = Path(__file__).parents[1] / 'src/tablescan_local/default_templates'


def sample():
    template = TableTemplate('custom', '=Title', NormalizedRect(0, 0, .8, .9),
                             [0, .2, .6, 1], [0, .25, 1], header_rows=1)
    template.ensure_column_rules()
    template.column_rules[1].name = 'Measurement'
    template.set_fixed_value(0, 0, 'ID')
    template.set_fixed_value(1, 0, '001')
    template.set_fixed_value(2, 0, '=1+1')
    template.excluded_cells = [CellAddress(2, 1)]
    template.fields = [FieldRegion('date', 'Date', NormalizedRect(0, 0, .2, .1)),
                       FieldRegion('protocol', 'Protocol', NormalizedRect(.4, 0, .2, .1),
                                   source='fixed', fixed_value='Test A')]
    return template


def test_export_preserves_editable_grid_fixed_text_print_settings_and_source(tmp_path):
    template = sample()
    before = template.to_dict()
    path = export_template_excel(template, tmp_path / 'custom.xlsx')
    assert template.to_dict() == before
    with path.open('rb') as handle:
        workbook = load_workbook(handle)
        sheet = workbook.active
        assert sheet['A1'].value == '=Title' and sheet['A1'].data_type == 's'
        assert sheet['A2'].value.startswith('Date: _')
        assert sheet['A3'].value == 'Protocol: Test A'
        assert [sheet.cell(5, c).value for c in (1, 2)] == ['ID', 'Measurement']
        assert sheet['A6'].value == '001'
        assert sheet['A7'].value == '=1+1' and sheet['A7'].data_type == 's'
        assert sheet['B6'].value is sheet['B7'].value is None
        assert sheet['B7'].fill.fgColor.rgb == '00F0F0F0'
        for row in sheet.iter_rows(min_row=5, max_row=7):
            for cell in row:
                assert all(getattr(cell.border, side).style == 'thin'
                           for side in ('left', 'right', 'top', 'bottom'))
        assert sheet.column_dimensions['B'].width / sheet.column_dimensions['A'].width == pytest.approx(3)
        assert sheet.row_dimensions[6].height > sheet.row_dimensions[5].height
        assert sheet.page_setup.orientation == 'portrait'
        assert sheet.page_setup.paperSize == int(sheet.PAPERSIZE_A4)
        assert sheet.page_setup.fitToWidth == sheet.page_setup.fitToHeight == 1
        assert sheet.sheet_properties.pageSetUpPr.fitToPage
        assert sheet.print_title_rows == '$5:$5'
        assert sheet.print_area == "'Printable form'!$A$1:$B$7"
        workbook.close()


@pytest.mark.parametrize('key', ['plantar', 'von_frey', 'staircase', 'staircase_portrait', 'maxspeed'])
def test_every_factory_template_can_be_exported_and_originals_saved_byte_for_byte(tmp_path, key):
    template = TableTemplate.from_dict(json.loads((SOURCE / f'{key}.json').read_text()))
    path = export_template_excel(template, tmp_path / f'{key}-custom.xlsx')
    book = load_workbook(path)
    assert book.active.max_column == template.columns
    assert book.active.page_setup.orientation == ('landscape' if template.reference_page_aspect > 1 else 'portrait')
    book.close()
    for extension in ('xlsx', 'pdf'):
        path = save_bundled_form(key, tmp_path / f'original-{key}.{extension}', extension)
        assert path.read_bytes() == (SOURCE / f'{key}.{extension}').read_bytes()


def test_options_support_long_tables_and_letter_paper(tmp_path):
    path = export_template_excel(sample(), tmp_path / 'long.xlsx', PrintOptions('Letter', 'landscape', False))
    workbook = load_workbook(path)
    assert workbook.active.page_setup.paperSize == int(workbook.active.PAPERSIZE_LETTER)
    assert workbook.active.page_setup.orientation == 'landscape'
    assert workbook.active.page_setup.fitToWidth == 1
    assert workbook.active.page_setup.fitToHeight == 0
    workbook.close()


@pytest.mark.parametrize('bad_guides', [[0, 0, 1], [0, 1, .5], [0, float('nan'), 1]])
def test_invalid_grid_does_not_overwrite_existing_export(tmp_path, bad_guides):
    template = sample()
    template.row_guides = bad_guides
    target = tmp_path / 'previous.xlsx'
    target.write_bytes(b'previous workbook')
    with pytest.raises(ValueError, match='increasing order'):
        export_template_excel(template, target)
    assert target.read_bytes() == b'previous workbook'


def test_unknown_bundle_or_extension_does_not_write(tmp_path):
    assert len(bundled_forms()) == 5
    for key, extension in [('../plantar', 'xlsx'), ('plantar', 'json')]:
        with pytest.raises(ValueError):
            save_bundled_form(key, tmp_path / 'bad', extension)
    assert not list(tmp_path.iterdir())


def test_library_save_action_and_cancel_do_not_change_templates(qtbot, tmp_path, monkeypatch):
    from tablescan_local import printable_templates as module
    panel = BundledFormsPanel()
    qtbot.addWidget(panel)
    target = tmp_path / 'saved'
    monkeypatch.setattr(module.QFileDialog, 'getSaveFileName', lambda *args: (str(target), ''))
    monkeypatch.setattr(module, 'notify', lambda *args: None)
    panel.forms.setCurrentIndex(panel.forms.findData('plantar'))
    panel.save_excel.click()
    assert target.with_suffix('.xlsx').read_bytes() == (SOURCE / 'plantar.xlsx').read_bytes()
    monkeypatch.setattr(module.QFileDialog, 'getSaveFileName', lambda *args: ('', ''))
    panel.save_pdf.click()
    assert not target.with_suffix('.pdf').exists()


def test_export_dialog_uses_current_options(qtbot, tmp_path, monkeypatch):
    from tablescan_local import printable_templates as module
    dialog = PrintableTemplateDialog(sample())
    qtbot.addWidget(dialog)
    path = tmp_path / 'form'
    monkeypatch.setattr(module.QFileDialog, 'getSaveFileName', lambda *args: (str(path), ''))
    dialog.orientation.setCurrentIndex(dialog.orientation.findData('landscape'))
    dialog.pagination.setCurrentIndex(dialog.pagination.findData(False))
    dialog.save_button.click()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.saved_path == path.with_suffix('.xlsx')
    workbook = load_workbook(dialog.saved_path)
    assert workbook.active.page_setup.orientation == 'landscape'
    assert workbook.active.page_setup.fitToHeight == 0
    workbook.close()


@pytest.mark.parametrize('code', ['en', 'ru', 'cs'])
def test_bundled_panel_fits_narrow_library_and_all_actions_have_help(qtbot, code):
    from PySide6.QtWidgets import QAbstractButton
    from tablescan_local.i18n import set_language
    set_language(code)
    try:
        panel = BundledFormsPanel()
        qtbot.addWidget(panel)
        panel.resize(680, 220)
        panel.show()
        assert panel.minimumSizeHint().width() <= 680
        assert panel.width() <= 680
        buttons = panel.findChildren(QAbstractButton)
        assert sum(bool(button.property('actionHelp')) for button in buttons) == 2
        assert all(button.property('hasActionHelp') for button in buttons)
    finally:
        set_language('en')


def test_library_exports_selected_template_and_has_original_downloads(qtbot, monkeypatch):
    from tablescan_local import ui
    seen = []

    class ExportDialog:
        saved_path = None

        def __init__(self, template, parent):
            seen.append(template)

        def exec(self):
            return 0

    monkeypatch.setattr('tablescan_local.printable_templates.PrintableTemplateDialog', ExportDialog)
    page = ui.TemplateLibraryPage()
    qtbot.addWidget(page)
    page._export_selected_template()
    assert seen == []
    custom = sample()
    factory = TableTemplate.from_dict(json.loads((SOURCE / 'plantar.json').read_text()))
    page.set_templates([custom, factory])
    page.select_template(custom.id)
    page._export_selected_template()
    assert seen == [custom]
    assert page.bundled_forms.forms.count() == 5
