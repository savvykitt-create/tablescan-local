import json
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QObject, Signal

from tablescan_local.analysis_queue import AnalysisQueue
from tablescan_local.domain import TableTemplate, NormalizedRect, JobResult, PageResult, CellResult
from tablescan_local.storage import LocalStore


def template():
    t = TableTemplate('queue-test', 'Queue test', NormalizedRect(.1,.1,.8,.8), [.1,.5,.9], [.1,.5,.9])
    t.ensure_column_rules()
    return t


class FakeWorker(QObject):
    progress = Signal(int, int, object)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()
    def __init__(self):
        super().__init__(); self.started = False; self.interrupted = False
    def start(self): self.started = True
    def requestInterruption(self): self.interrupted = True


def add_job(store, tmp_path, name):
    path = tmp_path / name
    cv2.imwrite(str(path), np.full((100,100,3),255,np.uint8))
    job_id, copied = store.import_source(path)
    return job_id, str(path), str(copied)


def test_serial_queue_snapshots_and_routes_results(qtbot, tmp_path):
    store = LocalStore(tmp_path/'store'); workers=[]
    def factory(entry):
        worker=FakeWorker();workers.append(worker);return worker
    queue=AnalysisQueue(store,factory)
    a=add_job(store,tmp_path,'a.png');b=add_job(store,tmp_path,'b.png');t=template()
    queue.enqueue(*a,t);queue.enqueue(*b,t)
    t.name='changed after enqueue'
    assert len(workers)==1 and queue.entries[1]['template']['name']=='Queue test'
    assert not queue.enqueue(*a,t)
    workers[0].completed.emit(JobResult(a[1],template(),[PageResult(0,a[1],[],[])]))
    assert len(workers)==1  # Completion alone is not permission to overlap threads.
    workers[0].finished.emit()
    qtbot.waitUntil(lambda:len(workers)==2)
    assert store.load_job(a[0])[1] is not None and store.load_job(b[0])[1] is None
    workers[1].failed.emit('bad scan');workers[1].finished.emit()
    assert queue.entries[1]['status']=='failed'
    queue.retry(b[0]);assert len(workers)==3
    queue.cancel(b[0]);assert workers[2].interrupted
    workers[2].cancelled.emit();workers[2].finished.emit()
    assert queue.entries[1]['status']=='cancelled'


def test_crash_restart_marks_active_interrupted_and_keeps_waiting_job(qtbot,tmp_path):
    store=LocalStore(tmp_path/'store');queue=AnalysisQueue(store,lambda _:FakeWorker())
    a=add_job(store,tmp_path,'a.png');b=add_job(store,tmp_path,'b.png')
    queue.enqueue(*a,template());queue.enqueue(*b,template())
    restored=AnalysisQueue(store,lambda _:FakeWorker())
    assert [e['status'] for e in restored.entries]==['interrupted','queued']
    qtbot.waitUntil(lambda:restored.entries[1]['status']=='running')
    restored.shutdown()


@pytest.mark.parametrize('resume_before_terminal', [True, False])
def test_cancel_all_hides_rows_and_resume_all_keeps_results_and_serial_order(qtbot, tmp_path, resume_before_terminal):
    from tablescan_local.queue_dialog import AnalysisQueueDialog
    store = LocalStore(tmp_path / 'store'); workers = []
    def factory(_):
        worker = FakeWorker(); workers.append(worker); return worker
    queue = AnalysisQueue(store, factory)
    dialog = AnalysisQueueDialog(queue); qtbot.addWidget(dialog)
    complete = add_job(store, tmp_path, 'complete.png')
    a = add_job(store, tmp_path, 'a.png'); b = add_job(store, tmp_path, 'b.png')
    queue.enqueue(*complete, template())
    workers[0].completed.emit(JobResult(complete[1], template(), [PageResult(0, complete[1], [], [])]))
    workers[0].finished.emit()
    queue.enqueue(*a, template()); queue.enqueue(*b, template())
    dialog.cancel_all_button.click()
    assert workers[1].interrupted and queue.entries[2]['status'] == 'cancelled'
    assert dialog.table.rowCount() == 2  # Ready result and still-stopping worker.
    if resume_before_terminal:
        dialog.resume_all_button.click()
    workers[1].cancelled.emit()
    if not resume_before_terminal:
        assert dialog.table.rowCount() == 1
        dialog.resume_all_button.click()
    assert len(workers) == 2
    workers[1].finished.emit()
    qtbot.waitUntil(lambda: len(workers) == 3)
    assert queue.active['job_id'] == a[0]
    workers[2].completed.emit(JobResult(a[1], template(), [PageResult(0, a[1], [], [])]))
    workers[2].finished.emit()
    qtbot.waitUntil(lambda: len(workers) == 4)
    assert queue.active['job_id'] == b[0]
    workers[3].completed.emit(JobResult(b[1], template(), [PageResult(0, b[1], [], [])]))
    workers[3].finished.emit()
    assert all(e['status'] == 'ready' for e in queue.entries)
    assert store.load_job(complete[0])[1] is not None
    assert not dialog.resume_all_button.isEnabled()


