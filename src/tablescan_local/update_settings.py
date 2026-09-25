"""User-initiated update checks and downloads; never checks at startup."""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QVBoxLayout, QProgressBar

from . import __version__, app_update
from .i18n import tr
from .localized_widgets import QWidget, QLabel, QPushButton


class UpdateWorker(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, release=None, parent=None):
        super().__init__(parent)
        self.release = release

    def run(self):
        try:
            result = (app_update.check_latest(__version__) if self.release is None else
                      app_update.prepare(self.release, self.progress.emit, self.isInterruptionRequested))
            if self.isInterruptionRequested():
                if isinstance(result, app_update.PreparedUpdate):
                    result.cleanup()
                return
            self.result.emit(result)
        except app_update.UpdateCancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateSettings(QWidget):
    installRequested = Signal(object)

    def __init__(self, parent=None, busy=lambda: False):
        super().__init__(parent)
        self.busy = busy
        self.worker = None
        self.release = None
        self.prepared = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 18, 0, 0)
        layout.addWidget(QLabel(tr('Application updates')))
        layout.addWidget(QLabel(tr('Installed version: {version}', version=__version__)))
        note = QLabel(tr('Updates come from TableScan GitHub releases. History, templates and Slow mode are kept.'))
        note.setWordWrap(True); layout.addWidget(note)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.check = QPushButton(tr('Check for updates'))
        self.check.clicked.connect(self.check_updates); layout.addWidget(self.check)
        self.action = QPushButton(tr('Download update'))
        self.action.clicked.connect(self.perform_action); self.action.hide(); layout.addWidget(self.action)
        self.progress = QProgressBar(); self.progress.hide(); layout.addWidget(self.progress)
        self.cancel = QPushButton(tr('Cancel update download'))
        self.cancel.clicked.connect(self.cancel_download); self.cancel.hide(); layout.addWidget(self.cancel)

    def check_updates(self):
        if self.worker:
            return
        if self.prepared:
            self.prepared.cleanup(); self.prepared = None
        self.release = None
        self.action.hide()
        self.status.setText(tr('Checking GitHub releases…'))
        self._start(UpdateWorker(parent=self))

    def perform_action(self):
        if self.worker:
            return
        if self.busy():
            self.status.setText(tr('Wait for analysis and other operations to finish before updating.'))
            return
        if self.prepared:
            self.installRequested.emit(self.prepared)
        elif self.release:
            self.status.setText(tr('Downloading and verifying update…'))
            self.cancel.show()
            self.cancel.setEnabled(True)
            self._start(UpdateWorker(self.release, self))

    def _start(self, worker):
        self.worker = worker
        self.check.setEnabled(False); self.action.setEnabled(False)
        self.progress.setRange(0, 0); self.progress.show()
        worker.result.connect(self.completed)
        worker.failed.connect(self.failed)
        worker.progress.connect(self.show_progress)
        worker.finished.connect(self.finished)
        worker.start()

    def show_progress(self, count, total):
        self.progress.setRange(0, 100)
        self.progress.setValue(count * 100 // max(1, total))

    def cancel_download(self):
        if self.worker:
            self.worker.requestInterruption()
            self.cancel.setEnabled(False)
            self.status.setText(tr('Cancelling update…'))

    def completed(self, result):
        if isinstance(result, app_update.PreparedUpdate):
            self.prepared = result
            self.status.setText(tr('Update verified. Install and restart to finish.'))
            self.action.setText(tr('Install and restart'))
        elif result is None:
            self.status.setText(tr('You have the latest version.'))
            return
        else:
            self.release = result
            self.status.setText(tr('Version {version} is available.', version=result.version))
            self.action.setText(tr('Download update'))
        self.action.show()

    def failed(self, error):
        self.status.setText(tr('Update failed: {error}', error=error))

    def finished(self):
        cancelled = self.worker.isInterruptionRequested()
        self.worker.deleteLater(); self.worker = None
        self.check.setEnabled(True); self.action.setEnabled(True)
        self.progress.hide(); self.cancel.hide()
        if cancelled:
            self.status.setText(tr('Update cancelled. The installed application was not changed.'))
