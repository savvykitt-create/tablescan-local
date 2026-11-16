import json
from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries

from tablescan_local.domain import CellAddress, FieldRegion, NormalizedRect, TableTemplate
from tablescan_local.printable_templates import (
    BundledFormsPanel, PrintableTemplateDialog, PrintOptions, bundled_forms,
    export_template_excel, save_bundled_form, template_page,
)


SOURCE = Path(__file__).parents[1] / 'src/tablescan_local/default_templates'


def sample():
    template = TableTemplate('custom', '=Title', NormalizedRect(.1, .3, .75, .6),
                             [.3, .45, .67, .9], [.1, .3, .85], header_rows=1,
                             reference_page_aspect=210/297)
    template.ensure_column_rules()
    template.column_rules[1].name = 'Measurement'
    template.set_fixed_value(0, 0, 'ID')
    template.set_fixed_value(1, 0, '001')
    template.set_fixed_value(2, 0, '=1+1')
    template.excluded_cells = [CellAddress(2, 1)]
    template.fields = [FieldRegion('date', 'Date', NormalizedRect(.1, .08, .22, .09), printed_label='Date'),
                       FieldRegion('protocol', 'Protocol', NormalizedRect(.5, .08, .3, .09),
                                   source='fixed', fixed_value='Test A')]
    return template


def named_box(book, name, page):
    sheet = book.active
    _, address = next(book.defined_names[name].destinations)
    c1, r1, c2, r2 = range_boundaries(address)
    def x(c): return sum(sheet.column_dimensions[get_column_letter(i)].width * 6 for i in range(1, c))
    def y(r): return sum(sheet.row_dimensions[i].height for i in range(1, r))
    width, height = page.width_mm / 25.4 * 72, page.height_mm / 25.4 * 72
    return ((x(c1)/width, y(r1)/height, x(c2+1)/width, y(r2+1)/height), sheet.cell(r1, c1))


def test_export_preserves_page_coordinates_fixed_text_and_source(tmp_path):
    template = sample()
    before = template.to_dict()
    path = export_template_excel(template, tmp_path / 'custom.xlsx')
    assert template.to_dict() == before
    workbook = load_workbook(path)
    sheet, page = workbook.active, template_page(template)
    for r in range(template.rows):
        for c in range(template.columns):
            box, cell = named_box(workbook, f'TableScan_Cell_{r+1}_{c+1}', page)
            assert box == pytest.approx((template.column_guides[c],template.row_guides[r],
                                        template.column_guides[c+1],template.row_guides[r+1]), abs=.002)
    assert named_box(workbook, 'TableScan_Cell_1_1', page)[1].value == 'ID'
    assert named_box(workbook, 'TableScan_Cell_1_2', page)[1].value == 'Measurement'
    assert named_box(workbook, 'TableScan_Cell_2_1', page)[1].value == '001'
    formula_like = named_box(workbook, 'TableScan_Cell_3_1', page)[1]
    assert formula_like.value == '=1+1' and formula_like.data_type == 's'
    assert named_box(workbook, 'TableScan_Cell_2_2', page)[1].value is None
    assert named_box(workbook, 'TableScan_Cell_3_2', page)[1].value is None
    for index, field in enumerate(template.fields, 1):
        box, cell = named_box(workbook, f'TableScan_Field_{index}', page)
        r=field.rect
        assert box == pytest.approx((r.x,r.y,r.x+r.width,r.y+r.height), abs=.002)
        assert cell.comment.text == field.name
    assert named_box(workbook, 'TableScan_Field_1', page)[1].value == 'Date'
    assert named_box(workbook, 'TableScan_Field_2', page)[1].value == 'Test A'
    assert '=Title' not in [cell.value for row in sheet for cell in row]
    assert workbook._named_styles['Normal'].font.name == 'Courier New'
    assert workbook._fonts[0].name == 'Courier New'
    assert sheet.page_setup.orientation == 'portrait'
    assert sheet.page_setup.paperSize == int(sheet.PAPERSIZE_A4)
    assert sheet.page_setup.scale == 100
    assert not sheet.sheet_properties.pageSetUpPr.fitToPage
    assert sheet.print_title_rows is None
    assert all(getattr(sheet.page_margins,name)==0 for name in ('left','right','top','bottom','header','footer'))
    workbook.close()


