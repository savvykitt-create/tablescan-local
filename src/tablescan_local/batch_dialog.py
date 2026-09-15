"""Preview and customize each document before submitting a batch to OCR."""
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPixmap, QPen, QBrush
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QVBoxLayout, QMessageBox,
                              QGraphicsView, QGraphicsScene)
import cv2

from .batch_preflight import Compatibility, PreflightWorker, assess_geometry, assess_orientations
from .domain import TableTemplate
from .i18n import tr
from .imaging import load_document, rotate_document, detect_grid
from .localized_widgets import (QDialog, QLabel, QPushButton, QComboBox, QCheckBox,
                                QTableWidget, QTableWidgetItem, QDialogButtonBox)


MODES = ((False, False), (True, False), (True, True))


class ProtocolPreview(QGraphicsView):
    """Zoomable, bounded-resolution preview; full-resolution editing is separate."""
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setMinimumHeight(220)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def display(self, image, template, detection=None):
        self.scene().clear()
        if image is None:
            return
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        self.scene().addPixmap(QPixmap.fromImage(QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy()))
        for grid, color in ((detection, '#D97706'), (template, '#2563EB')):
            if grid is None:
                continue
            pen = QPen(QColor(color), 1)
            for y in grid.row_guides:
                self.scene().addLine(grid.column_guides[0]*w, y*h, grid.column_guides[-1]*w, y*h, pen)
            for x in grid.column_guides:
                self.scene().addLine(x*w, grid.row_guides[0]*h, x*w, grid.row_guides[-1]*h, pen)
        if template:
            for field in template.fields:
                r = field.rect
                fill = QColor(field.color); fill.setAlpha(25)
                self.scene().addRect(r.x*w, r.y*h, r.width*w, r.height*h, QPen(QColor(field.color), 1), QBrush(fill))
        self.scene().setSceneRect(0, 0, w, h)
        self.fit()

    def fit(self):
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()

    def wheelEvent(self, event):
        self.scale(1.2 if event.angleDelta().y() > 0 else 1/1.2,
                   1.2 if event.angleDelta().y() > 0 else 1/1.2)
        event.accept()


