"""Optional Slow mode installation card for the application's Settings page."""
from PySide6.QtCore import QLockFile, QThread, QTimer, Signal
from PySide6.QtWidgets import QVBoxLayout, QProgressBar, QPlainTextEdit

from .i18n import tr, localized_saved_message
from .localized_widgets import QWidget, QLabel, QPushButton
from .slow_runtime import runtime_root
from . import slow_setup


class SetupWorker(QThread):
    progress = Signal(str, int, int)
    failed = Signal(str)

    def run(self):
        try:
            slow_setup.install(self.progress.emit)
        except Exception as exc:
            self.failed.emit(str(exc))


class SlowSettings(QWidget):
    def __init__(self, parent=None, busy=lambda: False):
        super().__init__(parent)
        self.busy = busy
        self.worker = None
        self.lock = None
        self.error = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.addWidget(QLabel(tr('Slow mode')))
        note = QLabel(tr('Additional local verification with Qwen and GLM. Installation needs internet and at least 15 GB of free space. CPU: 16 GB RAM minimum, 24 GB recommended. Windows installs Python automatically.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.install_button = QPushButton()
        self.install_button.clicked.connect(self.start_install)
        layout.addWidget(self.install_button)
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
            'ready': tr('Slow mode is installed and ready. Select Slow analysis when recognizing a document.'),
            'missing': tr('Slow mode is not installed.'),
            'installing': tr('Slow mode is being installed by another process.'),
            'failed': tr('Slow mode setup is incomplete or needs repair. Retry installation.'),
        }
        self.status.setText(tr('Slow mode installation failed. Open installation details and retry.') if self.error else messages[state])
        self.install_button.setText(tr('Retry installation') if state == 'failed' or self.error else tr('Install Slow mode'))
        self.install_button.setEnabled(state not in {'ready', 'installing'})

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
        self.install_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText(tr('Preparing Slow mode…'))
        self.worker = SetupWorker(self)
        self.worker.progress.connect(self.show_progress)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def show_progress(self, message, value, maximum):
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
        self.progress.hide()
        self.refresh()
