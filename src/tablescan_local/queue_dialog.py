from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QVBoxLayout, QMessageBox
from .localized_widgets import QDialog, QLabel, QPushButton, QTableWidget, QTableWidgetItem
from .i18n import tr


class AnalysisQueueDialog(QDialog):
    reviewRequested = Signal(str)
    prepareRequested = Signal(list)

    def __init__(self, queue, parent=None):
        super().__init__(parent)
        self.queue = queue
        self.setWindowTitle(tr('Analysis queue'))
        self.resize(1180, 450)
        self.setModal(False)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr('Analyses run one at a time. You can prepare other tables and review completed results.')))
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([tr('File'), tr('Analysis mode'), tr('Status'), tr('Progress'), tr('Preparation'), tr('Details'), ''])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        from PySide6.QtWidgets import QHeaderView
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(6, 44)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 250)
        self.table.itemSelectionChanged.connect(self.update_buttons)
        self.table.itemDoubleClicked.connect(lambda _: self.open_review())
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        self.cancel_button = QPushButton(tr('Cancel analysis'))
        self.cancel_button.clicked.connect(lambda: queue.cancel(self.selected_id()))
        self.retry_button = QPushButton(tr('Retry analysis'))
        self.retry_button.clicked.connect(lambda: queue.retry(self.selected_id()))
        self.review_button = QPushButton(tr('Open Review'))
        self.review_button.clicked.connect(self.open_review)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.retry_button)
        self.cancel_all_button = QPushButton(tr('Cancel all'))
        self.cancel_all_button.clicked.connect(lambda: self._bulk_action(queue.cancel_all))
        self.resume_all_button = QPushButton(tr('Resume all'))
        self.resume_all_button.setToolTip(tr('Restart cancelled, interrupted and failed analyses from the beginning. Completed results are kept.'))
        self.resume_all_button.clicked.connect(lambda: self._bulk_action(queue.resume_all))
        actions.addWidget(self.cancel_all_button)
        actions.addWidget(self.resume_all_button)
        self.prepare_button = QPushButton(tr('Prepare files again…'))
        self.prepare_button.setToolTip(tr('Check rotation, fit and protocols again. New analyses keep the previous results.'))
        self.prepare_button.clicked.connect(self.prepare_again)
        actions.addWidget(self.prepare_button)
        actions.addStretch()
        actions.addWidget(self.review_button)
        layout.addLayout(actions)
        queue.changed.connect(self.refresh)
        self.refresh()

    def selected_id(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _bulk_action(self, action):
        try:
            action()
        except OSError as exc:
            QMessageBox.warning(self, tr('Queue error'), str(exc))

    def refresh(self):
        from .slow_mode import completion_details
        from .i18n import localized_saved_message, fmt
        selected = self.selected_id()
        labels = {'queued': tr('Queued'), 'running': tr('Analyzing'), 'cancelling': tr('Stopping'),
                  'cancelled': tr('Cancelled'), 'interrupted': tr('Interrupted'), 'failed': tr('Failed'),
                  'review': tr('Review required'), 'ready': tr('Ready for export'), 'exported': tr('Exported')}
        colors = {'queued': ('#DBEAFE', '#1E40AF'), 'running': ('#CFFAFE', '#155E75'),
                  'cancelling': ('#FFEDD5', '#9A3412'), 'interrupted': ('#E2E8F0', '#334155'),
                  'failed': ('#FEE2E2', '#991B1B'), 'review': ('#FEF3C7', '#92400E'),
                  'ready': ('#DCFCE7', '#166534'), 'exported': ('#EDE9FE', '#5B21B6')}
        visible = [entry for entry in self.queue.entries if entry['status'] != 'cancelled']
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.setRowCount(len(visible))
        for row, entry in enumerate(visible):
            mode = tr('Slow analysis') if entry.get('slow_mode') else (tr('High accuracy') if entry.get('high_accuracy', True) else tr('Fast analysis'))
            prepared = entry.get('preparation', {})
            preparation = tr('Manually checked') if prepared.get('status') == 'manual' else tr('Fitted')
            preparation = (fmt("{label} · {rotation}° · {rows}×{columns}", label=preparation, rotation=prepared["rotation"], rows=prepared["rows"], columns=prepared["columns"])
                           if prepared else tr('Saved settings'))
            values = [Path(entry['source_path']).name, mode, labels[entry['status']],
                      str(entry['progress']) + '%', preparation,
                      completion_details(entry['slow_diagnostics']) if entry.get('slow_diagnostics') and entry['status'] in {'review', 'ready', 'exported'} else localized_saved_message(entry['message'])]
            for column, value in enumerate(values):
                item = self.table.item(row, column)
                if item is None:
                    item = QTableWidgetItem(value)
                    self.table.setItem(row, column, item)
                else:
                    item.setText(value)
                item.setToolTip(value)
                item.setData(Qt.ItemDataRole.UserRole, entry['job_id'])
                if column == 2:
                    background, foreground = colors[entry['status']]
                    item.setBackground(QColor(background))
                    item.setForeground(QColor(foreground))
            button = self.table.cellWidget(row, 6)
            if button is None or button.property('job_id') != entry['job_id']:
                button = QPushButton('×')
                button.setProperty('job_id', entry['job_id'])
                button.setAccessibleName(tr('Remove from queue: {p0}', p0=Path(entry['source_path']).name))
                button.setToolTip(tr('Remove from queue and stop this analysis if running. Saved results stay in file history.'))
                button.clicked.connect(lambda _, job_id=entry['job_id']: QTimer.singleShot(0, lambda: self._bulk_action(lambda: self.queue.remove(job_id))))
                self.table.setCellWidget(row, 6, button)
            if entry['job_id'] == selected:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self.update_buttons()

    def update_buttons(self):
        entry = next((e for e in self.queue.entries if e['job_id'] == self.selected_id()), None)
        status = entry['status'] if entry else ''
        if hasattr(self, 'cancel_button'):
            self.cancel_button.setEnabled(status in ('queued', 'running'))
            self.retry_button.setEnabled(status in ('failed', 'cancelled', 'interrupted'))
            self.review_button.setEnabled(status in ('review', 'ready', 'exported'))
            self.cancel_all_button.setEnabled(not self.queue.closing and any(
                e['status'] in ('running', 'queued') for e in self.queue.entries))
            resumable = sum(e['status'] in ('cancelled', 'interrupted', 'failed', 'cancelling') for e in self.queue.entries)
            self.resume_all_button.setEnabled(not self.queue.closing and resumable > 0)
            self.resume_all_button.setText(tr('Resume all ({p0})', p0=resumable) if resumable else tr('Resume all'))
            self.prepare_button.setEnabled(not self.queue.closing and any(e['status'] != 'cancelled' for e in self.queue.entries))

    def prepare_again(self):
        paths = list(dict.fromkeys(e['stored_path'] for e in self.queue.entries if e['status'] != 'cancelled'))
        if paths:
            QTimer.singleShot(0, lambda: self.prepareRequested.emit(paths))

    def open_review(self):
        entry = next((e for e in self.queue.entries if e['job_id'] == self.selected_id()), None)
        if entry and entry['status'] in ('review', 'ready', 'exported'):
            job_id = entry['job_id']
            # Let the native button/accessibility callback finish before hiding
            # its window and replacing the document widgets.
            QTimer.singleShot(0, lambda: self._open_review(job_id))

    def _open_review(self, job_id):
        self.table.clearSelection()
        self.hide()
        self.reviewRequested.emit(job_id)