def test_shutdown_stops_every_job_and_restart_waits_for_resume(qtbot, tmp_path):
    store = LocalStore(tmp_path / 'store'); worker = FakeWorker()
    queue = AnalysisQueue(store, lambda _: worker)
    a = add_job(store, tmp_path, 'a.png'); b = add_job(store, tmp_path, 'b.png')
    queue.enqueue(*a, template()); queue.enqueue(*b, template())
    queue.shutdown()
    worker.cancelled.emit(); worker.finished.emit()
    assert [e['status'] for e in queue.entries] == ['interrupted', 'interrupted']
    restored = AnalysisQueue(store, lambda _: FakeWorker())
    qtbot.wait(20)
    assert restored.worker is None
    restored.resume_all()
    assert restored.active['job_id'] == a[0] and restored.entries[1]['status'] == 'queued'
    restored.shutdown()


def test_remove_running_job_stops_it_and_ignores_late_completion(qtbot, tmp_path):
    from tablescan_local.queue_dialog import AnalysisQueueDialog
    store = LocalStore(tmp_path/'store'); workers = []
    def factory(_):
        worker = FakeWorker(); workers.append(worker); return worker
    queue = AnalysisQueue(store, factory)
    a = add_job(store, tmp_path, 'a.png'); b = add_job(store, tmp_path, 'b.png')
    queue.enqueue(*a, template()); queue.enqueue(*b, template())
    dialog = AnalysisQueueDialog(queue); qtbot.addWidget(dialog)
    dialog.table.cellWidget(0, 6).click()
    qtbot.waitUntil(lambda: workers[0].interrupted)
    assert len(queue.entries) == 1 and dialog.table.rowCount() == 1
    workers[0].completed.emit(JobResult(a[1], template(), [PageResult(0, a[1], [], [])]))
    assert store.load_job(a[0])[1] is None and len(workers) == 1
    workers[0].finished.emit()
    qtbot.waitUntil(lambda: len(workers) == 2)
    assert queue.active['job_id'] == b[0]
    workers[1].completed.emit(JobResult(b[1], template(), [PageResult(0, b[1], [], [])]))
    workers[1].finished.emit()
    queue.exported(b[0]); assert queue.entries[0]['status'] == 'exported'
    assert dialog.table.item(0, 2).background().color().name() == '#ede9fe'
    queue.remove(b[0])
    assert not queue.entries and not json.loads(queue.path.read_text())
    assert store.load_job(b[0])[1] is not None
    queue.resume_all(); assert queue.worker is None


@pytest.mark.parametrize('quit_event', [False, True])
def test_close_automatically_cancels_and_exits_without_second_close(qtbot, tmp_path, monkeypatch, quit_event):
    from PySide6.QtCore import QEvent
    from tablescan_local import ui
    monkeypatch.setenv('TABLESCAN_DATA_DIR', str(tmp_path / 'app'))
    app, window = ui.create_application(); qtbot.addWidget(window); window.show()
    started = threading.Event(); calls = []
    def process(images, path, t, report, *args, **kwargs):
        calls.append(path); started.set()
        while True:
            report(1, 10, 'working')
            threading.Event().wait(.01)
    monkeypatch.setattr(ui, 'process_document', process)
    notices = []
    monkeypatch.setattr(ui.QMessageBox, 'information', lambda *a: notices.append(a))
    paths = []
    for name in ('a.png', 'b.png'):
        path = tmp_path / name; cv2.imwrite(str(path), np.full((100,100,3),255,np.uint8)); paths.append(str(path))
    window.enqueue_files(paths, template())
    assert started.wait(2)
    if quit_event:
        app.sendEvent(app, QEvent(QEvent.Type.Quit))
    else:
        window.close()
    qtbot.waitUntil(lambda: window.analysis_queue.worker is None and not window.isVisible(), timeout=5000)
    assert len(calls) == 1 and not notices
    assert all(e['status'] == 'interrupted' for e in window.analysis_queue.entries)
    assert not window.queue_dialog.isVisible()


