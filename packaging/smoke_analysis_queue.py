"""Real OCR smoke: two differently sized forms share one queued template."""
import json
import os
from pathlib import Path
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['TABLESCAN_DATA_DIR'] = tempfile.mkdtemp(prefix='tablescan-queue-smoke-')

from tablescan_local.domain import TableTemplate
from tablescan_local.ui import create_application

root = Path(__file__).resolve().parents[1]
template = TableTemplate.from_dict(json.loads((root/'src/tablescan_local/default_templates/plantar.json').read_text(encoding='utf-8')))
app, window = create_application()
files = [str(root/'examples/lab_forms_excel/fixtures'/f'plantar_{rats}.pdf') for rats in (5, 10)]
window.enqueue_files(files, template, high_accuracy=False)
deadline = time.monotonic() + 240
while time.monotonic() < deadline:
    app.processEvents()
    if all(e['status'] in ('ready', 'review', 'failed') for e in window.analysis_queue.entries) and window.analysis_queue.worker is None:
        break
    time.sleep(.02)
else:
    window.analysis_queue.shutdown()
    while window.analysis_queue.worker is not None:
        app.processEvents(); time.sleep(.02)
    raise RuntimeError('Queue smoke timed out')
for entry, rats in zip(window.analysis_queue.entries, (5, 10), strict=True):
    assert entry['status'] in ('ready','review'), entry
    result = window.store.load_job(entry['job_id'])[1]
    assert result.template.rows == rats + 1
    assert [result.pages[0].cell(row,0).final_text for row in range(1,rats+1)] == [str(n) for n in range(1,rats+1)]
    window.open_recent(entry['job_id'], prefer_result=True)
    assert window.result.template.rows == rats + 1
    print(f'{Path(entry["source_path"]).name}: {rats} IDs, auto-fit, saved result and Review verified', flush=True)
print('Queue data:', os.environ['TABLESCAN_DATA_DIR'], flush=True)
window.close()
