"""Package printable workbooks, PDFs and importable OCR templates."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
from tablescan_local import __version__

FORM_KEYS = ('von_frey', 'plantar', 'staircase', 'staircase_portrait')
SHEET_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
# These worksheet children form an ordered sequence in SpreadsheetML.
# Excel discards the sheet when printOptions follows pageSetup, even though
# permissive readers (including openpyxl and LibreOffice) accept that file.
PRINT_ELEMENTS = tuple(SHEET_NS + name for name in
                       ('printOptions', 'pageMargins', 'pageSetup', 'headerFooter'))


def validate_workbook(path: Path) -> None:
    with ZipFile(path) as workbook:
        damaged = workbook.testzip()
        if damaged:
            raise ValueError(f'{path.name}: damaged ZIP member {damaged}')
        for name in workbook.namelist():
            if not (name.startswith('xl/worksheets/') and name.endswith('.xml')):
                continue
            sheet = ElementTree.fromstring(workbook.read(name))
            if sheet.tag != SHEET_NS + 'worksheet':
                continue
            order = [PRINT_ELEMENTS.index(child.tag) for child in sheet
                     if child.tag in PRINT_ELEMENTS]
            if order != sorted(set(order)):
                raise ValueError(f'{path.name}: invalid print settings order in {name}')


def make_archive(output: Path, source: Path, readme: Path) -> Path:
    # Validate before opening the destination so a failed check cannot replace
    # an existing release archive with an invalid or partial one.
    for key in FORM_KEYS:
        validate_workbook(source / f'{key}.xlsx')
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for key in FORM_KEYS:
            archive.write(source / f'{key}.xlsx', f'Excel/{key}.xlsx')
            archive.write(source / f'{key}.pdf', f'PDF/{key}.pdf')
            archive.write(source / f'{key}.json', f'templates/{key}.json')
        archive.write(readme, 'README.md')
    return output


if __name__ == '__main__':
    print(make_archive(root / 'release' / f'TableScan-Forms-{__version__}.zip',
                       root / 'src/tablescan_local/default_templates',
                       root / 'examples/lab_forms_excel/README.md'))
