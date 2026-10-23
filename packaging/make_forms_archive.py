"""Package printable workbooks, PDFs and importable OCR templates."""
from pathlib import Path
import posixpath
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
from tablescan_local import __version__

FORM_KEYS = ('von_frey', 'plantar', 'staircase', 'staircase_portrait')
SHEET_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
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
        names = workbook.namelist()
        required = {'[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml',
                    'xl/_rels/workbook.xml.rels'}
        if not required.issubset(names) or len(names) != len(set(names)):
            raise ValueError(f'{path.name}: missing or duplicate workbook parts')
        document = ElementTree.fromstring(workbook.read('xl/workbook.xml'))
        sheets = document.findall(f'{SHEET_NS}sheets/{SHEET_NS}sheet')
        if document.tag != SHEET_NS + 'workbook' or not sheets:
            raise ValueError(f'{path.name}: missing worksheets')
        relationships = {rel.get('Id'): rel for rel in ElementTree.fromstring(
            workbook.read('xl/_rels/workbook.xml.rels'))}
        for entry in sheets:
            relation = relationships.get(entry.get(REL_NS + 'id'))
            if (relation is None or relation.get('TargetMode') == 'External'
                    or not relation.get('Type', '').endswith('/worksheet')):
                raise ValueError(f'{path.name}: missing worksheet relationship')
            target = relation.get('Target', '')
            name = (target.lstrip('/') if target.startswith('/') else
                    posixpath.normpath(posixpath.join('xl', target)))
            if name not in names:
                raise ValueError(f'{path.name}: missing worksheet {name}')
            sheet = ElementTree.fromstring(workbook.read(name))
            cells = sheet.findall(f'{SHEET_NS}sheetData/{SHEET_NS}row/{SHEET_NS}c')
            if sheet.tag != SHEET_NS + 'worksheet' or not any(
                    node.tag in {SHEET_NS + 'f', SHEET_NS + 'v', SHEET_NS + 't'}
                    and (node.text or '').strip() for cell in cells for node in cell.iter()):
                raise ValueError(f'{path.name}: blank worksheet {name}')
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
