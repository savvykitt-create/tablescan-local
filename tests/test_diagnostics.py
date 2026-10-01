import json
from datetime import datetime

from tablescan_local.analysis_queue import AnalysisQueue
from tablescan_local.diagnostics_dialog import DiagnosticsDialog, duration_text
from tablescan_local.domain import JobResult, PageResult
from tablescan_local.storage import LocalStore
from test_analysis_queue import FakeWorker, add_job, template


def test_queue_times_use_monotonic_clock_and_survive_restart(qtbot, tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('tablescan_local.analysis_queue.time.monotonic', lambda: clock[0])
    store = LocalStore(tmp_path / 'store')
    worker = FakeWorker()
    queue = AnalysisQueue(store, lambda _: worker)
    job = add_job(store, tmp_path, 'scan.png')
    queue.enqueue(*job, template())
    entry = queue.active
    assert datetime.fromisoformat(entry['started_at']).tzinfo is not None
    assert entry.get('finished_at') is None
    clock[0] += 65.4
    queue._save_elapsed()
    assert queue.elapsed(entry) == 65.4
    worker.completed.emit(JobResult(job[1], template(), [PageResult(0, job[1], [], [])]))
    worker.finished.emit()
    assert entry['elapsed_seconds'] == 65.4
    assert datetime.fromisoformat(entry['finished_at']).tzinfo is not None
    clock[0] += 100
    assert queue.elapsed(entry) == 65.4
    restored = AnalysisQueue(store, lambda _: FakeWorker())
    assert restored.entries[0]['elapsed_seconds'] == 65.4
    assert duration_text(65.4) == '00:01:05'


def test_crash_duration_is_lower_bound_and_retry_resets_attempt(qtbot, tmp_path, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr('tablescan_local.analysis_queue.time.monotonic', lambda: clock[0])
    store = LocalStore(tmp_path / 'store')
    queue = AnalysisQueue(store, lambda _: FakeWorker())
    job = add_job(store, tmp_path, 'scan.png')
    queue.enqueue(*job, template())
    clock[0] = 20
    queue._save_elapsed()
    restored = AnalysisQueue(store, lambda _: FakeWorker())
    entry = restored.entries[0]
    assert entry['status'] == 'interrupted' and entry['timing_incomplete']
    assert not entry.get('finished_at')
    assert duration_text(restored.elapsed(entry), True) == '≥ 00:00:10'
    restored.retry(job[0])
    assert not entry.get('timing_incomplete') and entry['elapsed_seconds'] == 0
    clock[0] += 3
    restored.worker.cancelled.emit()
    restored.worker.finished.emit()
    assert entry['elapsed_seconds'] == 3 and entry['finished_at']


def test_waiting_jobs_and_legacy_entries_have_no_invented_times(qtbot, tmp_path):
    store = LocalStore(tmp_path / 'store')
    queue = AnalysisQueue(store, lambda _: FakeWorker())
    a = add_job(store, tmp_path, 'a.png'); b = add_job(store, tmp_path, 'b.png')
    queue.enqueue(*a, template()); queue.enqueue(*b, template())
    assert queue.entries[1].get('started_at') is None
    queue.cancel(b[0])
    assert queue.entries[1].get('finished_at') is None
    assert queue.elapsed(queue.entries[1]) is None
    assert duration_text(None) == '—'


def test_diagnostics_shows_saved_devices_and_exports_no_cell_contents(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr('tablescan_local.slow_mode.runtime_config', lambda: {'backend':'mlx', 'device':'auto'})
    store = LocalStore(tmp_path / 'store'); worker = FakeWorker()
    queue = AnalysisQueue(store, lambda _: worker)
    job = add_job(store, tmp_path, 'scan.png')
    queue.enqueue(*job, template())
    slow = {'status':'complete','backend':'mlx','execution':{'qwen':{'device':'metal','backend':'mlx','model_reused':True,'load_seconds':0.0,'request_seconds':2.5}}}
    result = JobResult(job[1], template(), [PageResult(0, job[1], [], [], slow_mode=slow)],
                       performance={'cpu_workers':2,'cpu_parallel_cells':48,'primary_pages_seconds':[4.5]})
    worker.completed.emit(result); worker.finished.emit()
    dialog = DiagnosticsDialog(queue, job_id=job[0]); qtbot.addWidget(dialog)
    report = json.loads(dialog.report_json())
    assert report['analysis']['performance']['cpu_parallel_cells'] == 48
    assert report['analysis']['slow_execution'][0]['execution']['qwen']['device'] == 'metal'
    assert str(tmp_path) not in dialog.report_json()
    dialog.copy_button.click()
    from PySide6.QtWidgets import QApplication
    assert json.loads(QApplication.clipboard().text()) == report
    from tablescan_local.queue_dialog import AnalysisQueueDialog
    qdialog = AnalysisQueueDialog(queue); qtbot.addWidget(qdialog)
    assert qdialog.table.item(0, 6).text() != '—'
    assert qdialog.table.item(0, 7).text() != '—'
    qdialog.table.selectRow(0)
    with qtbot.waitSignal(qdialog.diagnosticsRequested) as signal:
        qdialog.diagnostics_button.click()
    assert signal.args == [job[0]]
