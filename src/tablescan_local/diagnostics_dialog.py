"""Local, read-only resource and per-analysis diagnostics."""
import json
import platform
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QVBoxLayout,
                              QTreeWidget, QTreeWidgetItem, QMessageBox)
from .localized_widgets import QDialog, QLabel, QPushButton, QComboBox
from .i18n import tr
from .resource_policy import resources, GIB
from . import __version__


def duration_text(seconds, incomplete=False):
    if seconds is None:
        return '—'
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return ('≥ ' if incomplete else '') + f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def local_timestamp(value):
    if not value:
        return '—'
    from datetime import datetime
    try:
        return datetime.fromisoformat(value).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return '—'


class DiagnosticsDialog(QDialog):
    def __init__(self, queue, parent=None, job_id=None):
        super().__init__(parent)
        self.queue = queue
        self.report = {}
        self._saved_key = None
        self._saved = None
        self.setWindowTitle(tr('Diagnostics'))
        self.resize(850, 650)
        layout = QVBoxLayout(self)
        note = QLabel(tr('Current resources and recorded analysis measurements. Model devices are shown only after execution.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.jobs = QComboBox()
        self.jobs.setAccessibleName(tr('Analysis'))
        layout.addWidget(self.jobs)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setRootIsDecorated(True)
        self.tree.setColumnWidth(0, 340)
        layout.addWidget(self.tree)
        actions = QHBoxLayout()
        self.copy_button = QPushButton(tr('Copy diagnostic report'))
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.report_json()))
        self.save_button = QPushButton(tr('Save diagnostic report…'))
        self.save_button.clicked.connect(self.save_report)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.save_button)
        actions.addStretch()
        close = QPushButton(tr('Close'))
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.jobs.currentIndexChanged.connect(self.refresh)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.refresh()
        if job_id:
            self.jobs.setCurrentIndex(self.jobs.findData(job_id))

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def report_json(self):
        return json.dumps(self.report, ensure_ascii=False, indent=2)

    def save_report(self):
        path, _ = QFileDialog.getSaveFileName(self, str(tr('Save diagnostic report…')), 'tablescan-diagnostics.json', 'JSON (*.json)')
        if path:
            try:
                Path(path).write_text(self.report_json(), encoding='utf-8')
            except OSError as exc:
                QMessageBox.warning(self, str(tr('Diagnostics')), str(exc))

    def refresh(self, *_):
        selected = self.jobs.currentData()
        self.jobs.blockSignals(True)
        self.jobs.clear()
        self.jobs.addItem(tr('Device only'), '')
        for entry in self.queue.entries:
            self.jobs.addItem(Path(entry['source_path']).name, entry['job_id'])
        self.jobs.setCurrentIndex(max(0, self.jobs.findData(selected)))
        self.jobs.blockSignals(False)
        state = resources()
        from .numeric_decoder import _native_search
        from .slow_mode import runtime_config
        try:
            config = runtime_config()
            runtime = {'available': True, 'backend': config['backend'], 'requested_device': config['device']}
        except RuntimeError:
            runtime = {'available': False}
        self.report = {'version': __version__, 'system': platform.system(), 'architecture': platform.machine(),
                       'current_resources': state.to_dict(), 'decoder': 'native' if _native_search else 'python',
                       'slow_runtime': runtime}
        self.tree.clear()
        self.tree.setHeaderLabels([str(tr('Measurement')), str(tr('Value'))])

        def row(label, value, parent=None):
            return QTreeWidgetItem(parent or self.tree, [str(label), str(value)])

        device = row(tr('Device'), f'{platform.system()} / {platform.machine()}')
        row(tr('Application version'), __version__, device)
        row(tr('Physical CPU cores'), state.cores, device)
        row(tr('Available / total RAM'), f'{state.available / GIB:.1f} / {state.total / GIB:.1f} GiB' if state.total else '—', device)
        row(tr('Memory reserve'), f'{state.reserve / GIB:.1f} GiB', device)
        row(tr('Numeric decoder'), self.report['decoder'], device)
        row(tr('Configured Slow runtime'), f"{runtime['backend']} / {runtime['requested_device']}" if runtime['available'] else tr('Not installed'), device)
        entry = next((e for e in self.queue.entries if e['job_id'] == self.jobs.currentData()), None)
        if entry:
            key = (entry['job_id'], entry['status'], entry.get('finished_at'))
            if key != self._saved_key:
                self._saved_key = key
                saved = self.queue.store.load_job(entry['job_id']) if entry['status'] in {'ready', 'review', 'exported'} else None
                self._saved = saved[1] if saved else None
            performance = entry.get('performance', {}) or (self._saved.performance if self._saved else {})
            slow = entry.get('slow_diagnostics', []) or ([p.slow_mode for p in self._saved.pages] if self._saved else [])
            timing = {k: entry.get(k) for k in ('started_at', 'finished_at', 'timing_incomplete')}
            timing['elapsed_seconds'] = self.queue.elapsed(entry)
            # No source paths, cell contents or model prompts in the export.
            self.report['analysis'] = {'job_id': entry['job_id'], 'status': entry['status'],
                                       'timing': timing, 'performance': performance,
                                       'slow_execution': [{k: p[k] for k in ('status', 'backend', 'requested_device', 'execution') if k in p} for p in slow]}
            analysis = row(tr('Analysis'), Path(entry['source_path']).name)
            row(tr('Started'), local_timestamp(timing['started_at']), analysis)
            row(tr('Finished'), local_timestamp(timing['finished_at']), analysis)
            row(tr('Duration'), duration_text(timing['elapsed_seconds'], timing['timing_incomplete']), analysis)
            if performance:
                row(tr('CPU worker budget'), performance.get('cpu_workers', '—'), analysis)
                row(tr('Available OCR provider'), performance.get('ocr_provider_available', '—'), analysis)
                row(tr('Cells processed in parallel'), performance.get('cpu_parallel_cells', 0), analysis)
                row(tr('Sequential fallbacks'), performance.get('cpu_fallbacks', 0), analysis)
                row(tr('OCR total'), duration_text(performance.get('total_seconds')), analysis)
                for index, seconds in enumerate(performance.get('primary_pages_seconds', []), 1):
                    row(tr('Page {p0}: primary OCR', p0=index), f'{seconds:.2f} s', analysis)
            else:
                row(tr('Recorded measurements'), tr('Not available yet'), analysis)
            for index, page in enumerate(slow, 1):
                execution = page.get('execution', {})
                if not execution:
                    continue
                group = row(tr('Page {p0}: Slow models', p0=index), '', analysis)

                def model_rows(name, data):
                    if isinstance(data, list):
                        for i, item in enumerate(data, 1):
                            model_rows(f'{name} {i}', item)
                    elif isinstance(data, dict) and 'device' in data:
                        model = row(name, f"{data.get('backend', '')} / {data['device']}", group)
                        row(tr('Model reused'), tr('Yes') if data.get('model_reused') else tr('No'), model)
                        for key, label in [('load_seconds', tr('Model loading')), ('request_seconds', tr('Request time'))]:
                            if key in data:
                                row(label, f'{data[key]:.2f} s', model)
                    elif isinstance(data, dict):
                        for key, value in data.items():
                            model_rows(f'{name} {key}', value)
                for name, data in execution.items():
                    model_rows(name, data)
        self.tree.expandAll()