@pytest.mark.parametrize('key', ['plantar', 'von_frey', 'staircase', 'staircase_portrait', 'maxspeed'])
def test_every_factory_template_keeps_grid_positions_and_originals_saved_byte_for_byte(tmp_path, key):
    template = TableTemplate.from_dict(json.loads((SOURCE / f'{key}.json').read_text()))
    path = export_template_excel(template, tmp_path / f'{key}-custom.xlsx')
    book = load_workbook(path)
    page = template_page(template)
    assert book.active.page_setup.orientation == ('landscape' if template.reference_page_aspect > 1 else 'portrait')
    for r,c in ((0,0),(template.rows-1,template.columns-1),(template.rows//2,template.columns//2)):
        box,_ = named_box(book,f'TableScan_Cell_{r+1}_{c+1}',page)
        assert box == pytest.approx((template.column_guides[c],template.row_guides[r],
                                    template.column_guides[c+1],template.row_guides[r+1]), abs=.002)
    for i,field in enumerate(template.fields,1):
        if field.source == 'ocr':
            box,_ = named_box(book,f'TableScan_Field_{i}',page)
            r=field.rect
            assert box == pytest.approx((r.x,r.y,r.x+r.width,r.y+r.height),abs=.002)
    book.close()
    for extension in ('xlsx', 'pdf'):
        path = save_bundled_form(key, tmp_path / f'original-{key}.{extension}', extension)
        assert path.read_bytes() == (SOURCE / f'{key}.{extension}').read_bytes()


@pytest.mark.parametrize('options', [PrintOptions('Letter'), PrintOptions('Template','landscape'), PrintOptions(single_page=False)])
def test_reflow_options_cannot_create_an_incompatible_form(tmp_path,options):
    target=tmp_path/'keep.xlsx';target.write_bytes(b'original')
    with pytest.raises(ValueError):
        export_template_excel(sample(),target,options)
    assert target.read_bytes()==b'original'


def test_nonstandard_page_keeps_its_aspect_in_custom_paper(tmp_path):
    template=sample();template.reference_page_aspect=1.1
    path=export_template_excel(template,tmp_path/'custom-page.xlsx')
    book=load_workbook(path);setup=book.active.page_setup
    assert setup.paperSize is None
    assert setup.orientation=='landscape'
    assert float(setup.paperHeight[:-2])/float(setup.paperWidth[:-2]) == pytest.approx(1.1)
    assert template_page(template).width_mm/template_page(template).height_mm == pytest.approx(1.1)
    book.close()


def test_unlabelled_ocr_field_stays_blank_and_metadata_inside_grid_does_not_overwrite_cells(tmp_path):
    template=sample();template.fields[0].printed_label=''
    template.fields.append(FieldRegion('assay','Assay',NormalizedRect(.1,.3,.2,.15),source='fixed',fixed_value='SECRET METADATA'))
    path=export_template_excel(template,tmp_path/'fields.xlsx')
    book=load_workbook(path);page=template_page(template)
    assert named_box(book,'TableScan_Field_1',page)[1].value is None
    assert named_box(book,'TableScan_Cell_1_1',page)[1].value=='ID'
    assert 'SECRET METADATA' not in [c.value for row in book.active for c in row]
    book.close()


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


def test_export_dialog_preserves_template_layout(qtbot, tmp_path, monkeypatch):
    from tablescan_local import printable_templates as module
    dialog = PrintableTemplateDialog(sample())
    qtbot.addWidget(dialog)
    path = tmp_path / 'form'
    monkeypatch.setattr(module.QFileDialog, 'getSaveFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(module,'notify',lambda *args:None)
    assert not hasattr(dialog,'pagination')
    assert not hasattr(dialog,'orientation')
    assert not dialog.save_button.toolTip()
    assert not dialog.cancel_button.toolTip()
    dialog.save_button.click()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.saved_path == path.with_suffix('.xlsx')
    workbook = load_workbook(dialog.saved_path)
    assert workbook.active.page_setup.orientation == 'portrait'
    assert workbook.active.page_setup.scale == 100
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
