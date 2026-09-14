"""Package the three printable workbooks and importable OCR templates."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
from tablescan_local import __version__

output = root / 'release' / f'TableScan-Forms-{__version__}.zip'
output.parent.mkdir(exist_ok=True)
source = root / 'src/tablescan_local/default_templates'
with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
    for key in ('von_frey', 'plantar', 'staircase'):
        archive.write(source / f'{key}.xlsx', f'Excel/{key}.xlsx')
        archive.write(source / f'{key}.json', f'templates/{key}.json')
    archive.write(root / 'examples/lab_forms_excel/README_RU.md', 'README_RU.md')
print(output)
