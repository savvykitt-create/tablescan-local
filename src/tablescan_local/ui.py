from __future__ import annotations
from collections.abc import Sequence
from .i18n import localized_rule_name

from .i18n import tr, fmt, join_text
import json
import os
from .i18n import language, set_language, SUPPORTED_LANGUAGES
from bisect import bisect_right
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
from PySide6.QtCore import QEvent, QLockFile, QRectF, Qt, QThread, QStandardPaths, QTimer, QSettings, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QHBoxLayout, QHeaderView, QInputDialog, QMessageBox, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout

from .localized_widgets import QAction, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QRadioButton, QPushButton, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QToolButton, QWidget

from .documents import document_file_filter
from .domain import CellRuleRegion, ColumnRule, FieldRegion, JobResult, NormalizedRect, TableTemplate, excel_column_name
from .constraints import ValueConstraints
from .rule_editor import ValueRuleDialog, optional_number
from . import __version__
from .exporter import export_job
from .imaging import GridDetection, detect_grid, evenly_spaced_guides, open_document, rotate_document
from .ocr import OcrValue, constrain_reading
from .pipeline import suggest_standard_fields
from .recognition_worker import RecognitionWorker
from .storage import LocalStore
from .theme import apply_theme, set_theme_style
from .template_matcher import TemplateMatch, rank_templates
from .template_fit import fit_document_template, fit_template
from .analysis_queue import AnalysisQueue
from .queue_dialog import AnalysisQueueDialog
from .ui_common import BLUE, LIGHT_BLUE, AMBER, GREEN, BORDER, TEXT, MUTED, SURFACE, pixmap_from_bgr, notify
from .document_canvas import DocumentCanvas, OverlayRectItem, GuideLineItem
from .review_page import ReviewPage, CropPreview


class DropArea(QFrame):
    filesDropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("dropArea")
        set_theme_style(self, fmt('#dropArea {{ border: 2px dashed #6B8FEA; border-radius: 10px; background: #FFFFFF; }}#dropArea:hover {{ background: {p0}; }}', p0=LIGHT_BLUE))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()


