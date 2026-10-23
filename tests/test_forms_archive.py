import importlib.util
from io import BytesIO
from pathlib import Path
import re
import shutil
from xml.etree import ElementTree
from zipfile import ZipFile

import pypdfium2
import pytest
from openpyxl import load_workbook


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / 'src/tablescan_local/default_templates'
README = ROOT / 'examples/lab_forms_excel/README.md'
spec = importlib.util.spec_from_file_location('make_forms_archive', ROOT / 'packaging/make_forms_archive.py')
forms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forms)
HEADERS = {
    'von_frey': ['ID', 'L1', 'L2', 'L3', 'L4', 'L5', 'R1', 'R2', 'R3', 'R4', 'R5', 'Notes'],
    'plantar': ['ID', 'L1', 'L2', 'L3', 'L4', 'L5', 'R1', 'R2', 'R3', 'R4', 'R5', 'Notes'],
    'staircase': ['ID', 'L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L drop',
                  'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R drop', 'Notes'],
}
HEADERS['staircase_portrait'] = HEADERS['staircase'][:-1]


@pytest.mark.parametrize('path', sorted(SOURCE.glob('*.xlsx')) +
                         sorted((ROOT / 'examples/lab_forms_excel/xlsx').glob('*.xlsx')),
                         ids=lambda path: f'{path.parent.name}/{path.name}')
def test_shipped_workbooks_have_excel_compatible_print_settings(path):
    forms.validate_workbook(path)
    if path.parent != SOURCE:
        assert path.read_bytes() == (SOURCE / path.name).read_bytes()


def test_archive_contains_readable_complete_forms(tmp_path):
    output = forms.make_archive(tmp_path / 'forms.zip', SOURCE, README)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {'README.md'} | {
            f'{folder}/{key}.{extension}' for key in forms.FORM_KEYS
            for folder, extension in [('Excel', 'xlsx'), ('PDF', 'pdf'), ('templates', 'json')]
        }
        for key in forms.FORM_KEYS:
            data = archive.read(f'Excel/{key}.xlsx')
            workbook = load_workbook(BytesIO(data))
            cached = load_workbook(BytesIO(data), data_only=True)
            sheet = workbook.active
            last_row = 37 if key == 'staircase_portrait' else 26
            header_row = 7 if key == 'staircase_portrait' else 6
            assert sheet.max_row == last_row
            assert sheet.max_column == len(HEADERS[key])
            assert [cell.value for cell in sheet[header_row]] == HEADERS[key]
            for row in range(header_row + 1, last_row + 1):
                assert sheet.cell(row, 1).value == f'=ROW()-{header_row}'
                assert cached.active.cell(row, 1).value == row - header_row
                assert all(cell.value is None for cell in sheet[row][1:])
            for row in sheet.iter_rows(min_row=header_row, max_row=last_row):
                for cell in row:
                    assert all(getattr(getattr(cell.border, side), 'style', None)
                               for side in ('left', 'right', 'top', 'bottom')), cell.coordinate
            assert sheet.page_setup.paperSize == int(sheet.PAPERSIZE_A4)
            assert sheet.page_setup.orientation == ('portrait' if 'portrait' in key else 'landscape')
            assert sheet.page_setup.fitToWidth == sheet.page_setup.fitToHeight == 1
            assert sheet.sheet_properties.pageSetUpPr.fitToPage
            workbook.close()
            cached.close()
            pdf_data = archive.read(f'PDF/{key}.pdf')
            assert pdf_data == (SOURCE / f'{key}.pdf').read_bytes()
            with pypdfium2.PdfDocument(pdf_data) as pdf:
                assert len(pdf) == 1
                page = pdf[0]
                width, height = page.get_size()
                assert (width < height) == ('portrait' in key)
                assert sorted((width, height)) == pytest.approx((595.28, 841.89), abs=.1)
                text = page.get_textpage().get_text_range()
                title = key.removesuffix('_portrait').replace('_', ' ').title()
                assert title in text
                for header in HEADERS[key]:
                    assert header in text
                assert re.search(rf'(?m)^{last_row - header_row}\r?$', text)
                pixels = page.render(scale=1).to_pil().convert('L')
                dark_pixels = sum(pixels.histogram()[:128])
                assert .01 < dark_pixels / (pixels.width * pixels.height) < .25


@pytest.mark.parametrize('existing', [False, True])
def test_rejects_excel_repair_trigger_before_writing_archive(tmp_path, existing):
    source = tmp_path / 'source'
    shutil.copytree(SOURCE, source)
    path = source / 'plantar.xlsx'
    with ZipFile(path) as workbook:
        parts = [(info, workbook.read(info)) for info in workbook.infolist()]
    with ZipFile(path, 'w') as workbook:
        for info, data in parts:
            if info.filename == 'xl/worksheets/sheet1.xml':
                options = re.search(rb'<printOptions\b[^>]*/>', data).group()
                data = data.replace(options, b'')
                setup = re.search(rb'<pageSetup\b[^>]*/>', data).group()
                data = data.replace(setup, setup + options)
            workbook.writestr(info, data)
    output = tmp_path / 'forms.zip'
    if existing:
        output.write_bytes(b'previous archive')
    with pytest.raises(ValueError, match='plantar.xlsx: invalid print settings order'):
        forms.make_archive(output, source, README)
    if existing:
        assert output.read_bytes() == b'previous archive'
    else:
        assert not output.exists()


@pytest.mark.parametrize('damage', ['empty_zip', 'missing_workbook', 'missing_sheet',
                                  'no_cells', 'empty_cells'])
def test_rejects_missing_or_blank_workbook_before_replacing_archive(tmp_path, damage):
    source = tmp_path / 'source'
    shutil.copytree(SOURCE, source)
    path = source / 'plantar.xlsx'
    with ZipFile(path) as workbook:
        parts = [(info, workbook.read(info)) for info in workbook.infolist()]
    with ZipFile(path, 'w') as workbook:
        for info, data in parts:
            if damage == 'empty_zip':
                continue
            if damage == 'missing_workbook' and info.filename == 'xl/workbook.xml':
                continue
            if info.filename == 'xl/worksheets/sheet1.xml':
                if damage == 'missing_sheet':
                    continue
                sheet = ElementTree.fromstring(data)
                grid = sheet.find(forms.SHEET_NS + 'sheetData')
                if damage == 'no_cells':
                    grid.clear()
                elif damage == 'empty_cells':
                    for cell in grid.iter(forms.SHEET_NS + 'c'):
                        cell.clear()
                data = ElementTree.tostring(sheet)
            workbook.writestr(info, data)
    output = tmp_path / 'forms.zip'
    output.write_bytes(b'previous archive')
    with pytest.raises(ValueError, match='plantar.xlsx: (missing|blank)'):
        forms.make_archive(output, source, README)
    assert output.read_bytes() == b'previous archive'