def test_close_before_batch_worker_starts_does_not_start_it_later(qtbot, tmp_path, monkeypatch):
    from tablescan_local import ui, batch_preflight
    from tablescan_local.batch_dialog import BatchPreparationDialog
    monkeypatch.setenv('TABLESCAN_DATA_DIR', str(tmp_path / 'app'))
    _, window = ui.create_application(); qtbot.addWidget(window); window.show()
    calls = []
    monkeypatch.setattr(batch_preflight, 'prepare_file', lambda *args: calls.append(args))
    dialog = BatchPreparationDialog(['not-read-a.png', 'not-read-b.png'], [template()],
                                    lambda: ui.TablePage(mode='template'), window)
    dialog.show()
    window.close()  # Before the queued start timer is delivered.
    qtbot.waitUntil(lambda: dialog.worker is None and not window.isVisible(), timeout=3000)
    assert not calls


def test_second_application_cannot_open_the_same_queue(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QLockFile
    from tablescan_local import ui
    root = tmp_path / 'app'; root.mkdir()
    monkeypatch.setenv('TABLESCAN_DATA_DIR', str(root))
    lock = QLockFile(str(root / 'instance.lock')); lock.setStaleLockTime(0)
    assert lock.tryLock(0)
    try:
        with pytest.raises(ui.InstanceAlreadyRunning):
            ui.create_application(lock_store=True)
        assert not (root / 'analysis-queue.json').exists()
    finally:
        lock.unlock()


def test_complete_does_not_replace_current_document_and_each_review_opens(qtbot,tmp_path,monkeypatch):
    from tablescan_local import ui
    monkeypatch.setenv('TABLESCAN_DATA_DIR',str(tmp_path/'app'))
    _,window=ui.create_application();qtbot.addWidget(window)
    release=threading.Event();started=threading.Event();calls=[]
    def process(images,path,t,*args,**kwargs):
        calls.append(Path(path).name)
        if len(calls)==1:
            started.set();release.wait(5)
        return JobResult(path,t,[PageResult(0,path,[CellResult(1,1,Path(path).name,Path(path).name,1)],[])])
    monkeypatch.setattr(ui,'process_document',process)
    paths=[]
    for name in ('a.png','b.png'):
        path=tmp_path/name;cv2.imwrite(str(path),np.full((100,100,3),255,np.uint8));paths.append(str(path))
    window.open_source(paths[0],template());a=window.job_id
    window.start_recognition(window.template)
    assert started.wait(2)
    window.open_source(paths[1],template());b=window.job_id
    window.start_recognition(window.template)
    assert a!=b and len(calls)==1 and not window.queue_dialog.isModal()
    release.set()
    qtbot.waitUntil(lambda: all(e['status']=='ready' for e in window.analysis_queue.entries),timeout=8000)
    qtbot.waitUntil(lambda:window.analysis_queue.worker is None)
    assert window.job_id==b and window.result is None
    assert calls==['a.png','b.png']
    window.queue_dialog.table.selectRow(0)
    window.queue_dialog.review_button.click()
    qtbot.waitUntil(lambda:window.job_id==a)
    assert window.result.pages[0].cells[0].final_text=='a.png'
    window.show_analysis_queue()
    window.queue_dialog.table.selectRow(1)
    window.queue_dialog.review_button.click()
    qtbot.waitUntil(lambda:window.job_id==b)
    assert window.result.pages[0].cells[0].final_text=='b.png'


def test_batch_failure_does_not_block_next_file(qtbot,tmp_path,monkeypatch):
    from tablescan_local import ui
    monkeypatch.setenv('TABLESCAN_DATA_DIR',str(tmp_path/'app'))
    _,window=ui.create_application();qtbot.addWidget(window)
    bad=tmp_path/'bad.png';bad.write_bytes(b'broken')
    good=tmp_path/'good.png';cv2.imwrite(str(good),np.full((100,100,3),255,np.uint8))
    monkeypatch.setattr(ui,'process_document',lambda images,path,t,*a,**k:JobResult(path,t,[PageResult(0,path,[],[])]))
    window.enqueue_files([str(bad),str(good)],template())
    qtbot.waitUntil(lambda:window.analysis_queue.entries[-1]['status']=='ready',timeout=5000)
    qtbot.waitUntil(lambda:window.analysis_queue.worker is None)
    assert [e['status'] for e in window.analysis_queue.entries]==['failed','ready']
    assert window.job_id==''