class FileProtocolDialog(QDialog):
    def __init__(self, item, template, editor_factory, parent=None, *, save_template=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Protocol for {p0}', p0=Path(item.path).name))
        self.resize(1240, 830)
        self.images = load_document(item.path)
        self.images = rotate_document(self.images, template.rotation_degrees)
        self.source_path = item.path
        self.save_template = save_template
        self.result_template = None
        self.result_geometry = None
        self.current_page = 0
        layout = QVBoxLayout(self)
        note = QLabel(tr('Changes apply only to this file. Check the grid, fields and rules on every page.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.page_select = QComboBox()
        for index in range(len(self.images)):
            self.page_select.addItem(tr('Page {p0}', p0=index + 1), index)
        layout.addWidget(self.page_select)
        self.editor = editor_factory()
        layout.addWidget(self.editor, 1)
        self.editor.set_document(self.images[0], TableTemplate.from_dict(template.to_dict()))
        self.editor.saveVersionRequested.connect(lambda _: self.save())
        self.editor.rotationRequested.connect(self.rotate)
        self.page_select.currentIndexChanged.connect(self.change_page)
        self.save_template_button = QPushButton(tr('Save as new template'))
        self.save_template_button.setEnabled(save_template is not None)
        self.save_template_button.clicked.connect(self.save_to_library)
        layout.addWidget(self.save_template_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr('Use for this file'))
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save_to_library(self):
        if self.save_template is None or not self.editor.prepare_current_settings():
            return
        template = TableTemplate.from_dict(self.editor.template.to_dict())
        template.id = template.family_id = str(uuid4())
        template.template_version = 0
        template.reference_page_aspect = self.images[0].shape[1] / self.images[0].shape[0]
        try:
            template.validate_value_rules()
            self.save_template(template, self.source_path)
        except Exception as exc:
            QMessageBox.warning(self, tr('Не удалось сохранить шаблон'), str(exc))
            return
        self.save_template_button.setText(tr('Template saved'))

    def change_page(self, index):
        if self.editor.prepare_current_settings():
            self.editor.set_document(self.images[index], self.editor.template)
            self.current_page = index
        else:
            self.page_select.blockSignals(True)
            self.page_select.setCurrentIndex(self.current_page)
            self.page_select.blockSignals(False)

    def rotate(self, degrees):
        if not self.editor.prepare_current_settings():
            return
        template = self.editor.template
        template.rotation_degrees = (template.rotation_degrees + degrees) % 360
        self.images = rotate_document(self.images, degrees)
        self.editor.set_document(self.images[self.page_select.currentIndex()], template)

    def save(self):
        if not self.editor.prepare_current_settings():
            return
        template = TableTemplate.from_dict(self.editor.template.to_dict())
        try:
            template.validate_value_rules()
        except ValueError as exc:
            QMessageBox.warning(self, tr('Проверьте правила'), str(exc))
            return
        # Freeze the explicitly inspected geometry so OCR cannot refit away edits.
        template.auto_fit_rows = False
        pages = []
        for image in self.images:
            try:
                detection = detect_grid(image)
            except ValueError:
                detection = None
            pages.append((image.shape, detection))
        self.result_template, self.result_geometry = template, pages
        self.accept()


class BatchPreparationDialog(QDialog):
    def __init__(self, paths, templates, editor_factory, parent=None, *, save_template=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Prepare batch'))
        self.resize(1260, 840)
        self.paths, self.templates = list(paths), list(templates)
        self.editor_factory = editor_factory
        self.save_template = save_template
        self.items = [None] * len(paths)
        self.custom = {}
        self.rows = []
        self._closing = False
        self.jobs = []
        self.worker = PreflightWorker(paths, templates, self)
        layout = QVBoxLayout(self)
        note = QLabel(tr('Each file is rotated and fitted separately. Check the overlay before analysis; files that need alignment cannot be queued until corrected. Compatibility measures layout, not OCR accuracy.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        common = QHBoxLayout()
        common.addWidget(QLabel(tr('Protocol for all files')))
        self.common_template = QComboBox()
        self.common_template.addItem(tr('Automatic — highest compatibility'), '__auto__')
        for template in templates:
            self.common_template.addItem(template.name, template.id)
        self.common_template.addItem(tr('Blank protocol'), '__blank__')
        common.addWidget(self.common_template, 1)
        apply = QPushButton(tr('Apply to all'))
        apply.clicked.connect(self.apply_all)
        common.addWidget(apply)
        layout.addLayout(common)
        self.table = QTableWidget(len(paths), 7)
        self.table.setHorizontalHeaderLabels([tr('Include'), tr('File'), tr('Protocol'), tr('Compatibility'), tr('Rotation'), tr('Auto-fit'), tr('Details')])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((75, 145, 245, 110, 85, 130)):
            self.table.setColumnWidth(column, width)
        self.table.itemDoubleClicked.connect(lambda cell: self.edit_file(cell.row()))
        self.table.itemSelectionChanged.connect(self.selected_file_changed)
        for index, path in enumerate(paths):
            include, select = QCheckBox(), QComboBox()
            include.setChecked(True)
            include.toggled.connect(self.update_actions)
            select.setEnabled(False)
            select.currentIndexChanged.connect(lambda _, row=index: self.selection_changed(row))
            self.rows.append((include, select))
            self.table.setCellWidget(index, 0, include)
            self.table.setItem(index, 1, QTableWidgetItem(Path(path).name))
            self.table.item(index, 1).setToolTip(path)
            self.table.setCellWidget(index, 2, select)
            self.table.setItem(index, 3, QTableWidgetItem('—'))
            self.table.setItem(index, 4, QTableWidgetItem('—'))
            self.table.setItem(index, 5, QTableWidgetItem('—'))
            self.table.setItem(index, 6, QTableWidgetItem(tr('Checking compatibility…')))
        layout.addWidget(self.table, 1)
        self.table.setCurrentCell(0, 1)
        preview_row = QHBoxLayout()
        self.preview = ProtocolPreview()
        preview_row.addWidget(self.preview, 2)
        preview_details = QVBoxLayout()
        preview_details.addWidget(QLabel(tr('Overlay preview · blue: protocol · orange: detected grid')))
        self.preview_page = QComboBox()
        self.preview_page.currentIndexChanged.connect(self.update_preview)
        preview_details.addWidget(self.preview_page)
        self.preview_detail = QLabel('')
        self.preview_detail.setWordWrap(True)
        self.preview_detail.setMinimumWidth(260)
        self.preview_detail.setMaximumWidth(380)
        preview_details.addWidget(self.preview_detail, 1)
        fit_preview = QPushButton(tr('Fit preview'))
        fit_preview.clicked.connect(self.preview.fit)
        preview_details.addWidget(fit_preview)
        preview_row.addLayout(preview_details, 1)
        layout.addLayout(preview_row, 1)
        self.edit_button = QPushButton(tr('Open / adjust selected file'))
        self.edit_button.clicked.connect(lambda: self.edit_file(self.table.currentRow()))
        layout.addWidget(self.edit_button)
        self.progress = QLabel(tr('Checking files: {p0} / {p1}', p0=0, p1=len(paths)))
        layout.addWidget(self.progress)
        modes = QHBoxLayout()
        modes.addWidget(QLabel(tr('Analysis mode for the batch')))
        self.mode = QComboBox()
        self.mode.addItems([tr('Fast analysis'), tr('High accuracy'), tr('Slow analysis — additional model verification')])
        self.mode.setCurrentIndex(1)
        modes.addWidget(self.mode, 1)
        layout.addLayout(modes)
        self.mode_note = QLabel('')
        self.mode_note.setWordWrap(True)
        layout.addWidget(self.mode_note)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.update_mode()
        actions = QHBoxLayout()
        cancel = QPushButton(tr('Отмена'))
        cancel.clicked.connect(self.reject)
        self.start_button = QPushButton(tr('Add selected files to queue'))
        self.start_button.setProperty('primary', True)
        self.start_button.clicked.connect(self.submit)
        actions.addWidget(cancel)
        actions.addStretch()
        actions.addWidget(self.start_button)
        layout.addLayout(actions)
        self.worker.prepared.connect(self.prepared)
        self.worker.progress.connect(self.preflight_progress)
        self.worker.finished.connect(self.preflight_finished)
        self.update_actions()
        QTimer.singleShot(0, self._start_preflight)

    def _start_preflight(self):
        if self._closing:
            self.preflight_finished()
        else:
            self.worker.start()

    def update_mode(self):
        self.mode_note.setText(tr('Files run sequentially in the selected mode. Slow analysis requires the local slow-mode module.')
                              if self.mode.currentIndex() == 2 else
                              tr('Files run sequentially. You can review completed results while the queue continues.'))

    def prepared(self, index, item):
        self.items[index] = item
        include, select = self.rows[index]
        if not item.error:
            for template in self.templates:
                if template.id not in item.assessments:
                    item.assessments[template.id] = assess_orientations(template, item.geometry)
        select.blockSignals(True)
        select.clear()
        for template in sorted(self.templates, key=lambda t: -(item.assessments[t.id].score or 0) if t.id in item.assessments else 0):
            result = item.assessments.get(template.id)
            score = str(result.score) + '%' if result and result.score is not None else '—'
            select.addItem(f'{template.name} · {score}', template.id)
            if result:
                select.setItemData(select.count() - 1, '\n'.join(result.reasons + ([result.error] if result.error else [])), Qt.ItemDataRole.ToolTipRole)
        select.addItem(tr('Blank protocol'), '__blank__')
        select.setCurrentIndex(0 if self.common_template.currentData() == '__auto__' else max(0, select.findData(self.common_template.currentData())))
        select.blockSignals(False)
        select.setEnabled(not item.error)
        if item.error:
            include.setChecked(False)
            include.setEnabled(False)
        self.progress.setText(tr('Checking files: {p0} / {p1}', p0=sum(i is not None for i in self.items), p1=len(self.items)))
        self.refresh_row(index)

    def preflight_progress(self, index, message):
        if not self._closing:
            self.table.item(index, 6).setText(message)
            self.progress.setText(tr('Preparing {p0}: {p1}', p0=Path(self.paths[index]).name, p1=message))

    def selected_file_changed(self):
        self.update_actions()
        if not hasattr(self, 'preview'):
            return
        row = self.table.currentRow()
        item = self.items[row] if row >= 0 else None
        self.preview_page.blockSignals(True)
        self.preview_page.clear()
        for page in range(item.pages if item else 0):
            self.preview_page.addItem(tr('Page {p0}', p0=page + 1))
        self.preview_page.blockSignals(False)
        self.update_preview()

    def update_preview(self):
        row = self.table.currentRow()
        item = self.items[row] if row >= 0 else None
        result = self.assessment(row) if item else None
        page = max(0, self.preview_page.currentIndex())
        if item and item.thumbnails and result is None:
            geometry = item.geometry.get(0, [])
            self.preview.display(item.thumbnails[page], None, geometry[page][1] if geometry else None)
            self.preview_detail.setText(tr('Open the file to create and check its protocol.'))
            return
        if not item or not result or not item.thumbnails:
            self.preview.display(None, None)
            self.preview_detail.setText(tr('Select a checked file to see its alignment.'))
            return
        image = rotate_document([item.thumbnails[page]], result.rotation_degrees)[0]
        geometry = item.geometry.get(result.rotation_degrees, [])
        detection = geometry[page][1] if geometry else None
        self.preview.display(image, result.preview_template or result.template, detection)
        self.preview_detail.setText('\n'.join(([result.error] if result.error else []) + result.reasons))

    def assessment(self, index):
        if index in self.custom and self.rows[index][1].currentData() == '__custom__':
            return self.custom[index]
        item = self.items[index]
        return item.assessments.get(self.rows[index][1].currentData()) if item else None

    def selection_changed(self, index):
        self.table.setCurrentCell(index, 1)
        self.refresh_row(index)

    def refresh_row(self, index):
        item, result = self.items[index], self.assessment(index)
        score = str(result.score) + '%' if result and result.score is not None else '—'
        if item and item.error:
            detail = item.error
        elif result:
            detail = result.error or '; '.join(result.reasons)
            if self.rows[index][1].currentData() == '__custom__':
                detail = str(tr('Individual settings; geometry fixed for this file.')) + ' ' + detail
        else:
            detail = str(tr('Open the file to create and check its protocol.')) if item else str(tr('Checking compatibility…'))
        self.table.item(index, 3).setText(score)
        self.table.item(index, 4).setText(f'{result.rotation_degrees}°' if result else '—')
        status = tr('Fitted') if result and result.fit_status == 'fitted' else tr('Manually checked') if result and result.fit_status == 'manual' else tr('Needs alignment')
        self.table.item(index, 5).setText(status if item else '—')
        self.table.item(index, 5).setForeground(QColor('#166534' if result and result.template else '#92400E'))
        self.table.item(index, 6).setText(detail)
        self.table.item(index, 6).setToolTip(detail)
        self.update_actions()
        if index == self.table.currentRow():
            self.selected_file_changed()

    def apply_all(self):
        for index, item in enumerate(self.items):
            if item is not None:
                self.custom.pop(index, None)
                select = self.rows[index][1]
                select.blockSignals(True)
                custom_index = select.findData('__custom__')
                if custom_index >= 0:
                    select.removeItem(custom_index)
                select.blockSignals(False)
                self.rows[index][1].setCurrentIndex(0 if self.common_template.currentData() == '__auto__' else self.rows[index][1].findData(self.common_template.currentData()))
                self.refresh_row(index)

    def update_actions(self):
        if not hasattr(self, 'start_button'):
            return
        row = self.table.currentRow()
        self.edit_button.setEnabled(row >= 0 and self.items[row] is not None and not self.items[row].error)
        selected = [i for i, (include, _) in enumerate(self.rows) if include.isChecked()]
        self.start_button.setEnabled(self.worker is None and bool(selected) and all(
            self.assessment(i) and self.assessment(i).template is not None for i in selected))
        self.start_button.setToolTip(tr('Wait for checking to finish. Files that cannot be fitted must be opened and adjusted, or excluded.'))

    def edit_file(self, index):
        if index < 0 or self.items[index] is None or self.items[index].error:
            return
        result = self.assessment(index)
        try:
            if self.rows[index][1].currentData() == '__blank__':
                from .domain import NormalizedRect
                from .imaging import evenly_spaced_guides
                geometry = self.items[index].geometry[0]
                shape, detection = geometry[0]
                if detection is None:
                    rect = NormalizedRect(.1, .1, .8, .8)
                    rows, columns = evenly_spaced_guides(rect, 10, 6)
                else:
                    rect, rows, columns = detection.table_rect, detection.row_guides, detection.column_guides
                identifier = str(uuid4())
                template = TableTemplate(identifier, f'{Path(self.paths[index]).stem} — template', rect, rows, columns,
                                         family_id=identifier, template_version=0,
                                         reference_page_aspect=shape[1] / shape[0])
                template.ensure_column_rules()
            else:
                template = (result.template or result.preview_template) if result and (result.template or result.preview_template) else next(
                    t for t in self.templates if t.id == self.rows[index][1].currentData())
            dialog = FileProtocolDialog(self.items[index], template, self.editor_factory, self,
                                        save_template=self.save_to_library if self.save_template else None)
        except Exception as exc:
            QMessageBox.warning(self, tr('Не удалось открыть документ'), str(exc))
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            template = dialog.result_template
            result = assess_geometry(template, dialog.result_geometry, auto_fit=False)
            # Manual inspection can approve a form whose grid is undetectable.
            result.template = template
            result.preview_template = template
            result.rotation_degrees = template.rotation_degrees
            result.fit_status = 'manual'
            self.items[index].geometry[template.rotation_degrees] = dialog.result_geometry
            # The explicit save approves this geometry even if grid detection
            # is unavailable; never conceal that fact in the details.
            if result.error:
                result.reasons.append(result.error)
                result.error = ''
            self.custom[index] = result
            select = self.rows[index][1]
            select.blockSignals(True)
            custom_index = select.findData('__custom__')
            if custom_index < 0:
                select.addItem(tr('Individual protocol'), '__custom__')
                custom_index = select.count() - 1
            select.setCurrentIndex(custom_index)
            select.blockSignals(False)
            self.refresh_row(index)
        dialog.deleteLater()

    def save_to_library(self, template, source):
        saved = self.save_template(template, source)
        self.templates.append(saved)
        self.common_template.addItem(saved.name, saved.id)
        for index, item in enumerate(self.items):
            if item is None or item.error:
                continue
            item.assessments[saved.id] = assess_orientations(saved, item.geometry)
            select = self.rows[index][1]
            ranked = sorted(self.templates, key=lambda t: -(item.assessments[t.id].score or 0))
            position = next(i for i, template in enumerate(ranked) if template.id == saved.id)
            score = item.assessments[saved.id].score
            select.blockSignals(True)
            select.insertItem(position, f'{saved.name} · {score}%' if score is not None else saved.name, saved.id)
            select.blockSignals(False)
        return saved

    def preflight_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.update_actions()
        if self._closing:
            super().reject()

    def reject(self):
        if self.worker is not None:
            self._closing = True
            self.worker.requestInterruption()
            self.progress.setText(tr('Stopping'))
        else:
            super().reject()

    def submit(self):
        if not self.start_button.isEnabled():
            return
        if self.mode.currentIndex() == 2:
            from .slow_mode import runtime_config
            try:
                runtime_config()
            except RuntimeError as exc:
                QMessageBox.warning(self, tr('Slow mode недоступен'), str(exc))
                return
        self.jobs = [(self.paths[i], TableTemplate.from_dict(self.assessment(i).template.to_dict()))
                     for i, (include, _) in enumerate(self.rows) if include.isChecked()]
        self.accept()

    @property
    def analysis_options(self):
        return MODES[self.mode.currentIndex()]

    @property
    def preparations(self):
        return {self.paths[i]: dict(rotation=result.rotation_degrees, status=result.fit_status,
                                   score=result.score, rows=result.template.rows, columns=result.template.columns)
                for i, (include, _) in enumerate(self.rows)
                if include.isChecked() and (result := self.assessment(i)) and result.template}
