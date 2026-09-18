"""Optional Slow mode installation card for the application's Settings page."""
from PySide6.QtCore import QLockFile, QThread, QTimer, Signal
from PySide6.QtWidgets import QVBoxLayout, QProgressBar, QPlainTextEdit, QMessageBox

from .i18n import tr, localized_saved_message
from .localized_widgets import QWidget, QLabel, QPushButton
from .slow_runtime import runtime_root
from . import slow_setup
from .cleanup import remove_slow, prune_empty, SLOW_FILES


class SetupWorker(QThread):
    progress = Signal(str, int, int)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancellation = slow_setup.Cancellation()

    def cancel(self):
        self.cancellation.cancel()

    def run(self):
        try:
            slow_setup.install(self.progress.emit, self.cancellation)
        except slow_setup.SetupCancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))


class RemovalWorker(QThread):
    failed = Signal(str)

    def run(self):
        try:
            remove_slow(setup_locked=True)
        except Exception as exc:
            self.failed.emit(str(exc))


class SlowSettings(QWidget):
    def __init__(self, parent=None, busy=lambda: False):
        super().__init__(parent)
        self.busy = busy
        self.worker = None
        self.lock = None
        self.error = ''
        self.cancelling = False
        self.removing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.addWidget(QLabel(tr('Slow mode')))
        note = QLabel(tr('Additional local verification with Qwen and GLM. Installation needs internet and at least 15 GB of free space. CPU: 16 GB RAM minimum, 24 GB recommended. Windows and Apple Silicon macOS prepare Python and an isolated environment automatically.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.install_button = QPushButton()
        self.install_button.clicked.connect(self.start_install)
        layout.addWidget(self.install_button)
        self.remove_button = QPushButton(tr('Remove Slow mode'))
        self.remove_button.clicked.connect(self.start_remove)
        layout.addWidget(self.remove_button)
        self.cancel_button = QPushButton(tr('Cancel installation'))
        self.cancel_button.clicked.connect(self.cancel_install)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.details_button = QPushButton(tr('Show installation details'))
        self.details_button.clicked.connect(lambda: self.details.setVisible(not self.details.isVisible()))
        layout.addWidget(self.details_button)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(200)
        self.details.setMaximumHeight(160)
        self.details.hide()
        layout.addWidget(self.details)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def is_installing(self):
        return self.worker is not None

    def refresh(self):
        if self.details.isVisible():
            try:
                with (runtime_root() / 'setup.log').open('rb') as log:
                    log.seek(0, 2)
                    log.seek(max(0, log.tell() - 24000))
                    text = log.read().decode('utf-8', errors='replace')
                if self.error:
                    text += '\n' + self.error
                if text != self.details.toPlainText():
                    self.details.setPlainText(text)
                    self.details.verticalScrollBar().setValue(self.details.verticalScrollBar().maximum())
            except OSError:
                self.details.setPlainText(self.error)
        if self.worker is not None:
            return
        state = slow_setup.setup_status()
        messages = {
            'cancelled': tr('Slow mode installation cancelled. You can retry installation.'),
            'ready': tr('Slow mode is installed and ready. Select Slow analysis when recognizing a document.'),
            'missing': tr('Slow mode is not installed.'),
            'installing': tr('Slow mode is being installed by another process.'),
            'failed': tr('Slow mode setup is incomplete or needs repair. Retry installation.'),
        }
        self.status.setText(tr('Slow mode operation failed. Open details and retry.') if self.error else messages[state])
        self.install_button.setText(tr('Retry installation') if state in {'failed', 'cancelled'} or self.error else tr('Install Slow mode'))
        self.install_button.setEnabled(state not in {'ready', 'installing'})
        self.remove_button.setEnabled(state != 'installing' and any((runtime_root() / name).exists() for name in SLOW_FILES))

    def start_remove(self):
        if self.worker is not None:
            return
        if self.busy():
            self.status.setText(tr('Wait for the current analysis to finish before removing Slow mode.'))
            return
        if QMessageBox.question(self, tr('Remove Slow mode'),
                tr('Remove Slow models, Python environment and cache? Your TableScan history and documents will be kept.'),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        root = runtime_root()
        self.lock = QLockFile(str(root / 'setup.lock'))
        self.lock.setStaleLockTime(0)
        if not self.lock.tryLock(0):
            self.status.setText(tr('Slow mode is being installed by another process.'))
            return
        self.removing = True
        self.error = ''
        self.install_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText(tr('Removing Slow mode…'))
        self.worker = RemovalWorker(self)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def start_install(self):
        if self.worker is not None:
            return
        if self.busy():
            self.status.setText(tr('Wait for the current analysis to finish before installing Slow mode.'))
            return
        root = runtime_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.error = str(exc)
            self.refresh()
            return
        self.lock = QLockFile(str(root / 'setup.lock'))
        self.lock.setStaleLockTime(0)
        if not self.lock.tryLock(0):
            self.status.setText(tr('Slow mode is being installed by another process.'))
            return
        self.error = ''
        self.cancelling = False
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.install_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText(tr('Preparing Slow mode…'))
        self.worker = SetupWorker(self)
        self.worker.progress.connect(self.show_progress)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def cancel_install(self):
        if self.worker is None or self.cancelling:
            return
        self.cancelling = True
        self.cancel_button.setEnabled(False)
        self.status.setText(tr('Stopping Slow mode installation…'))
        self.worker.cancel()

    def show_progress(self, message, value, maximum):
        if self.cancelling:
            return
        self.status.setText(localized_saved_message(message))
        self.progress.setRange(0, 100 if maximum else 0)
        if maximum:
            self.progress.setValue(int(value * 100 / maximum))

    def failed(self, message):
        self.error = message
        self.details.show()

    def finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.lock.unlock()
        self.lock = None
        if self.removing:
            try:
                prune_empty(runtime_root())
            except OSError as exc:
                self.error = str(exc)
        self.removing = False
        self.progress.hide()
        self.cancel_button.hide()
        self.cancelling = False
        self.refresh()
