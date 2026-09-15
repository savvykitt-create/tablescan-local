"""Serial background OCR with durable, per-document settings snapshots."""
import json
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .domain import TableTemplate
from .i18n import tr


class AnalysisQueue(QObject):
    changed = Signal()
    resultSaved = Signal(str, object)

    def __init__(self, store, worker_factory, parent=None):
        super().__init__(parent)
        self.store = store
        self.worker_factory = worker_factory
        self.path = store.root / 'analysis-queue.json'
        self.entries = []
        self.worker = None
        self.active = None
        self.closing = False
        self._resume_after_stop = False
        if self.path.exists():
            self.entries = json.loads(self.path.read_text(encoding='utf-8'))
            for entry in self.entries:
                if entry['status'] in ('running', 'cancelling'):
                    entry['status'] = 'interrupted'
                if entry['status'] in {'review', 'ready', 'exported'} and 'slow_diagnostics' not in entry:
                    saved = self.store.load_job(entry['job_id'])
                    if saved and saved[1]:
                        entry['slow_diagnostics'] = [page.slow_mode for page in saved[1].pages]
        QTimer.singleShot(0, self.start_next)

    def persist(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def busy(self, job_id):
        return any(e['job_id'] == job_id and e['status'] in ('queued', 'running', 'cancelling') for e in self.entries)

    def enqueue(self, job_id, source_path, stored_path, template, high_accuracy=True, slow_mode=False, *, preparation=None):
        if self.busy(job_id):
            return False
        self.closing = False
        snapshot = TableTemplate.from_dict(template.to_dict())
        snapshot.validate_value_rules()
        entry = dict(job_id=job_id, source_path=source_path, stored_path=str(stored_path),
                     template=snapshot.to_dict(), high_accuracy=high_accuracy, slow_mode=slow_mode,
                     status='queued', progress=0, message='', preparation=dict(preparation or {}))
        previous = self.entries
        self.entries = [e for e in previous if e['job_id'] != job_id] + [entry]
        try:
            self.persist()
        except OSError:
            self.entries = previous
            raise
        self.changed.emit()
        self.start_next()
        return True

    def start_next(self):
        if self.closing or self.worker is not None:
            return
        entry = next((e for e in self.entries if e['status'] == 'queued'), None)
        if entry is None:
            return
        try:
            worker = self.worker_factory(entry)
            entry.pop('slow_diagnostics', None)
            entry['status'] = 'running'
            self.persist()
        except Exception as exc:
            entry.update(status='failed', message=str(exc))
            self.changed.emit()
            QTimer.singleShot(0, self.start_next)
            return
        self.active, self.worker = entry, worker
        worker.progress.connect(self._progress)
        worker.completed.connect(self._completed)
        worker.failed.connect(lambda message: self._terminal('failed', message))
        worker.cancelled.connect(self._cancelled)
        worker.finished.connect(self._finished)
        self.changed.emit()
        worker.start()

    def _progress(self, value, maximum, message):
        percent = round(100 * value / max(1, maximum))
        changed = self.active['progress'] != percent
        self.active.update(progress=percent, message=str(message))
        if changed:
            self.changed.emit()

    def _completed(self, result):
        if self.active.get('_removed'):
            return
        try:
            self.store.save_result(self.active['job_id'], result)
            draft = self.store.load_draft(self.active['job_id'])
            if draft and draft.to_dict() == self.active['template']:
                (self.store.jobs_dir / self.active['job_id'] / 'working-template.json').unlink(missing_ok=True)
        except Exception as exc:
            # Keep the complete OCR result recoverable if the database write fails.
            fallback = self.store.jobs_dir / self.active['job_id'] / 'unsaved-analysis.json'
            try:
                fallback.write_text(json.dumps(result.to_dict(), ensure_ascii=False), encoding='utf-8')
            except OSError:
                pass
            self._terminal('failed', str(exc))
            return
        self.active['progress'] = 100
        preparation = getattr(self.worker, 'preparation', None)
        if preparation:
            self.active.update(template=result.template.to_dict(), preparation=preparation)
        self.active['slow_diagnostics'] = [page.slow_mode for page in result.pages]
        incomplete = any(page.slow_mode.get('status') in {'failed', 'partial'} for page in result.pages)
        message = str(tr('Для части ячеек slow mode не завершён. Основные результаты сохранены; спорные значения требуют проверки.')) if incomplete else ''
        self._terminal('review' if result.unresolved_count else 'ready', message)
        self.resultSaved.emit(self.active['job_id'], result)

    def _terminal(self, status, message=''):
        self.active.update(status=status, message=message)
        try:
            self.persist()
        except OSError as exc:
            self.active['message'] = str(exc)
        self.changed.emit()

    def _cancelled(self):
        if self.closing:
            self._terminal('interrupted')
        elif self._resume_after_stop:
            self.active['progress'] = 0
            self._terminal('queued')
        else:
            self._terminal('cancelled')
        self._resume_after_stop = False

    def _finished(self):
        if self._resume_after_stop and not self.closing and self.active['status'] in ('cancelled', 'interrupted', 'failed'):
            self.active['progress'] = 0
            self._terminal('queued')
        if self.active['status'] in ('running', 'cancelling'):
            self._terminal('interrupted')
        self.worker = self.active = None
        self._resume_after_stop = False
        self.changed.emit()
        QTimer.singleShot(0, self.start_next)

    def cancel(self, job_id):
        entry = next((e for e in self.entries if e['job_id'] == job_id), None)
        if entry is self.active and self.worker:
            self._resume_after_stop = False
            entry['status'] = 'cancelling'
            self.worker.requestInterruption()
        elif entry and entry['status'] == 'queued':
            entry['status'] = 'cancelled'
        self.persist()
        self.changed.emit()

    def remove(self, job_id):
        """Remove queue membership; retain saved results in file history."""
        entry = next((e for e in self.entries if e['job_id'] == job_id), None)
        if entry is None:
            return
        previous = self.entries
        self.entries = [e for e in previous if e is not entry]
        try:
            self.persist()
        except OSError:
            self.entries = previous
            raise
        if entry is self.active and self.worker:
            entry['_removed'] = True
            entry['status'] = 'cancelling'
            self._resume_after_stop = False
            self.worker.requestInterruption()
        self.changed.emit()

    def exported(self, job_id):
        for entry in self.entries:
            if entry['job_id'] == job_id and entry['status'] in ('ready', 'exported'):
                entry['status'] = 'exported'
                self.persist()
                self.changed.emit()

    def cancel_all(self):
        """Cancel running/waiting work while retaining snapshots for Resume all."""
        self._resume_after_stop = False
        for entry in self.entries:
            if entry['status'] == 'queued':
                entry['status'] = 'cancelled'
            elif entry is self.active and entry['status'] in ('running', 'cancelling'):
                entry['status'] = 'cancelling'
                self.worker.requestInterruption()
        self.persist()
        self.changed.emit()

    def resume_all(self):
        if self.closing:
            return
        for entry in self.entries:
            if entry is self.active:
                if entry['status'] in ('cancelling', 'cancelled', 'interrupted', 'failed'):
                    # Wait for the interrupted thread to finish before retrying it.
                    self._resume_after_stop = True
            elif entry['status'] in ('cancelled', 'interrupted', 'failed'):
                entry.update(status='queued', progress=0, message='')
        self.persist()
        self.changed.emit()
        self.start_next()

    def retry(self, job_id):
        entry = next((e for e in self.entries if e['job_id'] == job_id), None)
        if entry and entry['status'] in ('failed', 'cancelled', 'interrupted'):
            self.closing = False
            entry.update(status='queued', message='', progress=0)
            self.persist()
            self.changed.emit()
            self.start_next()

    def shutdown(self):
        self.closing = True
        self._resume_after_stop = False
        for entry in self.entries:
            if entry['status'] == 'queued':
                entry['status'] = 'interrupted'
            elif entry is self.active and entry['status'] in ('running', 'cancelling'):
                entry['status'] = 'cancelling'
        if self.worker:
            self.worker.requestInterruption()
        self.persist()
        self.changed.emit()

    def review_updated(self, job_id, result):
        for entry in self.entries:
            if entry['job_id'] == job_id and entry['status'] in ('review', 'ready', 'exported'):
                entry['status'] = 'review' if result.unresolved_count else 'ready'
                self.persist()
                self.changed.emit()