class FilesPage(QWidget):
    chooseRequested = Signal()
    filesDropped = Signal(list)
    recentOpened = Signal(str)
    historyRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.history_offset = 0
        self.history_page_size = 20
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(24)

        area = DropArea()
        area.filesDropped.connect(self.filesDropped)
        area_layout = QVBoxLayout(area)
        area_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        area_layout.setSpacing(14)
        icon = QLabel("▦")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_theme_style(icon, fmt('font-size: 68px; color: {p0};', p0=BLUE))
        heading = QLabel(tr('Добавьте изображения таблиц'))
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_theme_style(heading, "font-size: 27px; font-weight: 750;")
        choose = QPushButton(tr('Выбрать файлы'))
        choose.setProperty("primary", True)
        choose.setFixedWidth(210)
        choose.clicked.connect(self.chooseRequested)
        formats = QLabel(tr('или перетащите сюда PDF, PNG, JPG, TIFF или HEIC/HEIF'))
        formats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_theme_style(formats, fmt('color: {p0}; font-size: 15px;', p0=MUTED))
        privacy = QLabel(tr('▣  Файлы и распознанные данные не покидают этот компьютер'))
        privacy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_theme_style(privacy, fmt('color: {p0};', p0=MUTED))
        for widget in (icon, heading, choose, formats):
            area_layout.addWidget(widget, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(area, 4)

        recent_heading = QLabel(tr('Недавние файлы'))
        set_theme_style(recent_heading, "font-size: 17px; font-weight: 700;")
        layout.addWidget(recent_heading)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText(tr('Поиск по всей истории файлов'))
        self.history_search.setAccessibleName(tr('Поиск по всей истории файлов'))
        self.history_search.textChanged.connect(self._search_history)
        layout.addWidget(self.history_search)
        self.recent = QTableWidget(0, 3)
        self.recent.setHorizontalHeaderLabels([tr('Файл'), tr('Изменён'), tr('Состояние')])
        self.recent.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.recent.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.recent.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.recent.verticalHeader().hide()
        self.recent.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recent.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recent.doubleClicked.connect(lambda index: self.recentOpened.emit(self.recent.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.recent, 2)
        paging = QHBoxLayout()
        self.history_previous = QPushButton(tr('Предыдущие'))
        self.history_next = QPushButton(tr('Следующие'))
        self.history_count = QLabel("")
        self.history_previous.clicked.connect(lambda: self._move_history(-1))
        self.history_next.clicked.connect(lambda: self._move_history(1))
        paging.addWidget(self.history_previous)
        paging.addWidget(self.history_count)
        paging.addStretch()
        paging.addWidget(self.history_next)
        layout.addLayout(paging)

    def _search_history(self, _text: str) -> None:
        self.history_offset = 0
        self.historyRequested.emit()

    def _move_history(self, direction: int) -> None:
        self.history_offset = max(0, self.history_offset + direction * self.history_page_size)
        self.historyRequested.emit()

    def set_recent(self, rows: list[dict[str, str]], total: int | None = None) -> None:
        total = len(rows) if total is None else total
        self.history_previous.setEnabled(self.history_offset > 0)
        self.history_next.setEnabled(self.history_offset + len(rows) < total)
        self.history_count.setText(tr('{p0}–{p1} из {p2}', p0=self.history_offset + 1 if rows else 0,
                                     p1=self.history_offset + len(rows), p2=total))
        self.recent.setRowCount(len(rows))
        labels = {"imported": tr('Импортирован'), "review": tr('Нужна сверка'), "ready": tr('Готов к экспорту')}
        for row_index, row in enumerate(rows):
            name = QTableWidgetItem(row["source_name"])
            name.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.recent.setItem(row_index, 0, name)
            try:
                modified = datetime.fromisoformat(row["updated_at"]).astimezone().strftime("%Y-%m-%d %H:%M %Z")
            except ValueError:
                modified = row["updated_at"]
            self.recent.setItem(row_index, 1, QTableWidgetItem(modified))
            self.recent.setItem(row_index, 2, QTableWidgetItem(labels.get(row["status"], row["status"])))


class TemplateChoiceDialog(QDialog):
    """Make automatic matching visible and keep the final choice with the user."""

    def __init__(self, matches: list[TemplateMatch], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Выберите шаблон'))
        self.setMinimumSize(650, 430)
        layout = QVBoxLayout(self)
        heading = QLabel(tr('Какой шаблон применить к документу?'))
        set_theme_style(heading, "font-size: 20px; font-weight: 750;")
        layout.addWidget(heading)
        note = QLabel(tr('Программа сравнила сетку локально. Проверьте выбор: перед распознаванием будет показано наложение шаблона.'))
        note.setWordWrap(True); set_theme_style(note, fmt('color: {p0};', p0=MUTED))
        layout.addWidget(note)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        new_item = QListWidgetItem(tr('Создать новый шаблон для этого документа\nАвтоматически найденная сетка останется доступна для настройки'))
        new_item.setData(Qt.ItemDataRole.UserRole, "")
        self.list.addItem(new_item)
        for match in matches:
            percent = round(match.score * 100)
            item = QListWidgetItem(
                tr('{p0} — совпадение {p1}%\n', p0=match.template.name, p1=percent)
                + join_text('; ', match.reasons)
            )
            item.setData(Qt.ItemDataRole.UserRole, match.template.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, match.score)
            self.list.addItem(item)
        self.list.setCurrentRow(1 if matches and matches[0].score >= .55 else 0)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.list, 1)
        self.explanation = QLabel("")
        self.explanation.setWordWrap(True); set_theme_style(self.explanation, fmt('background: {p0}; padding: 10px; border-radius: 6px;', p0=LIGHT_BLUE))
        self.list.currentItemChanged.connect(self._update_explanation)
        layout.addWidget(self.explanation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr('Продолжить'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('Отмена импорта'))
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_explanation(self.list.currentItem())

    @property
    def selected_template_id(self) -> str:
        item = self.list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _update_explanation(self, item=None, _previous=None) -> None:
        if item is None or not item.data(Qt.ItemDataRole.UserRole):
            self.explanation.setText(tr('Будет создан новый рабочий шаблон. Его можно сохранить в библиотеку отдельной командой.'))
            return
        score = float(item.data(Qt.ItemDataRole.UserRole + 1) or 0)
        if score >= .82:
            message = tr('Высокое геометрическое совпадение. Всё равно проверьте линии сетки и ориентацию.')
        elif score >= .60:
            message = tr('Шаблон похож, но требуется внимательная проверка наложения.')
        else:
            message = tr('Совпадение слабое. Выберите этот шаблон только после визуальной проверки.')
        self.explanation.setText(message)


class TemplateLibraryPage(QWidget):
    createRequested = Signal()
    importRequested = Signal()
    openRequested = Signal(str)
    duplicateRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._template_signature: tuple[tuple[str, int, str, int], ...] = ()
        layout = QVBoxLayout(self); layout.setContentsMargins(30, 26, 30, 26); layout.setSpacing(16)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel(tr('Шаблоны')); set_theme_style(title, "font-size: 25px; font-weight: 750;")
        subtitle = QLabel(tr('Настройте сетку и допустимые значения один раз, затем применяйте шаблон к новым файлам.'))
        set_theme_style(subtitle, fmt('color: {p0};', p0=MUTED))
        title_box.addWidget(title); title_box.addWidget(subtitle)
        create = QPushButton(tr('Создать из образца')); create.setProperty("primary", True); create.clicked.connect(self.createRequested)
        header.addLayout(title_box); header.addStretch(); header.addWidget(create)
        import_button = QPushButton(tr('Импорт JSON'))
        import_button.clicked.connect(self.importRequested)
        header.addWidget(import_button)
        layout.addLayout(header)
        self.list = QListWidget()
        self.list.setSpacing(5)
        set_theme_style(self.list, fmt('QListWidget {{ border: 1px solid {p0}; border-radius: 9px; padding: 8px; }}QListWidget::item {{ min-height: 54px; padding: 9px 12px; border-radius: 7px; }}QListWidget::item:selected {{ background: {p1}; color: {p2}; border: 1px solid #A5B4FC; }}', p0=BORDER, p1=LIGHT_BLUE, p2=TEXT))
        self.list.itemDoubleClicked.connect(lambda _item: self._emit_selected(self.openRequested))
        layout.addWidget(self.list, 1)
        actions = QHBoxLayout(); actions.addStretch()
        duplicate = QPushButton(tr('Дублировать')); duplicate.clicked.connect(lambda: self._emit_selected(self.duplicateRequested))
        delete = QPushButton(tr('Удалить')); delete.setProperty("danger", True); delete.clicked.connect(lambda: self._emit_selected(self.deleteRequested))
        open_button = QPushButton(tr('Открыть редактор')); open_button.setProperty("primary", True); open_button.clicked.connect(lambda: self._emit_selected(self.openRequested))
        actions.addWidget(duplicate); actions.addWidget(delete); actions.addWidget(open_button)
        layout.addLayout(actions)

    def _emit_selected(self, signal) -> None:
        item = self.list.currentItem()
        if item:
            signal.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def set_templates(self, templates: list[TableTemplate]) -> None:
        latest = {}
        for template in templates:
            key = template.family_id or template.id
            if key not in latest or template.template_version > latest[key].template_version:
                latest[key] = template
        ordered = sorted(latest.values(), key=lambda item: item.name.casefold())
        signature = tuple((item.id, item.template_version, item.name, len(item.cell_rules)) for item in ordered)
        if signature == self._template_signature:
            return
        selected = self.list.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self._template_signature = signature
        self.list.clear()
        for template in ordered:
            sample = tr('есть') if template.reference_source_path and Path(template.reference_source_path).exists() else tr('нет')
            item = QListWidgetItem(
                tr('{p0}\nGrid: {p1} × {p2} · Regions: {p3} · Sample: {p4}', p0=template.name, p1=template.rows, p2=template.columns, p3=len(template.cell_rules), p4=sample)
            )
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            self.list.addItem(item)
        if ordered:
            self.list.setCurrentRow(0)
            self.select_template(selected_id)

    def select_template(self, template_id: str | None) -> None:
        for index in range(self.list.count()):
            if self.list.item(index).data(Qt.ItemDataRole.UserRole) == template_id:
                self.list.setCurrentRow(index)
                return


class PreparationTabs(QTabWidget):
    """Do not let a hidden, tall rules form stretch the exclusion workspace."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentChanged.connect(lambda _index: self.updateGeometry())

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        widget = self.currentWidget()
        if widget is not None:
            hint.setHeight(widget.minimumSizeHint().height() + self.tabBar().sizeHint().height() + 8)
        return hint


class TablePage(QWidget):
    continueRequested = Signal(object)
    saveVersionRequested = Signal(object)
    saveTemplateRequested = Signal(object)
    templateChanged = Signal(object)
    savedTemplateRequested = Signal(str)
    rotationRequested = Signal(int)

    def __init__(self, mode: str = "document", *, private_copy: bool = False) -> None:
        super().__init__()
        self.mode = mode
        self.image: np.ndarray | None = None
        self.template: TableTemplate | None = None
        self._loading_form = False
        self._selected_cells: set[tuple[int, int]] = set()
        self._loading_quick_rule = False
        self._quick_dirty = False
        self._quick_error = ""
        self._quick_pending_cells = set()
        self._quick_active_rule_id = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 20)
        header = QHBoxLayout()
        title = QLabel(tr('Редактор шаблона') if mode == "template" else tr('Совмещение документа'))
        set_theme_style(title, "font-size: 23px; font-weight: 750;")
        self.version_label = QLabel("")
        set_theme_style(self.version_label, fmt('color: {p0}; font-weight: 650;', p0=MUTED))
        header.addWidget(title); header.addWidget(self.version_label); header.addStretch()
        if mode == "template":
            validate = QPushButton(tr('Проверить шаблон'))
            validate.clicked.connect(self._validate_template)
            save_version = QPushButton(tr('Use for this file') if private_copy else tr('Сохранить шаблон'))
            save_version.setProperty("primary", True)
            save_version.clicked.connect(self._continue)
            header.addWidget(validate); header.addWidget(save_version)
        outer.addLayout(header)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.document_split = split
        split.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 14, 0)
        tools = QHBoxLayout()
        zoom_out = QToolButton(text="−")
        zoom_in = QToolButton(text="+")
        fit = QToolButton(text=tr('Вписать'))
        zoom_out.clicked.connect(lambda: self.canvas.zoom_by(0.85))
        zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.18))
        fit.clicked.connect(self._fit_canvas)
        for button in (zoom_out, zoom_in, fit):
            tools.addWidget(button)
        rotate_left = QToolButton(text=tr('↶ Повернуть'))
        rotate_right = QToolButton(text=tr('Повернуть ↷'))
        rotate_left.clicked.connect(lambda: self.rotationRequested.emit(90))
        rotate_right.clicked.connect(lambda: self.rotationRequested.emit(-90))
        tools.addWidget(rotate_left)
        tools.addWidget(rotate_right)
        self.exclusions_button = QToolButton(text=tr('Exclusions'))
        self.exclusions_button.clicked.connect(lambda: self.tabs.setCurrentWidget(self.exclusions_panel))
        tools.addWidget(self.exclusions_button)
        tools.addStretch()
        left_layout.addLayout(tools)
        self.canvas = DocumentCanvas()
        self.canvas.regionCreated.connect(self._region_created)
        self.canvas.geometryChanged.connect(lambda: self.templateChanged.emit(self.template))
        self.canvas.fieldSelected.connect(lambda index: self.field_list.setCurrentRow(index))
        self.canvas.cellSelectionChanged.connect(self._selection_changed)
        left_layout.addWidget(self.canvas)
        split.addWidget(left)

        self.tabs = PreparationTabs()
        self.tabs.setMinimumWidth(400)
        self.tabs.setMaximumWidth(640)
        self.tabs.addTab(self._build_grid_tab(), tr('Сетка'))
        self.tabs.addTab(self._build_fields_tab(), tr('Поля'))
        self.tabs.addTab(self._build_cell_rules_tab(), tr('Правила'))
        from .fixed_cells import FixedCellsPanel
        self.fixed_panel = FixedCellsPanel(lambda: self.template, self.canvas)
        self.fixed_panel.changed.connect(self.templateChanged.emit)
        self.tabs.addTab(self.fixed_panel, tr('Template values'))
        from .exclusions import ExclusionsPanel
        self.exclusions_panel = ExclusionsPanel(lambda: self.template, self.canvas)
        self.exclusions_panel.changed.connect(self._exclusions_changed)
        self.tabs.addTab(self.exclusions_panel, tr('Exclusions'))
        self.tabs.currentChanged.connect(self._tab_changed)
        split.addWidget(self.tabs)
        split.setSizes([820, 440])
        outer.addWidget(split, 1)

    def _build_grid_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 18, 8, 8)
        form = QFormLayout()
        form.setSpacing(12)
        self.saved_template_select = QComboBox()
        # User selection applies the complete template, not just its label.
        # activated does not fire when controls are populated programmatically.
        self.saved_template_select.activated.connect(self._request_saved_template)
        self.template_name = QLineEdit("New template")
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 200)
        self.columns_spin = QSpinBox()
        self.columns_spin.setRange(1, 100)
        self.header_rows_spin = QSpinBox()
        self.header_rows_spin.setRange(0, 20)
        self.row_labels_spin = QSpinBox()
        self.row_labels_spin.setRange(0, 20)
        self.crossed = QCheckBox(tr('Находить зачёркнутые строки'))
        self.crossed.setChecked(True)
        self.high_accuracy = QCheckBox(tr('Максимальная точность (медленнее)'))
        self.high_accuracy.setChecked(True)
        self.high_accuracy.setToolTip(tr('Многоэтапно сравнивает модели, внутренние варианты чисел, повторные вырезы, разделитель и независимую проверку цифр. Всё работает локально.'))
        form.addRow(tr('Сохранённый шаблон'), self.saved_template_select)
        form.addRow(tr('Название шаблона'), self.template_name)
        form.addRow(tr('Строк'), self.rows_spin)
        form.addRow(tr('Столбцов'), self.columns_spin)
        form.addRow(tr('Строк заголовка'), self.header_rows_spin)
        form.addRow(tr('Столбцов с названиями'), self.row_labels_spin)
        if self.mode == "template":
            self.saved_template_select.hide()
            label = form.labelForField(self.saved_template_select)
            if label:
                label.hide()
        layout.addLayout(form)
        layout.addWidget(self.crossed)
        self.auto_fit_rows = QCheckBox(tr('Автоматически подгонять строки шаблона'))
        self.auto_fit_rows.setToolTip(tr('Для регулярных таблиц: определяет число строк, переносит правила и поля. Столбцы должны совпадать.'))
        self.auto_fit_rows.toggled.connect(self._save_common)
        layout.addWidget(self.auto_fit_rows)
        fit_grid = QPushButton(tr('Подогнать шаблон к таблице'))
        fit_grid.clicked.connect(self.fit_current_grid)
        layout.addWidget(fit_grid)
        layout.addWidget(self.high_accuracy)
        self.slow_mode = QCheckBox(tr('Slow mode — дополнительная перепроверка'))
        self.slow_mode.setToolTip(tr('Две дополнительные модели перепроверяют спорные измерения. Время зависит от компьютера и размера таблицы; на CPU проверка может быть длительной. Всё работает локально; исправления остаются доступными для проверки.'))
        self.slow_mode.toggled.connect(lambda checked: self.high_accuracy.setChecked(True) if checked else None)
        self.high_accuracy.toggled.connect(lambda checked: self.slow_mode.setChecked(False) if not checked else None)
        layout.addWidget(self.slow_mode)
        self.adaptive = QCheckBox(tr('Adaptive analysis (experimental)'))
        self.adaptive.setToolTip(tr('Short analysis when models agree; complete analysis otherwise. Short-pass values still require review.'))
        self.adaptive.toggled.connect(lambda checked: self.high_accuracy.setChecked(True) if checked else None)
        self.high_accuracy.toggled.connect(lambda checked: self.adaptive.setChecked(False) if not checked else None)
        layout.addWidget(self.adaptive)
        accuracy_note = QLabel(tr('Рекомендуется для ячеек с форматом «Число». Для 100 ячеек на современном компьютере обычно требуется несколько минут; программа тратит дополнительное время на альтернативы и проверки.'))
        accuracy_note.setWordWrap(True)
        set_theme_style(accuracy_note, fmt('color: {p0};', p0=MUTED))
        accuracy_note.hide()
        help_label = QLabel(tr('Проверьте каждую синюю направляющую. Если заголовки находятся НАД таблицей, укажите 0 строк заголовка. Направляющие можно перетаскивать мышью.'))
        help_label.setWordWrap(True)
        set_theme_style(help_label, fmt('color: {p0};', p0=MUTED))
        help_label.hide()
        self.grid_warning = QLabel("")
        self.grid_warning.setWordWrap(True)
        set_theme_style(self.grid_warning, fmt('color: {p0};', p0=AMBER))
        layout.addWidget(self.grid_warning)
        detect = QPushButton(tr('Найти сетку заново'))
        detect.clicked.connect(self.detect_again)
        load_saved = QPushButton(tr('Применить выбранный шаблон'))
        load_saved.clicked.connect(self._request_saved_template)
        redraw = QPushButton(tr('Перерисовать границу таблицы'))
        redraw.clicked.connect(lambda: self.canvas.begin_draw("table"))
        layout.addWidget(load_saved)
        settings_note = QLabel(tr('Распознавание использует текущие настройки. Сохранённый шаблон — только основа.'))
        settings_note.setWordWrap(True)
        layout.addWidget(settings_note)
        layout.addWidget(detect)
        layout.addWidget(redraw)
        save_as_template = QPushButton(tr('Сохранить шаблон'))
        save_as_template.clicked.connect(self._save_current_template)
        save_as_template.setVisible(self.mode != "template")
        layout.addWidget(save_as_template)
        layout.addStretch()
        self.continue_button = QPushButton(tr('Сохранить сетку и продолжить'))
        self.continue_button.setProperty("primary", True)
        self.continue_button.clicked.connect(self._continue)
        self.continue_button.setVisible(self.mode != "template")
        layout.addWidget(self.continue_button)
        if self.mode == "template":
            self.high_accuracy.hide(); accuracy_note.hide(); load_saved.hide()
            self.slow_mode.hide()
            self.adaptive.hide()
        self.rows_spin.setKeyboardTracking(False)
        self.columns_spin.setKeyboardTracking(False)
        self.rows_spin.valueChanged.connect(self._grid_count_changed)
        self.columns_spin.valueChanged.connect(self._grid_count_changed)
        self.template_name.editingFinished.connect(self._save_common)
        self.template_name.textEdited.connect(self._save_common)
        self.header_rows_spin.valueChanged.connect(self._save_common)
        self.row_labels_spin.valueChanged.connect(self._save_common)
        self.crossed.toggled.connect(self._save_common)
        return tab

    def set_recognition_running(self, running: bool) -> None:
        self.continue_button.setEnabled(not running)
        self.continue_button.setText(tr('Analysis queued or running') if running else tr('Add to analysis queue'))

    def set_saved_templates(self, templates: list[TableTemplate]) -> None:
        from .template_matcher import latest_template_versions
        templates = latest_template_versions(templates)
        selected_id = self.template.id if self.template else self.saved_template_select.currentData()
        self.saved_template_select.blockSignals(True)
        self.saved_template_select.clear()
        self.saved_template_select.addItem(tr('Выберите сохранённый шаблон…'), "")
        for template in templates:
            self.saved_template_select.addItem(template.name, template.id)
        if selected_id:
            index = self.saved_template_select.findData(selected_id)
            self.saved_template_select.setCurrentIndex(max(0, index))
        self.saved_template_select.blockSignals(False)

    def _request_saved_template(self) -> None:
        template_id = self.saved_template_select.currentData()
        if template_id:
            self.savedTemplateRequested.emit(str(template_id))

    def _build_fields_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 18, 8, 8)
        buttons = QHBoxLayout()
        add = QPushButton(tr('+ Добавить область'))
        add.clicked.connect(lambda: self.canvas.begin_draw("field"))
        suggest = QPushButton(tr('Предложить автоматически'))
        suggest.clicked.connect(self.suggest_fields)
        buttons.addWidget(add)
        buttons.addWidget(suggest)
        layout.addLayout(buttons)
        self.field_list = QListWidget()
        self.field_list.currentRowChanged.connect(self._field_selected)
        layout.addWidget(self.field_list, 1)

        form = QFormLayout()
        self.field_name = QLineEdit()
        self.field_kind = QComboBox()
        for label, code in ((tr('Текст'), "text"), (tr('Дата'), "date"), (tr('Целое число'), "integer"), (tr('Десятичное число'), "numeric"), (tr('Сложная запись'), "complex_numeric")):
            self.field_kind.addItem(label, code)
        self.field_recognition = QComboBox()
        for label, code in ((tr('Печатный текст'), "printed"), (tr('Рукописный текст'), "handwritten"), (tr('Число'), "numeric")):
            self.field_recognition.addItem(label, code)
        self.field_source = QComboBox()
        self.field_source.addItem(tr('Распознать'), "ocr")
        self.field_source.addItem(tr('Постоянное значение'), "fixed")
        self.field_fixed = QLineEdit()
        self.field_export = QComboBox()
        self.field_export.addItem(tr('Повторить для каждой строки'), "repeat")
        self.field_export.addItem(tr('Распределить по группе столбцов'), "column_group")
        self.field_required = QCheckBox()
        self.field_column_start = QSpinBox()
        self.field_column_end = QSpinBox()
        form.addRow(tr('Название поля'), self.field_name)
        form.addRow(tr('Тип значения'), self.field_kind)
        form.addRow(tr('Режим OCR'), self.field_recognition)
        form.addRow(tr('Источник'), self.field_source)
        form.addRow(tr('Постоянное значение'), self.field_fixed)
        form.addRow(tr('Экспортировать'), self.field_export)
        form.addRow(tr('Обязательное поле'), self.field_required)
        form.addRow(tr('Первый столбец'), self.field_column_start)
        form.addRow(tr('Последний столбец'), self.field_column_end)
        layout.addLayout(form)
        only_label = QLabel(tr('Распознаются только основная таблица и отмеченные области. Всё за их пределами игнорируется.'))
        only_label.setWordWrap(True)
        set_theme_style(only_label, fmt('color: {p0};', p0=MUTED))
        only_label.hide()
        actions = QHBoxLayout()
        redraw = QPushButton(tr('Перерисовать область'))
        redraw.clicked.connect(self.redraw_selected_field)
        delete = QPushButton(tr('Удалить поле'))
        delete.setProperty("danger", True)
        delete.clicked.connect(self.delete_field)
        save = QPushButton(tr('Сохранить поле'))
        save.setProperty("primary", True)
        save.clicked.connect(self.save_field)
        actions.addWidget(redraw)
        actions.addWidget(delete)
        actions.addWidget(save)
        layout.addLayout(actions)
        self.field_list.setMinimumHeight(120)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(tab)
        return scroll

    def _build_columns_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 18, 8, 8)
        form = QFormLayout()
        self.column_select = QComboBox()
        self.column_select.currentIndexChanged.connect(self._column_selected)
        self.column_name = QLineEdit()
        self.column_role = QComboBox()
        for label, code in ((tr('Данные'), "data"), (tr('Заголовок'), "header"), (tr('Название строки'), "row_label"), (tr('Игнорировать'), "ignored")):
            self.column_role.addItem(label, code)
        self._column_constraints = ValueConstraints()
        self.column_name.textEdited.connect(self._column_identity_edited)
        self.column_role.activated.connect(self._column_identity_edited)
        form.addRow(tr('Выбранный столбец'), self.column_select)
        form.addRow(tr('Название столбца'), self.column_name)
        form.addRow(tr('Назначение'), self.column_role)
        layout.addLayout(form)
        self.column_rule_summary = QLabel(); self.column_rule_summary.setWordWrap(True)
        layout.addWidget(self.column_rule_summary)
        configure = QPushButton(tr('Формат и допустимые значения…'))
        configure.clicked.connect(self._configure_column)
        layout.addWidget(configure)
        note = QLabel(tr('Правило столбца применяется к строкам данных. Выделение на вкладке «Правила ячеек» имеет приоритет и может охватывать одну ячейку, строку или блок.'))
        note.setWordWrap(True)
        set_theme_style(note, fmt('color: {p0};', p0=MUTED))
        note.hide()
        save = QPushButton(tr('Сохранить правила столбца'))
        save.setProperty("primary", True)
        save.clicked.connect(self.save_column)
        layout.addWidget(save)
        return tab

    def _build_cell_rules_tab(self) -> QWidget:
        tab = QWidget(); layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 16, 8, 8); layout.setSpacing(6)
        help_text = QLabel(tr('Щёлкните ячейку или протяните мышью по таблице. Shift расширяет диапазон; Ctrl/Cmd добавляет или убирает ячейки. Заголовок выбирает строку или столбец.'))
        help_text.setWordWrap(True); set_theme_style(help_text, fmt('color: {p0};', p0=MUTED))
        self.canvas.setToolTip(help_text.text())
        help_text.hide()

        selection_row = QHBoxLayout()
        self.selection_label = QLabel(tr('Ячейки не выбраны'))
        set_theme_style(self.selection_label, "font-weight: 700;")
        select_all = QPushButton(tr('Все измерения'))
        select_all.clicked.connect(self._rule_all_data)
        clear = QPushButton(tr('Снять'))
        clear.clicked.connect(lambda: self.canvas.select_cells(set()))
        undo = QToolButton(text="↶"); undo.setToolTip(tr('Отменить последнее выделение')); undo.clicked.connect(self.canvas.undo_selection)
        redo = QToolButton(text="↷"); redo.setToolTip(tr('Повторить выделение')); redo.clicked.connect(self.canvas.redo_selection)
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)
        selection_row.addWidget(select_all, 1); selection_row.addWidget(clear)
        selection_row.addWidget(undo); selection_row.addWidget(redo)
        layout.addLayout(selection_row)

        form = QFormLayout(); form.setSpacing(12)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.quick_rule_name = QLineEdit("Measurements")
        self.quick_kind = QComboBox()
        for text, code in ((tr('Десятичное число'), "numeric"), (tr('Целое число'), "integer"), (tr('Текст'), "text"), (tr('Дата'), "date"), (tr('Сложная запись'), "complex_numeric")):
            self.quick_kind.addItem(text, code)
        self.quick_places = QSpinBox(); self.quick_places.setRange(-1, 8); self.quick_places.setSpecialValueText(tr('Любое')); self.quick_places.setValue(1)
        self.quick_minimum = QLineEdit("0"); self.quick_maximum = QLineEdit("60")
        range_box = QWidget(); range_layout = QHBoxLayout(range_box); range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.addWidget(self.quick_minimum); range_layout.addWidget(QLabel("—")); range_layout.addWidget(self.quick_maximum)
        self.quick_require_decimal = QCheckBox(tr('Десятичная часть обязательна')); self.quick_require_decimal.setChecked(True)
        self.quick_allow_empty = QCheckBox(tr('Ячейка может быть пустой')); self.quick_allow_empty.setChecked(True)
        self.quick_color = QComboBox()
        for label, color in ((tr('Индиго'), "#4F46E5"), (tr('Бирюзовый'), "#0F766E"), (tr('Зелёный'), "#15803D"), (tr('Оранжевый'), "#C26B16"), (tr('Малиновый'), "#BE185D")):
            self.quick_color.addItem(label, color)
        form.addRow(tr('Название'), self.quick_rule_name)
        form.addRow(tr('Тип значения'), self.quick_kind)
        form.addRow(tr('Знаков после точки'), self.quick_places)
        form.addRow(tr('Разрешено от / до'), range_box)
        form.addRow(self.quick_require_decimal)
        form.addRow(self.quick_allow_empty)
        form.addRow(tr('Цвет области'), self.quick_color)
        layout.addLayout(form)
        self.quick_explanation = QLabel("")
        self.quick_explanation.setWordWrap(True)
        set_theme_style(self.quick_explanation, fmt('background: {p0}; color: {p1}; padding: 9px; border-radius: 6px;', p0=LIGHT_BLUE, p1=TEXT))
        layout.addWidget(self.quick_explanation)
        self.rule_edit_status = QLabel(tr('Изменения правил применяются сразу к выбранной области.'))
        self.rule_edit_status.setWordWrap(True)
        layout.addWidget(self.rule_edit_status)
        for widget in (self.quick_minimum, self.quick_maximum): widget.textChanged.connect(self._quick_preview)
        self.quick_places.valueChanged.connect(self._quick_preview)
        self.quick_kind.currentIndexChanged.connect(self._quick_kind_changed)
        self.quick_require_decimal.toggled.connect(self._quick_preview)
        for widget in (self.quick_rule_name, self.quick_minimum, self.quick_maximum):
            widget.textChanged.connect(self._quick_edited)
        self.quick_places.valueChanged.connect(self._quick_edited)
        self.quick_kind.currentIndexChanged.connect(self._quick_edited)
        self.quick_color.currentIndexChanged.connect(self._quick_edited)
        self.quick_require_decimal.toggled.connect(self._quick_edited)
        self.quick_allow_empty.toggled.connect(self._quick_edited)

        actions = QHBoxLayout()
        apply_rule = QPushButton(tr('Применить к выделению')); apply_rule.setProperty("primary", True); apply_rule.clicked.connect(self._apply_quick_rule)
        advanced = QPushButton(tr('Дополнительно…')); advanced.clicked.connect(self._advanced_for_selection)
        self.apply_rule_button = apply_rule
        self.advanced_rule_button = advanced
        apply_rule.setEnabled(False); advanced.setEnabled(False)
        actions.addWidget(apply_rule, 1); actions.addWidget(advanced); layout.addLayout(actions)

        rules_heading = QLabel(tr('Области шаблона')); set_theme_style(rules_heading, "font-weight: 700;")
        layout.addWidget(rules_heading)
        self.rule_list = QListWidget(); self.rule_list.setMaximumHeight(150); self.rule_list.currentRowChanged.connect(self._rule_selected)
        layout.addWidget(self.rule_list)
        self.rule_summary = QLabel(); self.rule_summary.setWordWrap(True); set_theme_style(self.rule_summary, fmt('color: {p0};', p0=MUTED))
        layout.addWidget(self.rule_summary)
        actions = QHBoxLayout()
        edit = QPushButton(tr('Изменить…')); edit.clicked.connect(self._edit_selected_rule)
        remove = QPushButton(tr('Удалить правило')); remove.clicked.connect(self._remove_selected_rule)
        actions.addWidget(edit); actions.addWidget(remove); layout.addLayout(actions)
        column_heading = QGroupBox(tr('Column defaults'))
        column_heading.setCheckable(True)
        column_heading.setChecked(False)
        column_layout = QVBoxLayout(column_heading)
        column_content = self._build_columns_tab()
        column_layout.addWidget(column_content)
        column_content.hide()
        column_heading.toggled.connect(column_content.setVisible)
        layout.addWidget(column_heading)
        layout.addStretch()
        self._quick_preview()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(tab)
        return scroll

    def _selection_changed(self, cells: set[tuple[int, int]]) -> None:
        if self._quick_dirty and not self._quick_pending_cells and cells:
            self._quick_pending_cells = set(cells)
            self._commit_quick_edits()
        if self._quick_dirty and self._quick_pending_cells and not self._loading_form and cells != self._quick_pending_cells:
            self.canvas.select_cells(set(self._quick_pending_cells))
            return
        self._selected_cells = set(cells)
        self.apply_rule_button.setEnabled(bool(cells))
        self.advanced_rule_button.setEnabled(bool(cells))
        if not cells:
            self.selection_label.setText(tr('Ячейки не выбраны'))
            return
        rows = [cell[0] for cell in cells]; columns = [cell[1] for cell in cells]
        start = fmt('{p0}{p1}', p0=excel_column_name(min(columns)), p1=min(rows) + 1)
        end = fmt('{p0}{p1}', p0=excel_column_name(max(columns)), p1=max(rows) + 1)
        rectangular = len(cells) == (max(rows) - min(rows) + 1) * (max(columns) - min(columns) + 1)
        address = start if start == end else fmt('{p0}:{p1}', p0=start, p1=end)
        self.selection_label.setText(address if rectangular else tr('{p0} ячеек', p0=len(cells)))

    def _quick_kind_changed(self) -> None:
        loading = self._loading_quick_rule
        self._loading_quick_rule = True
        kind = self.quick_kind.currentData()
        numeric = kind == "numeric"
        for widget in (self.quick_places, self.quick_require_decimal):
            widget.setEnabled(numeric)
        if kind == "integer":
            self.quick_places.setValue(0)
            self.quick_require_decimal.setChecked(False)
        elif kind not in {"numeric", "integer"}:
            self.quick_places.setValue(-1)
            self.quick_require_decimal.setChecked(False)
            self.quick_minimum.clear(); self.quick_maximum.clear()
        self._loading_quick_rule = loading
        self._quick_preview()

    def _quick_targets(self) -> set[tuple[int, int]]:
        if self._quick_dirty:
            return set(self._quick_pending_cells)
        if self._selected_cells:
            return set(self._selected_cells)
        index = self.rule_list.currentRow() if hasattr(self, "rule_list") else -1
        if self.template and 0 <= index < len(self.template.cell_rules):
            r = self.template.cell_rules[index]
            return {(row, col) for row in range(r.row_start, r.row_end + 1)
                    for col in range(r.column_start, r.column_end + 1)}
        return set()

    def _read_quick_rule(self) -> ValueConstraints:
        values = {}
        if self.template:
            rectangles = self._selection_rectangles(self._quick_targets())
            if len(rectangles) == 1:
                for r in reversed(self.template.cell_rules):
                    if (r.row_start, r.row_end, r.column_start, r.column_end) == rectangles[0]:
                        values = asdict(r.constraints)
                        break
        values.update(
            value_format=str(self.quick_kind.currentData()),
            minimum=optional_number(self.quick_minimum.text()),
            maximum=optional_number(self.quick_maximum.text()),
            decimal_places=None if self.quick_places.value() < 0 else self.quick_places.value(),
            allow_empty=self.quick_allow_empty.isChecked(),
            require_decimal=self.quick_require_decimal.isChecked(),
        )
        return ValueConstraints(**values)

    def _quick_edited(self, *_args) -> None:
        if self._loading_quick_rule or self._loading_form or not self.template:
            return
        if not self._quick_dirty:
            self._quick_pending_cells = self._quick_targets()
        self._quick_dirty = True
        self._commit_quick_edits()

    def _commit_quick_edits(self) -> bool:
        if not self._quick_dirty:
            return True
        targets = self._quick_targets()
        try:
            if not targets:
                raise ValueError(tr('Сначала выберите ячейку или диапазон на таблице.'))
            rule = self._read_quick_rule()
            rule.validate()
        except (ValueError, TypeError) as exc:
            self._quick_error = str(exc)
            self.rule_edit_status.setText(tr('Правило не применено: {p0}', p0=exc))
            return False
        name = self.quick_rule_name.text().strip() or "Values"
        for bounds in self._selection_rectangles(targets):
            matching = next((r for r in reversed(self.template.cell_rules)
                             if (r.row_start, r.row_end, r.column_start, r.column_end) == bounds), None)
            if matching:
                matching.name = name
                matching.constraints = ValueConstraints(**asdict(rule))
                matching.color = str(self.quick_color.currentData())
            else:
                self.template.cell_rules.append(CellRuleRegion(
                    str(uuid4()), name, *bounds, constraints=ValueConstraints(**asdict(rule)),
                    color=str(self.quick_color.currentData())))
        self.template.schema_version = max(3, self.template.schema_version)
        # Refresh labels without reloading the form or moving its text cursor.
        self.rule_list.blockSignals(True)
        while self.rule_list.count() < len(self.template.cell_rules):
            self.rule_list.addItem("")
        for i, region in enumerate(self.template.cell_rules):
            self.rule_list.item(i).setText(fmt('{p0}. {p1}\n{p2}', p0=i + 1, p1=localized_rule_name(region.name), p2=region.address()))
        self.rule_list.blockSignals(False)
        self._quick_dirty = False
        self._quick_error = ""
        self.rule_summary.setText(rule.summary())
        self.rule_edit_status.setText(tr('Текущие правила применены к документу.'))
        self.canvas.redraw()
        self.templateChanged.emit(self.template)
        return True

    def prepare_current_settings(self) -> bool:
        self.rows_spin.interpretText()
        self.columns_spin.interpretText()
        self.quick_places.interpretText()
        self.header_rows_spin.interpretText()
        self.row_labels_spin.interpretText()
        if not self._commit_quick_edits():
            QMessageBox.warning(self, tr('Проверьте правила'), self._quick_error)
            return False
        self._save_common()
        return True

    def _save_current_template(self) -> None:
        if self.template and self.prepare_current_settings():
            self.saveTemplateRequested.emit(self.template)

    def _quick_preview(self) -> None:
        if not hasattr(self, "quick_kind"):
            return
        try:
            rule = self._read_quick_rule(); rule.validate()
            examples = []
            for raw in ("135", "3417", "99.8"):
                reading = constrain_reading(OcrValue(raw, .5, candidates=[]), rule)
                if reading.text != raw and not rule.hard_errors(reading.text):
                    examples.append(fmt('{p0} → {p1}', p0=raw, p1=reading.text))
                elif rule.hard_errors(raw):
                    examples.append(tr('{p0} запрещено', p0=raw))
            message = tr('Точка и запятая будут сохранены как точка.')
            if examples:
                message += tr(' По этому правилу: ') + join_text('; ', examples) + "."
            self.quick_explanation.setText(join_text("; ", examples))
            self.quick_explanation.setVisible(bool(examples) and rule.value_format in {"numeric", "integer"})
            set_theme_style(self.quick_explanation, fmt('background: {p0}; color: {p1}; padding: 9px; border-radius: 6px;', p0=LIGHT_BLUE, p1=TEXT))
        except (ValueError, TypeError) as exc:
            self.quick_explanation.show()
            self.quick_explanation.setText(tr('Проверьте правило: {p0}', p0=exc))
            set_theme_style(self.quick_explanation, "background: #FEF2F2; color: #B91C1C; padding: 9px; border-radius: 6px;")

    @staticmethod
    def _selection_rectangles(cells: set[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
        """Compress an arbitrary Excel-like selection into rectangular rules."""
        runs_by_row: dict[int, list[tuple[int, int]]] = {}
        for row in sorted({item[0] for item in cells}):
            columns = sorted(column for candidate_row, column in cells if candidate_row == row)
            runs: list[tuple[int, int]] = []
            for column in columns:
                if not runs or column > runs[-1][1] + 1:
                    runs.append((column, column))
                else:
                    runs[-1] = (runs[-1][0], column)
            runs_by_row[row] = runs
        active: dict[tuple[int, int], tuple[int, int]] = {}
        rectangles: list[tuple[int, int, int, int]] = []
        for row in sorted(runs_by_row):
            current = set(runs_by_row[row])
            for run, (start, end) in list(active.items()):
                if run not in current or row != end + 1:
                    rectangles.append((start, end, run[0], run[1])); del active[run]
            for run in current:
                start, end = active.get(run, (row, row))
                active[run] = (start, row if row == end + 1 else end)
        for run, (start, end) in active.items():
            rectangles.append((start, end, run[0], run[1]))
        return rectangles

    def _append_rule_regions(self, name: str, rule: ValueConstraints, color: str,
                             *, selection: tuple[int, int, int, int] | None = None) -> None:
        if not self.template or not self._selected_cells:
            QMessageBox.information(self, tr('Выделение'), tr('Сначала выберите ячейку или диапазон на таблице.'))
            return
        rectangles = [selection] if selection is not None else self._selection_rectangles(self._selected_cells)
        for row_start, row_end, column_start, column_end in rectangles:
            self.template.cell_rules = [
                existing for existing in self.template.cell_rules
                if (existing.row_start, existing.row_end, existing.column_start, existing.column_end)
                != (row_start, row_end, column_start, column_end)
            ]
            self.template.cell_rules.append(CellRuleRegion(
                str(uuid4()), str(name), row_start, row_end, column_start, column_end,
                constraints=ValueConstraints(**asdict(rule)), color=color,
            ))
        notify(self, tr("Rule applied"))
        self.template.schema_version = 3
        self._refresh_cell_rules()
        self.templateChanged.emit(self.template)

    def _apply_quick_rule(self) -> None:
        try:
            rule = self._read_quick_rule(); rule.validate()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, tr('Некорректное правило'), str(exc)); return
        self._append_rule_regions(
            self.quick_rule_name.text().strip() or "Values", rule,
            str(self.quick_color.currentData()),
        )

    def _advanced_for_selection(self) -> None:
        if not self.template or not self._selected_cells:
            QMessageBox.information(self, tr('Выделение'), tr('Сначала выберите ячейку или диапазон на таблице.')); return
        rows = [cell[0] for cell in self._selected_cells]; columns = [cell[1] for cell in self._selected_cells]
        try:
            initial = self._read_quick_rule(); initial.validate()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, tr('Некорректное правило'), str(exc)); return
        region = CellRuleRegion(
            str(uuid4()), str(self.quick_rule_name.text().strip() or "Values"),
            min(rows), max(rows), min(columns), max(columns), initial,
            str(self.quick_color.currentData()),
        )
        dialog = ValueRuleDialog(initial, self, title=tr('Расширенное правило'), region=region, rows=self.template.rows, columns=self.template.columns)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.quick_rule_name.setText(dialog.region_name.text().strip() or "Values")
            self._append_rule_regions(self.quick_rule_name.text(), dialog.rule, str(self.quick_color.currentData()),
                                      selection=dialog.selection)

    def _refresh_cell_rules(self) -> None:
        self.rule_list.blockSignals(True); self.rule_list.clear()
        if self.template:
            for index, region in enumerate(self.template.cell_rules):
                outside = region.row_end >= self.template.rows or region.column_end >= self.template.columns
                self.rule_list.addItem(fmt('{p0}. {p1}\n{p2}', p0=index + 1, p1=localized_rule_name(region.name), p2=region.address()) + (tr(' — вне сетки!') if outside else ""))
        self.rule_list.blockSignals(False)
        self.rule_list.setCurrentRow(self.rule_list.count() - 1)
        self._rule_selected(self.rule_list.currentRow())

    def _rule_selected(self, index: int) -> None:
        if self._quick_dirty and not self._loading_form and not self._commit_quick_edits():
            previous = next((i for i, r in enumerate(self.template.cell_rules)
                             if r.id == self._quick_active_rule_id), -1)
            self.rule_list.blockSignals(True)
            self.rule_list.setCurrentRow(previous)
            self.rule_list.blockSignals(False)
            return
        self._loading_quick_rule = True
        self._quick_dirty = False
        self.canvas.active_rule = index
        if self.template and 0 <= index < len(self.template.cell_rules):
            region = self.template.cell_rules[index]
            self._quick_active_rule_id = region.id
            self.rule_summary.setText(region.constraints.summary())
            self.canvas.select_region(region.row_start, region.row_end, region.column_start, region.column_end)
            self.quick_rule_name.setText(localized_rule_name(region.name))
            self.quick_kind.setCurrentIndex(max(0, self.quick_kind.findData(region.constraints.value_format)))
            self.quick_places.setValue(-1 if region.constraints.decimal_places is None else region.constraints.decimal_places)
            self.quick_minimum.setText("" if region.constraints.minimum is None else str(region.constraints.minimum))
            self.quick_maximum.setText("" if region.constraints.maximum is None else str(region.constraints.maximum))
            self.quick_require_decimal.setChecked(region.constraints.require_decimal)
            self.quick_allow_empty.setChecked(region.constraints.allow_empty)
            color_index = self.quick_color.findData(region.color)
            if color_index >= 0:
                self.quick_color.setCurrentIndex(color_index)
        else:
            self.rule_summary.clear()
        self._loading_quick_rule = False
        self.canvas.redraw()

    def _edit_rule(self, region: CellRuleRegion, *, existing: bool = False) -> None:
        if not self.template:
            return
        dialog = ValueRuleDialog(region.constraints, self, title=tr('Правило для выбранных ячеек'), region=region, rows=self.template.rows, columns=self.template.columns)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = CellRuleRegion(region.id, str(dialog.region_name.text().strip() or "Values"), *dialog.selection, constraints=dialog.rule, color=region.color)
        if existing:
            self.template.cell_rules = [r for r in self.template.cell_rules if r.id != region.id]
        self.template.cell_rules.append(updated)
        self.template.schema_version = 3
        self._refresh_cell_rules(); self.templateChanged.emit(self.template)

    def _rule_all_data(self) -> None:
        if self.template:
            self.canvas.select_all_data()

    def _rule_axis(self, axis: str) -> None:
        if not self.template:
            return
        t = self.template
        value, accepted = QInputDialog.getInt(self, tr('Выбор'), tr('Номер строки') if axis == "row" else tr('Номер столбца'), 1, 1, t.rows if axis == "row" else t.columns)
        if accepted:
            rs, re, cs, ce = (value - 1, value - 1, 0, t.columns - 1) if axis == "row" else (t.header_rows, t.rows - 1, value - 1, value - 1)
            self._edit_rule(CellRuleRegion(str(uuid4()), ("Row" if axis == "row" else "Column"), rs, re, cs, ce))

    def _edit_selected_rule(self) -> None:
        index = self.rule_list.currentRow()
        if self.template and 0 <= index < len(self.template.cell_rules):
            self._edit_rule(self.template.cell_rules[index], existing=True)

    def _remove_selected_rule(self) -> None:
        index = self.rule_list.currentRow()
        if self.template and 0 <= index < len(self.template.cell_rules):
            self.template.cell_rules.pop(index)
            self._refresh_cell_rules(); self.templateChanged.emit(self.template)
            notify(self, tr("Rule deleted"))

    def _configure_column(self) -> None:
        dialog = ValueRuleDialog(self._column_constraints, self, title=tr('Правило всего столбца'))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._column_constraints = dialog.rule
            self.save_column()

    def set_document(self, image: np.ndarray, template: TableTemplate) -> None:
        self._quick_dirty = False
        self._quick_pending_cells = set()
        self._quick_error = ""
        self.image = image
        self.template = template
        resized_rules = template.resize_grid(template.rows, template.columns)
        self.grid_warning.setText(tr('Диапазоны правил обновлены: {p0}', p0='; '.join(resized_rules)) if resized_rules else '')
        self.version_label.clear()
        saved_index = self.saved_template_select.findData(template.id)
        self.saved_template_select.setCurrentIndex(max(0, saved_index))
        template.ensure_column_rules()
        self._loading_form = True
        self.template_name.setText(template.name)
        self.rows_spin.setValue(template.rows)
        self.columns_spin.setValue(template.columns)
        self.header_rows_spin.setValue(template.header_rows)
        self.row_labels_spin.setValue(template.row_label_columns)
        self.crossed.setChecked(template.detect_crossed_rows)
        self.auto_fit_rows.setChecked(template.auto_fit_rows)
        self._loading_form = False
        self._refresh_fields()
        self._refresh_columns()
        self._refresh_cell_rules()
        self.canvas.show_grid = True
        self.canvas.show_fields = False
        self.canvas.show_rules = False
        self.canvas.set_document(image, template)
        self.exclusions_panel.refresh(reset_history=True)
        self._selection_changed(set())
        self._tab_changed(self.tabs.currentIndex())

    def _fit_canvas(self) -> None:
        self.canvas.resetTransform()
        self.canvas.fit_document()

    def _exclusions_changed(self, template) -> None:
        self._refresh_columns()
        self.templateChanged.emit(template)

    def _tab_changed(self, index: int) -> None:
        self.canvas.set_active_field(self.field_list.currentRow() if index == 1 else -1)
        self.canvas.show_grid = index == 0
        self.canvas.show_fields = index == 1
        self.canvas.show_rules = index == 2
        self.canvas.set_selection_enabled(index in {2, 3, 4})
        self.fixed_panel.refresh()
        self.exclusions_panel.refresh()

    def _save_common(self) -> None:
        if self._loading_form or not self.template:
            return
        self.template.name = str(self.template_name.text().strip() or "New template")
        self.template.header_rows = min(self.header_rows_spin.value(), max(0, self.template.rows - 1))
        self.template.row_label_columns = min(self.row_labels_spin.value(), max(0, self.template.columns - 1))
        self.template.detect_crossed_rows = self.crossed.isChecked()
        self.template.auto_fit_rows = self.auto_fit_rows.isChecked()
        self.template.ensure_column_rules()
        for index in range(min(self.template.row_label_columns, len(self.template.column_rules))):
            self.template.column_rules[index].role = "row_label"
        self.templateChanged.emit(self.template)

    def _grid_count_changed(self) -> None:
        if self._loading_form or not self.template:
            return
        if not self._commit_quick_edits():
            self._loading_form = True
            self.rows_spin.setValue(self.template.rows)
            self.columns_spin.setValue(self.template.columns)
            self._loading_form = False
            QMessageBox.warning(self, tr('Проверьте правила'), self._quick_error)
            return
        changes = self.template.resize_grid(self.rows_spin.value(), self.columns_spin.value())
        self._loading_form = True
        self.header_rows_spin.setValue(self.template.header_rows)
        self.row_labels_spin.setValue(self.template.row_label_columns)
        self._loading_form = False
        self._selected_cells = {(r, c) for r, c in self._selected_cells
                                if r < self.template.rows and c < self.template.columns}
        self.canvas.select_cells(self._selected_cells)
        self._refresh_columns()
        self._refresh_cell_rules()
        if changes:
            self.grid_warning.setText(tr('Диапазоны правил обновлены: {p0}', p0=join_text('; ', changes)))
        self.canvas.redraw()
        self.exclusions_panel.refresh(reset_history=True)
        self.templateChanged.emit(self.template)

    def fit_current_grid(self) -> None:
        if self.image is None or self.template is None or not self.prepare_current_settings():
            return
        try:
            fitted = fit_template(self.template, detect_grid(self.image), self.image.shape[1]/self.image.shape[0])
        except ValueError as exc:
            QMessageBox.warning(self, tr('Подгонка требует проверки'), str(exc))
            return
        self.set_document(self.image, fitted)
        self.grid_warning.setText(tr('Подгонка выполнена: {p0} строк данных. Проверьте сетку и поля.', p0=fitted.rows-fitted.header_rows))
        self.templateChanged.emit(fitted)

    def detect_again(self) -> None:
        if self.image is None:
            return
        try:
            detection = detect_grid(self.image)
        except ValueError as exc:
            QMessageBox.warning(self, tr('Сетка не найдена'), str(exc))
            return
        changes = self.template.resize_grid(len(detection.row_guides) - 1, len(detection.column_guides) - 1)
        self.template.table_rect = detection.table_rect
        self.template.row_guides = detection.row_guides
        self.template.column_guides = detection.column_guides
        self.template.ensure_column_rules()
        self.set_document(self.image, self.template)
        notices = list(detection.warnings)
        if changes:
            notices.append(tr('Диапазоны правил обновлены: {p0}', p0=join_text('; ', changes)))
        self.grid_warning.setText(join_text(' ', notices))
        self.templateChanged.emit(self.template)
        notify(self, tr("Grid detected"))

    def _region_created(self, kind: str, rect: QRectF) -> None:
        if self.image is None or not self.template:
            return
        normalized = NormalizedRect.from_pixels(
            (round(rect.x()), round(rect.y()), round(rect.width()), round(rect.height())), self.image.shape
        )
        if kind == "cell_rule":
            t = self.template
            x1, x2 = max(normalized.x, t.column_guides[0]), min(normalized.x + normalized.width, t.column_guides[-1])
            y1, y2 = max(normalized.y, t.row_guides[0]), min(normalized.y + normalized.height, t.row_guides[-1])
            if x1 >= x2 or y1 >= y2:
                QMessageBox.information(self, tr('Выделение'), tr('Выделите область внутри таблицы.')); return
            rs, re = bisect_right(t.row_guides, y1) - 1, bisect_right(t.row_guides, y2 - 1e-9) - 1
            cs, ce = bisect_right(t.column_guides, x1) - 1, bisect_right(t.column_guides, x2 - 1e-9) - 1
            self._edit_rule(CellRuleRegion(str(uuid4()), str("Values"), rs, re, cs, ce))
            return
        if kind == "table":
            self.template.table_rect = normalized
            self.template.row_guides, self.template.column_guides = evenly_spaced_guides(
                normalized, self.rows_spin.value(), self.columns_spin.value()
            )
            self.canvas.redraw()
            self.templateChanged.emit(self.template)
            return
        if kind == "replace_field":
            index = self.field_list.currentRow()
            if 0 <= index < len(self.template.fields):
                self.template.fields[index].rect = normalized
                self.canvas.redraw()
                self.templateChanged.emit(self.template)
            return
        region = FieldRegion(str(uuid4()), f"Field {len(self.template.fields) + 1}", normalized)
        self.template.fields.append(region)
        self._refresh_fields()
        self.field_list.setCurrentRow(len(self.template.fields) - 1)
        self.canvas.redraw()
        self.templateChanged.emit(self.template)

    def _refresh_fields(self) -> None:
        self.field_list.clear()
        if not self.template:
            return
        for region in self.template.fields:
            value = fmt(' — {p0}', p0=region.fixed_value) if region.source == "fixed" and region.fixed_value else ""
            self.field_list.addItem(fmt('{p0}{p1}', p0=localized_rule_name(region.name), p1=value))
        maximum = max(0, self.template.columns - 1)
        self.field_column_start.setRange(0, maximum)
        self.field_column_end.setRange(0, maximum)

    def _field_selected(self, index: int) -> None:
        if not self.template or index < 0 or index >= len(self.template.fields):
            return
        region = self.template.fields[index]
        self._loading_form = True
        self.field_name.setText(localized_rule_name(region.name))
        self.field_kind.setCurrentIndex(max(0, self.field_kind.findData(region.kind)))
        self.field_recognition.setCurrentIndex(max(0, self.field_recognition.findData(region.recognition)))
        self.field_source.setCurrentIndex(max(0, self.field_source.findData(region.source)))
        self.field_fixed.setText(region.fixed_value)
        self.field_export.setCurrentIndex(max(0, self.field_export.findData(region.export_mode)))
        self.field_required.setChecked(region.required)
        self.field_column_start.setValue(region.column_start or 0)
        self.field_column_end.setValue(region.column_end if region.column_end is not None else max(0, self.template.columns - 1))
        self._loading_form = False
        self.canvas.active_field = index
        self.canvas.redraw()

    def save_field(self) -> None:
        index = self.field_list.currentRow()
        if not self.template or index < 0:
            return
        region = self.template.fields[index]
        region.name = str(self.field_name.text().strip() or region.name)
        region.kind = str(self.field_kind.currentData())
        region.recognition = str(self.field_recognition.currentData())
        region.source = str(self.field_source.currentData())
        region.fixed_value = self.field_fixed.text().strip()
        region.export_mode = str(self.field_export.currentData())
        region.required = self.field_required.isChecked()
        if region.export_mode == "column_group":
            region.column_start = self.field_column_start.value()
            region.column_end = self.field_column_end.value()
        else:
            region.column_start = region.column_end = None
        self._refresh_fields()
        self.field_list.setCurrentRow(index)
        self.templateChanged.emit(self.template)
        notify(self, tr("Field saved"))

    def redraw_selected_field(self) -> None:
        if self.template and 0 <= self.field_list.currentRow() < len(self.template.fields):
            self.canvas.begin_draw("replace_field")

    def delete_field(self) -> None:
        index = self.field_list.currentRow()
        if not self.template or index < 0:
            return
        notify(self, tr("Field deleted"))
        del self.template.fields[index]
        self.canvas.active_field = -1
        self._refresh_fields()
        self.canvas.redraw()
        self.templateChanged.emit(self.template)

    def suggest_fields(self) -> None:
        if self.image is None or not self.template:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            suggestions = suggest_standard_fields(self.image, self.template)
        finally:
            QApplication.restoreOverrideCursor()
        existing = {(region.name, region.fixed_value) for region in self.template.fields}
        added = 0
        for region in suggestions:
            if (region.name, region.fixed_value) not in existing:
                self.template.fields.append(region)
                added += 1
        self._refresh_fields()
        self.canvas.redraw()
        notify(self, tr('Добавлено областей: {p0}.', p0=added))

    def _refresh_columns(self) -> None:
        self.column_select.blockSignals(True)
        self.column_select.clear()
        if self.template:
            self.template.ensure_column_rules()
            for index, rule in enumerate(self.template.column_rules):
                self.column_select.addItem(fmt('{p0}: {p1}', p0=index + 1, p1=localized_rule_name(rule.name)))
        self.column_select.blockSignals(False)
        if self.column_select.count():
            self.column_select.setCurrentIndex(0)
            self._column_selected(0)

    def _column_selected(self, index: int) -> None:
        if not self.template or index < 0 or index >= len(self.template.column_rules):
            return
        rule = self.template.column_rules[index]
        self.column_name.setText(rule.name)
        self.column_role.setCurrentIndex(max(0, self.column_role.findData(rule.role)))
        self._column_constraints = rule.constraints()
        self.column_rule_summary.setText(self._column_constraints.summary())

    def _column_identity_edited(self, *_args) -> None:
        index = self.column_select.currentIndex()
        if not self.template or index < 0 or self._loading_form:
            return
        rule = self.template.column_rules[index]
        rule.name = self.column_name.text().strip() or rule.name
        rule.role = str(self.column_role.currentData())
        self.column_select.setItemText(index, fmt('{p0}: {p1}', p0=index + 1, p1=localized_rule_name(rule.name)))
        self.templateChanged.emit(self.template)

    def save_column(self) -> None:
        index = self.column_select.currentIndex()
        if not self.template or index < 0:
            return
        rule = self.template.column_rules[index]
        try:
            self._column_constraints.validate()
        except ValueError:
            QMessageBox.warning(self, tr('Правило'), tr('Проверьте формат и границы.'))
            return
        updated = asdict(rule) | asdict(self._column_constraints)
        updated.update(name=self.column_name.text().strip() or rule.name, role=str(self.column_role.currentData()))
        self.template.column_rules[index] = ColumnRule(**updated)
        self.template.schema_version = max(2, self.template.schema_version)
        self._refresh_columns()
        self.column_select.setCurrentIndex(index)
        self.templateChanged.emit(self.template)
        notify(self, tr("Column settings saved"))

    def _continue(self) -> None:
        if not self.prepare_current_settings():
            return
        if self.template:
            try:
                self.template.validate_value_rules()
            except ValueError as exc:
                QMessageBox.warning(self, tr('Проверьте правила'), str(exc)); return
            if self.mode == "template":
                self.saveVersionRequested.emit(self.template)
            else:
                self.continueRequested.emit(self.template)

    def _validate_template(self) -> None:
        if not self.template:
            return
        if not self.prepare_current_settings():
            return
        try:
            self.template.validate_value_rules()
        except ValueError as exc:
            QMessageBox.warning(self, tr('Шаблон требует исправления'), str(exc)); return
        uncovered = 0
        for row in range(self.template.header_rows, self.template.rows):
            for column in range(self.template.row_label_columns, self.template.columns):
                rule, _ = self.template.value_constraints(row, column)
                if rule.value_format == "complex_numeric" and not any(region.contains(row, column) for region in self.template.cell_rules):
                    uncovered += 1
        message = tr('Ошибок не найдено.')
        if uncovered:
            message += tr(' {p0} ячеек используют широкое базовое правило; для точного OCR задайте им тип и диапазон.', p0=uncovered)
        notify(self, message)


class CurrentPageStack(QStackedWidget):
    """A hidden template editor must not force the review window off a laptop screen."""
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(lambda _index: self.updateGeometry())

    def minimumSizeHint(self):
        widget = self.currentWidget()
        return widget.minimumSizeHint() if widget is not None else super().minimumSizeHint()


class ExportOptionsDialog(QDialog):
    def __init__(self, mode: str = "compact", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('Экспорт в Excel'))
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        title = QLabel(tr('Что включить в файл?'))
        set_theme_style(title, "font-size: 19px; font-weight: 600;")
        layout.addWidget(title)
        self.compact = QRadioButton(tr('Сокращённый — только исходная таблица'))
        self.extended = QRadioButton(tr('Расширенный — таблица, данные и аудит'))
        layout.addWidget(self.compact)
        note = QLabel(tr('Таблица с проверенными значениями в расположении оригинала. Каждая страница — отдельный лист.'))
        note.setWordWrap(True)
        set_theme_style(note, fmt('color: {p0}; padding-left: 25px;', p0=MUTED))
        layout.addWidget(note)
        layout.addWidget(self.extended)
        note = QLabel(tr('Дополнительно: Data для анализа и Audit с исходными прочтениями, исправлениями и оценками.'))
        note.setWordWrap(True)
        set_theme_style(note, fmt('color: {p0}; padding-left: 25px;', p0=MUTED))
        layout.addWidget(note)
        (self.extended if mode == "extended" else self.compact).setChecked(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr('Выбрать файл…'))
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("primary", True)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('Отмена'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def mode(self) -> str:
        return "compact" if self.compact.isChecked() else "extended"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(fmt('TableScan Local {p0}', p0=__version__))
        self.resize(1440, 900)
        configured_root = os.getenv("TABLESCAN_DATA_DIR")
        app_root = Path(configured_root) if configured_root else Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        )
        self.store = LocalStore(app_root)
        self.store.install_default_templates()
        self.preferences = QSettings(str(app_root / "preferences.ini"), QSettings.Format.IniFormat)
        set_language(str(self.preferences.value("appearance/language", "en")))
        apply_theme(QApplication.instance(), str(self.preferences.value("appearance/theme", "light")))
        self.job_id = ""
        self.source_path = ""
        self.stored_source_path = ""
        self.images: Sequence[np.ndarray] = []
        self.template: TableTemplate | None = None
        self.result: JobResult | None = None
        self.worker: RecognitionWorker | None = None
        self._workers: list[RecognitionWorker] = []
        self._close_requested = False
        self._close_timer = QTimer(self)
        self._close_timer.setInterval(100)
        self._close_timer.timeout.connect(self._finish_close_if_idle)
        QApplication.instance().installEventFilter(self)
        self.template_editor_working: TableTemplate | None = None
        self.template_editor_reference = ""
        self._pending_draft: tuple[str, dict] | None = None
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(250)
        self._draft_timer.timeout.connect(lambda: self._flush_draft(warn=False))
        self._build_ui()
        self.analysis_queue = AnalysisQueue(self.store, self._make_queue_worker, self)
        self.queue_dialog = AnalysisQueueDialog(self.analysis_queue, self)
        self.queue_dialog.reviewRequested.connect(lambda job_id: self.open_recent(job_id, prefer_result=True))
        self.queue_dialog.prepareRequested.connect(self.prepare_batch)
        self.queue_dialog.exportReadyRequested.connect(self.export_ready_files)
        self.queue_dialog.diagnosticsRequested.connect(self.show_diagnostics)
        self.diagnostics_dialog = None
        self.analysis_queue.changed.connect(self._queue_changed)
        self.analysis_queue.resultSaved.connect(self._queue_result_saved)
        self._queue_changed()
        self.files_page.historyRequested.connect(self.refresh_recent)
        self.refresh_recent()
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "savvykit.png")))

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        top = QFrame()
        top.setFixedHeight(56)
        set_theme_style(top, fmt('border-bottom: 1px solid {p0};', p0=BORDER))
        top_layout = QHBoxLayout(top)
        brand = QLabel(fmt('TableScan Local {p0}', p0=__version__))
        set_theme_style(brand, "font-size: 17px; font-weight: 750; border: 0;")
        local = QLabel(tr('●  Полностью локально'))
        set_theme_style(local, fmt('color: {p0}; border: 0;', p0=GREEN))
        self.file_title = QLabel("")
        set_theme_style(self.file_title, fmt('color: {p0}; border: 0;', p0=MUTED))
        top_layout.addWidget(brand)
        local.hide()
        top_layout.addStretch()
        top_layout.addWidget(self.file_title)
        self.save_status = QLabel("")
        top_layout.addWidget(self.save_status)
        self.queue_button = QPushButton(tr('Analysis queue'))
        self.queue_button.clicked.connect(self.show_analysis_queue)
        top_layout.addWidget(self.queue_button)
        self.diagnostics_button = QPushButton(tr('Diagnostics'))
        self.diagnostics_button.clicked.connect(lambda: self.show_diagnostics())
        top_layout.addWidget(self.diagnostics_button)
        self.theme_select = QComboBox()
        self.theme_select.addItem(tr('Светлая тема'), "light")
        self.theme_select.addItem(tr('Тёмная тема'), "dark")
        self.theme_select.setAccessibleName(tr('Цветовая тема приложения'))
        self.theme_select.setCurrentIndex(1 if QApplication.instance().property("tablescanTheme") == "dark" else 0)
        self.theme_select.currentIndexChanged.connect(self._change_theme)
        top_layout.addWidget(self.theme_select)
        root_layout.addWidget(top)
        self.update_notice = QPushButton()
        self.update_notice.setProperty('primary', True)
        self.update_notice.hide()
        self.update_notice.clicked.connect(self._show_app_update)
        root_layout.addWidget(self.update_notice)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        sidebar = QFrame()
        sidebar.setFixedWidth(190)
        set_theme_style(sidebar, fmt('background: {p0}; border-right: 1px solid {p1};', p0=SURFACE, p1=BORDER))
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 34, 12, 18)
        sidebar_layout.setSpacing(10)
        self.section_buttons: list[QPushButton] = []
        for index, (icon, label) in enumerate((("▱", tr('Документы')), ("▦", tr('Шаблоны')), ("⚙", tr('Настройки')))):
            button = QPushButton(fmt('{p0}   {p1}', p0=icon, p1=label))
            button.setCheckable(True)
            button.setMinimumHeight(55)
            set_theme_style(button, fmt('QPushButton {{ text-align: left; border: 0; background: transparent; font-weight: 650; }}QPushButton:checked {{ color: {p0}; background: #EAF1FF; border-left: 3px solid {p1}; }}', p0=BLUE, p1=BLUE))
            button.clicked.connect(lambda _checked=False, page=index: self._navigate_section(page))
            sidebar_layout.addWidget(button)
            self.section_buttons.append(button)
        sidebar_layout.addStretch()
        settings = QLabel(tr('●  Локально и офлайн\n\nⓘ  Исходные файлы не изменяются'))
        set_theme_style(settings, fmt('color: {p0}; border: 0; padding: 8px;', p0=MUTED))
        settings.setWordWrap(True)
        settings.hide()
        body.addWidget(sidebar)

        self.main_stack = CurrentPageStack()

        document_workspace = QWidget()
        document_layout = QVBoxLayout(document_workspace); document_layout.setContentsMargins(0, 0, 0, 0); document_layout.setSpacing(0)
        step_bar = QFrame(); step_bar.setFixedHeight(54); set_theme_style(step_bar, fmt('border-bottom: 1px solid {p0};', p0=BORDER))
        step_layout = QHBoxLayout(step_bar); step_layout.setContentsMargins(18, 7, 18, 7); step_layout.setSpacing(8)
        self.nav_buttons: list[QPushButton] = []
        for index, label in enumerate((tr('1  Файлы'), tr('2  Совмещение'), tr('3  Сверка'), tr('4  Экспорт'))):
            button = QPushButton(label); button.setCheckable(True)
            set_theme_style(button, fmt('QPushButton {{ border: 0; background: transparent; color: #68707C; }}QPushButton:checked {{ color: {p0}; background: #EFF6FF; }} QPushButton:disabled {{ color: #A5B0B3; background: transparent; }}', p0=BLUE))
            button.clicked.connect(lambda _checked=False, page=index: self._navigate(page))
            step_layout.addWidget(button); self.nav_buttons.append(button)
        step_layout.addStretch(); document_layout.addWidget(step_bar)

        self.stack = CurrentPageStack()
        self.files_page = FilesPage()
        self.files_page.chooseRequested.connect(self.choose_files)
        self.files_page.filesDropped.connect(self.open_files)
        self.files_page.recentOpened.connect(self.open_recent)
        self.table_page = TablePage("document")
        self.table_page.continueRequested.connect(self.start_recognition)
        self.table_page.templateChanged.connect(self._template_changed)
        self.table_page.savedTemplateRequested.connect(self._load_saved_template)
        self.table_page.saveTemplateRequested.connect(self._save_document_template_version)
        self.table_page.rotationRequested.connect(self._rotate_pages)
        self.review_page = ReviewPage()
        self.review_page.exportRequested.connect(self.export_current)
        self.review_page.resultChanged.connect(self._result_changed)
        export_placeholder = QWidget()
        export_layout = QVBoxLayout(export_placeholder)
        export_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        export_layout.addWidget(QLabel(tr('Завершите сверку, затем экспортируйте проверенную таблицу.')))
        for page in (self.files_page, self.table_page, self.review_page, export_placeholder):
            self.stack.addWidget(page)
        document_layout.addWidget(self.stack, 1)

        templates_workspace = QWidget()
        templates_layout = QVBoxLayout(templates_workspace); templates_layout.setContentsMargins(0, 0, 0, 0); templates_layout.setSpacing(0)
        self.template_back_bar = QFrame(); self.template_back_bar.setFixedHeight(48); set_theme_style(self.template_back_bar, fmt('border-bottom: 1px solid {p0};', p0=BORDER))
        back_layout = QHBoxLayout(self.template_back_bar); back_layout.setContentsMargins(18, 6, 18, 6)
        back = QPushButton(tr('←  К библиотеке шаблонов')); back.clicked.connect(self._show_template_library)
        back_layout.addWidget(back); back_layout.addStretch()
        self.templates_stack = CurrentPageStack()
        self.template_library = TemplateLibraryPage()
        self.template_library.createRequested.connect(self.create_template_from_sample)
        self.template_library.importRequested.connect(self.import_template_json)
        self.template_library.openRequested.connect(self.open_template_editor)
        self.template_library.duplicateRequested.connect(self.duplicate_template)
        self.template_library.deleteRequested.connect(self.delete_template)
        self.template_editor = TablePage("template")
        self.template_editor.templateChanged.connect(self._template_editor_changed)
        self.template_editor.saveVersionRequested.connect(self.save_template_version)
        self.template_editor.rotationRequested.connect(self._rotate_template)
        self.templates_stack.addWidget(self.template_library); self.templates_stack.addWidget(self.template_editor)
        templates_layout.addWidget(self.template_back_bar); templates_layout.addWidget(self.templates_stack, 1)

        settings_page = QWidget(); settings_layout = QVBoxLayout(settings_page); settings_layout.setContentsMargins(36, 30, 36, 30)
        settings_title = QLabel(tr('Настройки')); set_theme_style(settings_title, "font-size: 25px; font-weight: 750;")
        settings_layout.addWidget(settings_title)
        settings_text = QLabel(tr('Распознавание выполняется локально. Для числовых ячеек используется многоэтапный режим, а экспорт блокируется до сверки спорных значений.'))
        settings_text.setWordWrap(True); set_theme_style(settings_text, fmt('color: {p0}; font-size: 14px;', p0=MUTED))
        settings_text.hide()
        language_form = QFormLayout()
        self.language_select = QComboBox()
        self.language_select.setAccessibleName(tr("Interface language"))
        self.language_select.setMinimumWidth(230)
        self.language_select.setMaximumWidth(320)
        for code, name in SUPPORTED_LANGUAGES.items():
            self.language_select.addItem(name, code)
        self.language_select.setCurrentIndex(self.language_select.findData(language()))
        self.language_select.currentIndexChanged.connect(self._change_language)
        language_form.addRow(tr("Language"), self.language_select)
        settings_layout.addLayout(language_form)
        language_note = QLabel(tr("Language changes apply immediately and are saved for the next launch. Your document data is not translated."))
        language_note.setWordWrap(True)
        language_note.setMaximumWidth(820)
        set_theme_style(language_note, f"color: {MUTED};")
        settings_layout.addWidget(language_note)
        from .update_settings import UpdateSettings
        self.update_settings = UpdateSettings(self, busy=self._has_background_work, preferences=self.preferences)
        self.update_settings.installRequested.connect(self._install_app_update)
        self.update_settings.updateAvailable.connect(self._update_available)
        settings_layout.addWidget(self.update_settings)
        from .slow_settings import SlowSettings
        self.slow_settings = SlowSettings(self, busy=self._has_background_work)
        settings_layout.addWidget(self.slow_settings)
        settings_layout.addStretch()

        settings_scroll = QScrollArea()
        self.settings_scroll = settings_scroll
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings_page)
        for page in (document_workspace, templates_workspace, settings_scroll): self.main_stack.addWidget(page)
        body.addWidget(self.main_stack, 1)
        root_layout.addLayout(body, 1)
        self.setCentralWidget(root)
        self._refresh_templates()
        self._show_template_library()
        self._navigate_section(0)
        self._navigate(0)

        open_action = QAction(tr('Открыть'), self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.choose_files)
        self.addAction(open_action)
        export_action = QAction(tr('Экспорт'), self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export_current)
        self.addAction(export_action)

    def _update_available(self, release) -> None:
        self.update_notice.setVisible(release is not None)
        if release is not None:
            self.update_notice.setText(tr('Update {version} is available — open update settings', version=release.version))

    def _show_app_update(self) -> None:
        self._navigate_section(2)
        self.settings_scroll.ensureWidgetVisible(self.update_settings)

    def _install_app_update(self, plan) -> None:
        if self._has_background_work():
            self.update_settings.status.setText(tr('Wait for analysis and other operations to finish before updating.'))
            return
        try:
            if self.table_page.template and not self.table_page.prepare_current_settings():
                return
            if not self._flush_draft():
                return
            if self.template_editor.template:
                if not self.template_editor.prepare_current_settings():
                    return
                current = self.template_editor.template
                saved = next((item for item in self.store.load_templates() if item.id == current.id), None)
                if saved is None or saved.to_dict() != current.to_dict():
                    self.store.save_template_version(current, self.template_editor_reference)
            self.analysis_queue.shutdown()
            from .app_update import launch_installer
            launch_installer(plan)
        except Exception as exc:
            self.update_settings.failed(str(exc))
            return
        self.close()

    def _change_language(self, _index: int) -> None:
        code = self.language_select.currentData()
        if code not in SUPPORTED_LANGUAGES:
            return
        self.preferences.setValue("appearance/language", code)
        self.preferences.sync()
        set_language(code)

    def _change_theme(self, _index: int) -> None:
        mode = self.theme_select.currentData()
        apply_theme(QApplication.instance(), mode)
        self.preferences.setValue("appearance/theme", mode)
        self.preferences.sync()

    def _navigate_section(self, index: int) -> None:
        self.main_stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.section_buttons):
            button.setChecked(button_index == index)
        if index == 1:
            QTimer.singleShot(0, self._refresh_templates)

    def _show_template_library(self) -> None:
        self.templates_stack.setCurrentIndex(0)
        self._refresh_templates()
        # The back button lives inside this bar. Hiding its parent from the
        # button's own native click callback can leave macOS accessibility with
        # a stale child pointer. Defer the visual change until the event ends.
        QTimer.singleShot(0, self.template_back_bar.hide)

    def _update_steps(self):
        available = [True, self.template is not None, self.result is not None,
                     self.result is not None and self.result.unresolved_count == 0]
        for index, button in enumerate(self.nav_buttons):
            button.setEnabled(available[index])
            button.setChecked(index == self.stack.currentIndex())
            button.setToolTip("" if available[index] else str(tr('Open a document first') if index == 1 else tr('Complete recognition first') if index == 2 else tr('Review all flagged values before export')))

    def _navigate(self, index: int) -> None:
        self._update_steps()
        if not self.nav_buttons[index].isEnabled():
            return
        if index == 1 and not self.template:
            return
        if index >= 2 and not self.result:
            return
        if index == 3:
            self.export_current()
            self._update_steps()
            return
        self.stack.setCurrentIndex(index)
        self._navigate_section(0)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)

    def refresh_recent(self) -> None:
        page = self.files_page
        search = page.history_search.text().strip()
        total = self.store.job_count(search=search)
        page.history_offset = min(page.history_offset, max(0, (total - 1) // page.history_page_size * page.history_page_size))
        page.set_recent(self.store.recent_jobs(page.history_page_size, offset=page.history_offset, search=search), total)

    def _refresh_templates(self) -> None:
        templates = self.store.load_templates()
        if hasattr(self, "template_library"):
            self.template_library.set_templates(templates)
        if hasattr(self, "table_page"):
            self.table_page.set_saved_templates(templates)
        if hasattr(self, "template_editor"):
            self.template_editor.set_saved_templates(templates)

    def _template_editor_changed(self, template: TableTemplate) -> None:
        self.template_editor_working = template

    def create_template_from_sample(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr('Выберите образец таблицы'), "", document_file_filter())
        if not path:
            return
        try:
            images = open_document(path)
            if not images:
                raise ValueError(tr('Файл не содержит страниц'))
            detection = self._detect_or_manual(images[0])
        except Exception as exc:
            QMessageBox.critical(self, tr('Не удалось открыть образец'), str(exc)); return
        identifier = str(uuid4())
        template = TableTemplate(
            identifier, f"{Path(path).stem} — template", detection.table_rect,
            detection.row_guides, detection.column_guides,
            template_version=0, family_id=identifier, reference_source_path=path,
            reference_page_aspect=images[0].shape[1] / max(1, images[0].shape[0]),
        )
        template.ensure_column_rules()
        self.template_editor_working = template
        self.template_editor_reference = path
        self.template_editor.set_document(images[0], template)
        self.template_editor.grid_warning.setText(join_text(' ', detection.warnings))
        self.templates_stack.setCurrentIndex(1); self.template_back_bar.show(); self._navigate_section(1)

    def open_template_editor(self, template_id: str) -> None:
        template = next((item for item in self.store.load_templates() if item.id == template_id), None)
        if template is None:
            QMessageBox.warning(self, tr('Шаблон недоступен'), tr('Не удалось загрузить выбранный шаблон.')); return
        source = template.reference_source_path
        if not source or not Path(source).exists():
            source, _ = QFileDialog.getOpenFileName(self, tr('Укажите образец для шаблона'), "", document_file_filter())
            if not source:
                return
        try:
            images = rotate_document(open_document(source), template.rotation_degrees)
            if not images:
                raise ValueError(tr('Файл не содержит страниц'))
            image = images[0]
        except Exception as exc:
            QMessageBox.warning(self, tr('Образец недоступен'), str(exc)); return
        working = TableTemplate.from_dict(template.to_dict())
        self.template_editor_working = working; self.template_editor_reference = source
        self.template_editor.set_document(image, working)
        self.templates_stack.setCurrentIndex(1); self.template_back_bar.show(); self._navigate_section(1)

    def save_template_version(self, template: TableTemplate) -> None:
        try:
            template.validate_value_rules()
            if not self.template_editor_reference:
                raise ValueError(tr('Не указан эталонный документ'))
            saved = self.store.save_template_version(template, self.template_editor_reference)
        except Exception as exc:
            QMessageBox.warning(self, tr('Шаблон не сохранён'), str(exc)); return
        self.template_editor_working = saved
        self.template_editor.template = saved
        self.template_editor_reference = saved.reference_source_path
        self._refresh_templates()
        self.template_library.select_template(saved.id)
        try:
            image = rotate_document(open_document(saved.reference_source_path), saved.rotation_degrees)[0]
            self.template_editor.set_document(image, saved)
        except Exception:
            pass
        notify(self, tr('Шаблон сохранён: {p0}', p0=saved.name))

    def _save_document_template_version(self, template: TableTemplate) -> None:
        if not self.images:
            return
        if self.table_page.template is not None:
            if not self.table_page.prepare_current_settings():
                return
            template = self.table_page.template
        template.reference_page_aspect = self.images[0].shape[1] / max(1, self.images[0].shape[0])
        try:
            saved = self.store.save_template_version(template, self.stored_source_path or self.source_path)
        except Exception as exc:
            QMessageBox.warning(self, tr('Шаблон не сохранён'), str(exc)); return
        self.template = saved
        self.table_page.set_document(self.images[0], saved)
        self._refresh_templates()
        self.template_library.select_template(saved.id)
        notify(self, tr('Шаблон сохранён: {p0}', p0=saved.name))

    def duplicate_template(self, template_id: str) -> None:
        template = next((item for item in self.store.load_templates() if item.id == template_id), None)
        if template:
            duplicate = self.store.duplicate_template(template)
            self._refresh_templates()
            self.open_template_editor(duplicate.id)

    def delete_template(self, template_id: str) -> None:
        template = next((item for item in self.store.load_templates() if item.id == template_id), None)
        if template is None:
            return
        answer = QMessageBox.question(
            self, tr('Удалить шаблон?'),
            tr('Будет удалён шаблон «{p0}». Обработанные документы сохранятся.', p0=template.name),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.store.delete_template(template_id); self._refresh_templates()

    def import_template_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr('Импорт шаблона'), '', 'TableScan (*.json)')
        if not path:
            return
        try:
            template = TableTemplate.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))
            if not (1 <= template.rows <= 200 and 1 <= template.columns <= 100):
                raise ValueError('Invalid grid size')
            for axis in (template.row_guides, template.column_guides):
                if not all(np.isfinite(v) and 0 <= v <= 1 for v in axis) or any(a >= b for a, b in zip(axis, axis[1:])):
                    raise ValueError('Invalid grid coordinates')
            template.validate_value_rules()
            # Imported identifiers and local sample paths never become filesystem paths.
            template.id = template.family_id = str(uuid4())
            template.reference_source_path = ''
            template.template_version = 1
            for region in [*template.fields, *template.cell_rules]:
                region.id = str(uuid4())
            self.store.save_template(template)
        except Exception as exc:
            QMessageBox.warning(self, tr('Шаблон не импортирован'), str(exc))
            return
        self._refresh_templates()
        self.template_library.select_template(template.id)
        notify(self, tr('Шаблон импортирован: {p0}', p0=template.name))

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr('Открыть изображения таблиц'), "", document_file_filter())
        if paths:
            self.open_files(paths)

    def open_files(self, paths: list[str]) -> None:
        if not paths:
            return
        if len(paths) == 1:
            self.open_source(paths[0])
            return
        self.prepare_batch(paths)

    def prepare_batch(self, paths: list[str]) -> None:
        from .template_matcher import latest_template_versions
        templates = latest_template_versions(self.store.load_templates())
        from .batch_dialog import BatchPreparationDialog
        dialog = BatchPreparationDialog(paths, templates,
                                        lambda: TablePage(mode="template", private_copy=True), self,
                                        save_template=self._save_batch_template)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.enqueue_prepared_files(dialog.jobs, *dialog.analysis_options, preparations=dialog.preparations)
        dialog.deleteLater()

    def _save_batch_template(self, template, source):
        saved = self.store.save_template_version(template, source)
        self._refresh_templates()
        return saved

    def enqueue_files(self, paths, template, high_accuracy=True, slow_mode=False):
        self.enqueue_prepared_files([(path, template) for path in paths], high_accuracy, slow_mode)

    def enqueue_prepared_files(self, jobs, high_accuracy=True, slow_mode=False, *, preparations=None):
        if not self._flush_draft():
            return
        if slow_mode:
            from .slow_setup import setup_status
            from .slow_mode import runtime_config
            try:
                runtime_config()
            except RuntimeError as exc:
                self._navigate_section(2)
                QMessageBox.warning(self, tr('Slow mode недоступен'), str(exc))
                return
            if self.slow_settings.is_installing() or setup_status() != 'ready':
                self._navigate_section(2)
                return
            high_accuracy = True
        errors = []
        for path, template in jobs:
            job_id = None
            try:
                job_id, copied = self.store.import_source(path)
                self.store.save_draft(job_id, template)
                self.analysis_queue.enqueue(job_id, str(path), str(copied), template, high_accuracy, slow_mode,
                                            preparation=(preparations or {}).get(path))
            except Exception as exc:
                if job_id:
                    self.store.delete_job(job_id)
                errors.append(f'{Path(path).name}: {exc}')
        self.refresh_recent()
        self.show_analysis_queue()
        if errors:
            QMessageBox.warning(self, tr('Some files could not be queued'), '\n'.join(errors))

    def open_source(self, path: str, reused_template: TableTemplate | None = None) -> None:
        if not self._flush_draft():
            return
        selected_match: TemplateMatch | None = None
        new_job_id = ""
        try:
            new_job_id, copied = self.store.import_source(path)
            images = open_document(copied)
            if not images:
                raise ValueError(tr('The document contains no pages.'))
            detection: GridDetection | None = None if reused_template else self._detect_or_manual(images[0])
            chosen_template = reused_template
            if reused_template is None:
                matches = rank_templates(images[0].shape, detection, self.store.load_templates())
                if matches:
                    chooser = TemplateChoiceDialog(matches, self)
                    if chooser.exec() != QDialog.DialogCode.Accepted:
                        self.store.delete_job(new_job_id)
                        return
                    if chooser.selected_template_id:
                        selected_match = next((item for item in matches if item.template.id == chooser.selected_template_id), None)
                        chosen_template = selected_match.template if selected_match else None
            if chosen_template:
                template = TableTemplate.from_dict(chosen_template.to_dict())
                images = rotate_document(images, template.rotation_degrees)
                template = fit_document_template(template, images)
            else:
                template = self._new_template(path, str(copied), images, detection)
            template.resize_grid(template.rows, template.columns)
            template.validate_value_rules()
            self.store.save_draft(new_job_id, template)
        except Exception as exc:
            if new_job_id:
                self.store.delete_job(new_job_id)
            QMessageBox.critical(self, tr('Не удалось открыть документ'), str(exc))
            return
        self._activate_document(new_job_id, path, str(copied), images, template)
        if selected_match:
            self.table_page.grid_warning.setText(
                tr('Template match: {p0}%', p0=round(selected_match.score * 100))
                + (tr(' · Автоподгонка: {p0} строк данных. Проверьте сетку и поля.', p0=template.rows-template.header_rows) if template.auto_fit_rows else '')
            )
        else:
            self.table_page.grid_warning.setText(join_text(' ', detection.warnings) if detection else tr('Проверьте наложение сохранённого шаблона и число строк заголовка.'))
        self.refresh_recent()

    def _new_template(self, path: str, copied: str, images: Sequence[np.ndarray],
                      detection: GridDetection | None = None) -> TableTemplate:
        detection = detection or self._detect_or_manual(images[0])
        identifier = str(uuid4())
        template = TableTemplate(
            id=identifier, name=f"{Path(path).stem} — template", table_rect=detection.table_rect,
            row_guides=detection.row_guides, column_guides=detection.column_guides,
            template_version=0, family_id=identifier, reference_source_path=copied,
            reference_page_aspect=images[0].shape[1] / max(1, images[0].shape[0]),
        )
        template.ensure_column_rules()
        return template

    def _activate_document(self, job_id: str, path: str, copied: str, images: Sequence[np.ndarray],
                           template: TableTemplate, result: JobResult | None = None) -> None:
        """Install one fully prepared context; failed reads never get this far."""
        self.job_id, self.source_path, self.stored_source_path = job_id, path, copied
        self.images, self.template, self.result = images, template, result
        self.review_page.clear_result()
        self.file_title.setText(Path(path).name)
        self.save_status.setText(tr('Изменения сохранены'))
        self.save_status.setToolTip("")
        self.table_page.set_document(images[0], template)
        if result is not None:
            self.review_page.set_result(images, result)
        self._navigate(2 if result is not None else 1)
        self._queue_changed()

    def show_diagnostics(self, job_id=''):
        from .diagnostics_dialog import DiagnosticsDialog
        if self.diagnostics_dialog is None:
            self.diagnostics_dialog = DiagnosticsDialog(self.analysis_queue, self)
        self.diagnostics_dialog.refresh()
        if job_id:
            self.diagnostics_dialog.jobs.setCurrentIndex(self.diagnostics_dialog.jobs.findData(job_id))
        self.diagnostics_dialog.show()
        self.diagnostics_dialog.raise_()
        self.diagnostics_dialog.activateWindow()

    def show_analysis_queue(self):
        self.queue_dialog.show()
        self.queue_dialog.raise_()
        self.queue_dialog.activateWindow()

    def _make_queue_worker(self, entry):
        worker = RecognitionWorker(None, entry['stored_path'], TableTemplate.from_dict(entry['template']),
                                   self.store.jobs_dir / entry['job_id'] / 'crops', entry['high_accuracy'],
                                   self, slow_mode=entry['slow_mode'], auto_prepare='preparation' not in entry,
                                   run_id=entry.get('run_id'), adaptive=entry.get('adaptive', False))
        self.worker = worker
        self._workers.append(worker)
        worker.finished.connect(lambda: self._recognition_thread_finished(worker))
        return worker

    def _queue_changed(self):
        busy = sum(e['status'] in ('queued', 'running', 'cancelling') for e in self.analysis_queue.entries)
        self.queue_button.setText(tr('Analysis queue ({p0})', p0=busy))
        self.table_page.set_recognition_running(self.analysis_queue.busy(self.job_id))

    def _queue_result_saved(self, job_id, result):
        # A completion must never install another document's result in the editor.
        self.refresh_recent()

    @staticmethod
    def _detect_or_manual(image: np.ndarray) -> GridDetection:
        try:
            return detect_grid(image)
        except ValueError:
            rect = NormalizedRect(.1, .1, .8, .8)
            rows, columns = evenly_spaced_guides(rect, 10, 6)
            return GridDetection(rect, rows, columns, [tr('Grid not detected. Rotate if needed, then redraw the table boundary and set its rows and columns.')])

    def _rotate_template(self, degrees: int) -> None:
        template = self.template_editor_working
        image = self.template_editor.image
        if template is None or image is None:
            return
        if template.fields or template.cell_rules or template.fixed_cells or template.excluded_rows or template.excluded_columns or template.excluded_cells:
            answer = QMessageBox.question(self, tr('Rotate template'), tr('Rotation will detect a new grid and clear fields, rules, template values and exclusions. Continue?'))
            if answer != QMessageBox.StandardButton.Yes:
                return
        rotated = rotate_document([image], degrees)[0]
        detection = self._detect_or_manual(rotated)
        template.rotation_degrees = (template.rotation_degrees + degrees) % 360
        template.table_rect = detection.table_rect
        template.row_guides = detection.row_guides
        template.column_guides = detection.column_guides
        template.fields = []
        template.cell_rules = []
        template.fixed_cells = []
        template.clear_exclusions()
        template.column_rules = []
        template.ensure_column_rules()
        template.reference_page_aspect = rotated.shape[1] / rotated.shape[0]
        self.template_editor.set_document(rotated, template)
        notify(self, tr('Template rotated'))

    def _rotate_pages(self, degrees: int) -> None:
        if not self.images or not self.template:
            return
        if self.template.fields or self.result or self.template.excluded_rows or self.template.excluded_columns or self.template.excluded_cells:
            answer = QMessageBox.question(self, tr('Повернуть документ'), tr('Поворот сбросит текущую сетку и выделенные поля. Сохранённый исходный файл не изменится. Продолжить?'))
            if answer != QMessageBox.StandardButton.Yes:
                return
        rotation = (self.template.rotation_degrees + degrees) % 360
        self.images = rotate_document(self.images, degrees)
        detection = self._detect_or_manual(self.images[0])
        self.template = TableTemplate(str(uuid4()), str(self.template.name), detection.table_rect, detection.row_guides, detection.column_guides, rotation_degrees=rotation)
        self.result = None
        self.table_page.set_document(self.images[0], self.template)
        self.table_page.grid_warning.setText(join_text(' ', detection.warnings))
        self._template_changed(self.template)
        notify(self, tr("Document rotated"))

    def open_recent(self, job_id: str, *, prefer_result: bool = False) -> None:
        if not self._flush_draft():
            return
        try:
            loaded = self.store.load_job(job_id)
            if not loaded:
                raise ValueError(tr('Не удалось открыть сохранённое задание.'))
            metadata, result = loaded
            images = open_document(metadata["stored_source_path"])
            if not images:
                raise ValueError(tr('The document contains no pages.'))
            draft = None if prefer_result and result else self.store.load_draft(job_id)
            if draft and result and draft.to_dict() == result.template.to_dict():
                draft = None  # Saving unchanged controls must not hide reviewed data.
            if draft:
                template, result = draft, None
            elif result:
                template = TableTemplate.from_dict(result.template.to_dict())
            else:
                # Recover legacy imported jobs which predate automatic drafts.
                template = self._new_template(metadata["source_path"], metadata["stored_source_path"], images)
                self.store.save_draft(job_id, template)
            template.resize_grid(template.rows, template.columns)
            images = rotate_document(images, template.rotation_degrees)
            images[0]  # Decode the initial page before replacing the active context.
        except Exception as exc:
            QMessageBox.warning(self, tr('Исходник недоступен'), str(exc))
            return
        self._activate_document(job_id, metadata["source_path"], metadata["stored_source_path"], images, template, result)
        if draft:
            notify(self, tr('Восстановлены настройки последнего запуска. Проверьте их и продолжите.'))

    def _template_changed(self, template: TableTemplate) -> None:
        self.template = template
        if self.result and self.result.template.to_dict() != template.to_dict():
            # Keep the saved job intact; a changed template must not reinterpret
            # old confirmed results as if recognition had been rerun.
            self.result = None
        if self.result is None:
            self.review_page.clear_result()
        else:
            self.review_page.result = self.result
        if self.job_id:
            self._pending_draft = (self.job_id, template.to_dict())
            self.save_status.setText(tr('Сохранение изменений…'))
            self._draft_timer.start()
        self._update_steps()

    def _flush_draft(self, *, warn: bool = True) -> bool:
        self._draft_timer.stop()
        if self._pending_draft is None:
            return True
        job_id, data = self._pending_draft
        try:
            self.store.save_draft(job_id, TableTemplate.from_dict(data))
        except OSError as exc:
            self.save_status.setText(tr('Не удалось сохранить изменения'))
            self.save_status.setToolTip(str(exc))
            if warn:
                QMessageBox.warning(self, tr('Не удалось сохранить изменения'), str(exc))
            return False
        self._pending_draft = None
        self.save_status.setText(tr('Изменения сохранены'))
        self.save_status.setToolTip("")
        return True

    def _load_saved_template(self, template_id: str) -> None:
        if not self.images:
            return
        template = next((item for item in self.store.load_templates() if item.id == template_id), None)
        if template is None:
            self.table_page.set_saved_templates(self.store.load_templates())
            QMessageBox.warning(self, tr('Шаблон недоступен'), tr('Не удалось загрузить сохранённый шаблон.'))
            return
        current_rotation = self.template.rotation_degrees if self.template else 0
        images = rotate_document(self.images, template.rotation_degrees - current_rotation)
        try:
            fitted = fit_document_template(template, images)
        except ValueError as exc:
            self.table_page.set_saved_templates(self.store.load_templates())
            QMessageBox.warning(self, tr('Подгонка требует проверки'), str(exc))
            return
        self.images = images
        self.template = fitted
        self.result = None
        self.table_page.set_document(self.images[0], self.template)
        self._template_changed(self.template)

    def start_recognition(self, template: TableTemplate) -> None:
        if not self.images or not self.job_id:
            return
        if self.analysis_queue.busy(self.job_id):
            self.show_analysis_queue()
            return
        if self.table_page.template is not None:
            if not self.table_page.prepare_current_settings():
                return
            template = self.table_page.template
        if self.table_page.slow_mode.isChecked():
            from .slow_setup import setup_status
            from .slow_mode import runtime_config
            try:
                runtime_config()
            except RuntimeError as exc:
                self._navigate_section(2)
                QMessageBox.warning(self, tr('Slow mode недоступен'), str(exc))
                return
            if self.slow_settings.is_installing() or setup_status() != 'ready':
                self._navigate_section(2)
                return
        template = TableTemplate.from_dict(template.to_dict())
        try:
            template.validate_value_rules()
        except ValueError as exc:
            QMessageBox.warning(self, tr('Проверьте правила'), str(exc)); return
        try:
            if not self._flush_draft():
                return
            self.store.save_draft(self.job_id, template)
        except OSError as exc:
            QMessageBox.warning(self, tr('Проверьте правила'), str(exc))
            return
        self.template = template
        try:
            self.analysis_queue.enqueue(self.job_id, self.source_path, self.stored_source_path, template,
                                        self.table_page.high_accuracy.isChecked(), self.table_page.slow_mode.isChecked(),
                                        adaptive=self.table_page.adaptive.isChecked())
        except OSError as exc:
            QMessageBox.warning(self, tr('Queue error'), str(exc))
            return
        self.show_analysis_queue()

    def _recognition_thread_finished(self, worker: RecognitionWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        if self.worker is worker:
            self.worker = None
            self.table_page.set_recognition_running(False)
        worker.deleteLater()

    def _recognition_done(self, result: JobResult) -> None:
        self._flush_draft()
        self.result = result
        self.template = TableTemplate.from_dict(result.template.to_dict())
        self.table_page.set_document(self.images[0], self.template)
        self.store.save_result(self.job_id, result)
        self.review_page.set_result(self.images, result)
        self._navigate(2)
        self.refresh_recent()
        incomplete = [page for page in result.pages if page.slow_mode.get('status') in {'failed', 'partial'}]
        if incomplete:
            QMessageBox.warning(self, tr('Перепроверка завершена не полностью'),
                tr('Для части ячеек slow mode не завершён. Основные результаты сохранены; спорные значения требуют проверки.'))
        elif any(page.slow_mode for page in result.pages):
            notify(self, tr('Slow mode завершён. Исправлено измерений: {p0}. Проверьте спорные значения.',
                           p0=sum(page.slow_mode.get('changed', 0) for page in result.pages)))

    def eventFilter(self, watched, event):
        # Quit from the application menu/Cmd+Q must use the same asynchronous
        # shutdown as closing the main window, including with a dialog open.
        if watched is QApplication.instance() and event.type() == QEvent.Type.Quit and self.isVisible():
            self.close()
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    def _has_background_work(self):
        from .batch_dialog import BatchPreparationDialog
        return self.analysis_queue.worker is not None or any(
            thread.isRunning() for thread in self.findChildren(QThread)) or any(
            dialog.worker is not None for dialog in self.findChildren(BatchPreparationDialog))

    def _finish_close_if_idle(self):
        if self._close_requested and not self._has_background_work():
            self._close_timer.stop()
            self.close()

    def closeEvent(self, event) -> None:
        if self.slow_settings.is_installing():
            self._navigate_section(2)
            QMessageBox.information(self, tr('Slow mode'), tr('Slow mode removal is still running. Wait for it to finish before closing TableScan.') if self.slow_settings.removing else tr('Slow mode installation is still running. Cancel it in Settings or wait for it to finish before closing TableScan.'))
            event.ignore()
            return
        if not self._close_requested:
            try:
                self.analysis_queue.shutdown()
            except OSError as exc:
                QMessageBox.warning(self, tr('Queue error'), str(exc))
                event.ignore()
                return
            for thread in self.findChildren(QThread):
                thread.requestInterruption()
            from .batch_dialog import BatchPreparationDialog
            for dialog in self.findChildren(BatchPreparationDialog):
                dialog.reject()
            if self.template and not self.table_page.prepare_current_settings():
                event.ignore()
                return
            if not self._flush_draft():
                event.ignore()
                return
            self._close_requested = True
            self.update_settings.stop_automatic_checks()
            self.queue_dialog.hide()
            self.setEnabled(False)
            self.save_status.setText(tr('Closing: stopping analyses…'))
        if self._has_background_work():
            event.ignore()
            self._close_timer.start()
            return
        self._close_timer.stop()
        for dialog in self.findChildren(QDialog):
            if dialog.isVisible():
                dialog.reject()
        super().closeEvent(event)

    def _result_changed(self, result: JobResult) -> None:
        if result is not self.result:
            return  # Ignore a queued edit emitted by a previously open document.
        if self.job_id:
            self.store.save_result(self.job_id, result)
            self.analysis_queue.review_updated(self.job_id, result)
        self.refresh_recent()
        self._update_steps()

    def export_ready_files(self) -> None:
        entries = [dict(entry) for entry in self.analysis_queue.entries if entry['status'] == 'ready']
        if not entries:
            return
        options = ExportOptionsDialog(str(self.preferences.value("export/mode", "compact")), self)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        folder = QFileDialog.getExistingDirectory(self, tr('Export all ready files…'), str(Path.home()))
        if not folder:
            return
        saved_paths, errors = [], []
        for entry in entries:
            try:
                saved_job = self.store.load_job(entry['job_id'])
                if not saved_job or saved_job[1] is None:
                    raise ValueError(tr('Saved result is unavailable.'))
                result = saved_job[1]
                if result.unresolved_count:
                    self.analysis_queue.review_updated(entry['job_id'], result)
                    continue
                stem = Path(entry['source_path']).stem + '-recognized'
                target = Path(folder) / (stem + '.xlsx')
                suffix = 2
                while target.exists():
                    target = Path(folder) / f'{stem}-{suffix}.xlsx'
                    suffix += 1
                saved_paths.append(export_job(result, target, mode=options.mode))
                self.analysis_queue.exported(entry['job_id'])
            except Exception as exc:
                errors.append(f"{Path(entry['source_path']).name}: {exc}")
        self.preferences.setValue("export/mode", options.mode)
        self._update_steps()
        message = str(tr('Exported {p0} files to {p1}.', p0=len(saved_paths), p1=folder))
        if errors:
            QMessageBox.warning(self, tr('Ошибка экспорта'), message + '\n\n' + '\n'.join(errors))
        else:
            notify(self, message)

    def export_current(self) -> None:
        if not self.result:
            return
        if self.result.unresolved_count:
            QMessageBox.warning(self, tr('Нужна сверка'), tr('Перед экспортом проверьте все спорные значения: {p0}.', p0=self.result.unresolved_count))
            return
        options = ExportOptionsDialog(str(self.preferences.value("export/mode", "compact")), self)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        default = str(Path.home() / fmt('{p0}-recognized.xlsx', p0=Path(self.source_path).stem))
        target, _ = QFileDialog.getSaveFileName(self, tr('Экспорт проверенной таблицы'), default, tr('Книга Excel (*.xlsx)'))
        if not target:
            return
        try:
            saved = export_job(self.result, target, mode=options.mode)
            self.preferences.setValue("export/mode", options.mode)
            self.analysis_queue.exported(self.job_id)
        except Exception as exc:
            QMessageBox.critical(self, tr('Ошибка экспорта'), str(exc))
            return
        notify(self, tr('Проверенная таблица сохранена:\n{p0}', p0=saved))
        self._update_steps()


class InstanceAlreadyRunning(RuntimeError):
    pass


def create_application(*, lock_store=False) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("TableScan Local")
    app.setOrganizationName("TableScan Local")
    app.setStyle("Fusion")
    if lock_store:
        root = Path(os.getenv('TABLESCAN_DATA_DIR') or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        root.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(root / 'instance.lock'))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise InstanceAlreadyRunning(tr('This data folder is already open in another TableScan Local window. Close that application before opening another copy.'))
        app._store_lock = lock  # Keep ownership until the application exits.
    window = MainWindow()
    return app, window
