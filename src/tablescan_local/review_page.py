from __future__ import annotations
from collections.abc import Sequence
from .i18n import tr, fmt, join_text
import sys
from .i18n import escape_text as escape
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QShortcut, QKeySequence, QColor, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QMessageBox, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout
from .localized_widgets import QCheckBox, QComboBox, QFrame, QLabel, QLineEdit, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QToolButton, QWidget
from .domain import JobResult, excel_column_name
from .imaging import crop_cell, crop_normalized
from .theme import colors, set_theme_style
from .ui_common import AMBER, GREEN, MUTED, pixmap_from_bgr
from .document_canvas import DocumentCanvas
from . import review_actions


class CropPreview(QLabel):
    """Paint the original at a fixed layout size; image changes cannot resize the inspector."""
    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self._source_pixmap: QPixmap | None = None
        self.setObjectName("cropPreview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def set_source_pixmap(self, pixmap: QPixmap) -> None:
        self._source_pixmap = pixmap
        super().setText("")
        self.update()

    def setText(self, text: str) -> None:
        self._source_pixmap = None
        super().setText(text)
        self.update()

    def pixmap(self) -> QPixmap:
        if self._source_pixmap is None:
            return QPixmap()
        return self._source_pixmap.scaled(self.contentsRect().size(), Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._source_pixmap is not None and not self._source_pixmap.isNull():
            target = self.contentsRect().adjusted(8, 8, -8, -8)
            size = self._source_pixmap.size().scaled(target.size(), Qt.AspectRatioMode.KeepAspectRatio)
            rect = QRectF(0, 0, size.width(), size.height())
            rect.moveCenter(QPointF(target.center()))
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(rect, self._source_pixmap, QRectF(self._source_pixmap.rect()))



class ReviewPage(QWidget):
    exportRequested = Signal()
    resultChanged = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.images: Sequence[np.ndarray] = []
        self.result: JobResult | None = None
        self.current_page = 0
        self.current_kind = "cell"
        self.current_index = -1
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 10, 16, 14)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(tr('Страница')))
        self.page_select = QComboBox()
        self.page_select.setMinimumWidth(92)
        self.page_select.currentIndexChanged.connect(self._page_changed)
        toolbar.addWidget(self.page_select)
        self.unresolved_label = QLabel(tr('Нет значений для сверки'))
        set_theme_style(self.unresolved_label, fmt('color: {p0}; font-weight: 700;', p0=AMBER))
        self.uncertain_only = QCheckBox(tr('Только спорные'))
        self.uncertain_only.toggled.connect(self._apply_filter)
        self.confirm_all_button = QPushButton(tr('Подтвердить все спорные'))
        self.confirm_all_button.setToolTip(tr('Принять показанные значения во всём документе'))
        self.confirm_all_button.clicked.connect(self.confirm_all_uncertain)
        export = QPushButton(tr('Экспорт в Excel'))
        export.setProperty("primary", True)
        export.clicked.connect(self.exportRequested)
        toolbar.addStretch()
        toolbar.addWidget(self.unresolved_label)
        toolbar.addWidget(self.uncertain_only)
        toolbar.addWidget(self.confirm_all_button)
        toolbar.addWidget(export)
        outer.addLayout(toolbar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)
        preview_box = QWidget()
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_toolbar = QHBoxLayout()
        preview_toolbar.addWidget(QLabel(tr('Оригинал документа')))
        preview_toolbar.addStretch()
        self.canvas = DocumentCanvas()
        self.canvas.read_only = True
        self.canvas.show_grid = False
        self.canvas.show_fields = True
        for label, callback in (("−", lambda: self.canvas.zoom_by(.8)),
                                ("+", lambda: self.canvas.zoom_by(1.25)),
                                (tr('Вписать'), self.canvas.fit_document)):
            button = QPushButton(label)
            button.setToolTip({"−": tr('Уменьшить оригинал'), "+": tr('Увеличить оригинал'), tr('Вписать'): tr('Показать таблицу целиком')}[label])
            button.clicked.connect(callback)
            preview_toolbar.addWidget(button)
        preview_layout.addLayout(preview_toolbar)
        preview_layout.addWidget(self.canvas)
        split.addWidget(preview_box)

        self.results_tabs = QTabWidget()
        self.table = QTableWidget()
        self.table.setMinimumHeight(240)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.currentCellChanged.connect(lambda row, col, *_: self._cell_clicked(row, col))
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setMinimumSectionSize(76)
        self.table.horizontalHeader().setDefaultSectionSize(100)
        self.results_tabs.addTab(self.table, tr('Таблица'))
        fields_box = QWidget()
        fields_layout = QVBoxLayout(fields_box)
        self.fields_label = QLabel(tr('Выберите поле, чтобы увидеть его на оригинале и проверить значение.'))
        self.fields_label.setWordWrap(True)
        fields_layout.addWidget(self.fields_label)
        self.fields_table = QTableWidget(0, 3)
        self.fields_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Cocoa's selectedChildren traversal can invalidate other selected cells
        # while constructing a table's accessibility model. One selected cell
        # still identifies the field and avoids that native use-after-free.
        self.fields_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectItems if sys.platform == 'darwin'
            else QTableWidget.SelectionBehavior.SelectRows)
        self.fields_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.fields_table.setHorizontalHeaderLabels([tr('Поле'), tr('Значение'), tr('Оценка OCR')])
        self.fields_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.fields_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.fields_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.fields_table.verticalHeader().hide()
        self.fields_table.currentCellChanged.connect(lambda row, col, *_: self._field_clicked(row, col))
        fields_layout.addWidget(self.fields_table)
        self.results_tabs.addTab(fields_box, tr('Поля'))
        self.results_tabs.currentChanged.connect(self._result_tab_changed)
        for table in (self.table, self.fields_table):
            table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
            table.setVerticalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        split.addWidget(self.results_tabs)
        split.setSizes([560, 760])
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)

        inspector = QFrame()
        inspector.setObjectName("reviewInspector")
        inspector.setMinimumHeight(260)
        inspector_layout = QHBoxLayout(inspector)
        inspector_layout.setContentsMargins(14, 12, 14, 12)
        inspector_layout.setSpacing(20)
        crop_box = QVBoxLayout()
        self.crop_caption = QLabel(tr('Фрагмент оригинала'))
        self.crop_caption.setTextFormat(Qt.TextFormat.PlainText)
        self.crop_caption.setWordWrap(True)
        self.crop_caption.setMaximumHeight(36)
        crop_box.addWidget(self.crop_caption)
        self.crop_label = CropPreview(tr('Выберите значение'))
        self.crop_label.setMinimumSize(220, 125)
        crop_box.addWidget(self.crop_label, 1)
        inspector_layout.addLayout(crop_box, 2)

        edit_box = QVBoxLayout()
        edit_box.setSpacing(8)
        title_row = QHBoxLayout()
        self.address_label = QLabel(tr('Выбранное значение'))
        set_theme_style(self.address_label, "font-size: 16px; font-weight: 600;")
        self.value_label = QLabel("—")
        set_theme_style(self.value_label, "font-size: 19px; font-weight: 600;")
        self.value_label.setTextFormat(Qt.TextFormat.PlainText)
        self.address_label.setTextFormat(Qt.TextFormat.PlainText)
        self.value_label.setMaximumWidth(240)
        self.value_label.setWordWrap(True)
        title_row.addWidget(self.address_label)
        title_row.addStretch()
        title_row.addWidget(QLabel(tr('Распознано:')))
        title_row.addWidget(self.value_label)
        edit_box.addLayout(title_row)
        value_row = QHBoxLayout()
        value_title = QLabel(tr('Правильное значение'))
        self.correct_value = QLineEdit()
        self.correct_value.setPlaceholderText(tr('Сверьте с оригиналом и введите значение'))
        self.correct_value.setAccessibleName(tr('Правильное значение выбранной ячейки или поля'))
        self.correct_value.setMinimumHeight(40)
        set_theme_style(self.correct_value, "font-size: 19px;")
        value_title.setBuddy(self.correct_value)
        self.correct_value.returnPressed.connect(self.confirm_current)
        self.confirm_shortcuts = []
        for key in ("Return", "Enter"):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.confirm_current)
            self.confirm_shortcuts.append(shortcut)
        value_row.addWidget(value_title)
        value_row.addWidget(self.correct_value, 1)
        edit_box.addLayout(value_row)
        actions = QHBoxLayout()
        self.confirm_button = QPushButton(tr('Подтвердить и далее') + '  [Enter]')
        self.confirm_button.setProperty("primary", True)
        self.confirm_button.setToolTip(tr('Сохранить значение и перейти к следующему спорному. Enter в поле ввода'))
        self.confirm_button.clicked.connect(self.confirm_current)
        self.next_button = QPushButton(tr('Следующее спорное →') + '  [Alt+→]')
        self.next_button.setShortcut("Alt+Right")
        self.next_button.setToolTip(tr('Перейти без подтверждения текущего значения. Alt+→'))
        self.next_button.clicked.connect(self.select_next_uncertain)
        actions.addWidget(self.confirm_button, 1)
        actions.addWidget(self.next_button, 1)
        edit_box.addLayout(actions)
        self.slow_summary = QLabel()
        self.slow_summary.setWordWrap(True)
        self.slow_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.slow_summary.hide()
        edit_box.addWidget(self.slow_summary)
        secondary = QHBoxLayout()
        self.exclude_row = QCheckBox(tr('Исключить эту строку'))
        self.exclude_row.setToolTip(tr('Оставить измерения строки пустыми. Снимите отметку, чтобы восстановить значения.'))
        self.exclude_row.toggled.connect(self._exclude_toggled)
        secondary.addWidget(self.exclude_row)
        self.exclude_column = QCheckBox(tr('Exclude this column'))
        self.exclude_column.setToolTip(tr('Leave this column’s measurements empty, keeping its header. Clear to restore the values.'))
        self.exclude_column.toggled.connect(self._exclude_column_toggled)
        secondary.addWidget(self.exclude_column)
        secondary.addStretch()
        self.details_toggle = QToolButton()
        self.details_toggle.setText(tr('Почему нужна проверка'))
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.toggled.connect(self._toggle_details)
        secondary.addWidget(self.details_toggle)
        edit_box.addLayout(secondary)
        self.confidence_label = QLabel()
        self.confidence_label.setWordWrap(True)
        self.confidence_label.setTextFormat(Qt.TextFormat.RichText)
        self.confidence_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.confidence_label.setMargin(10)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_scroll.setMinimumHeight(110)
        self.detail_scroll.setMaximumHeight(170)
        self.detail_scroll.setWidget(self.confidence_label)
        self.detail_scroll.hide()
        edit_box.addWidget(self.detail_scroll)
        edit_box.addStretch()
        inspector_layout.addLayout(edit_box, 3)
        self.review_split = QSplitter(Qt.Orientation.Vertical)
        self.review_split.setHandleWidth(6)
        self.review_split.setChildrenCollapsible(False)
        self.review_split.addWidget(split)
        self.review_split.addWidget(inspector)
        self.review_split.setStretchFactor(0, 1)
        self.review_split.setStretchFactor(1, 0)
        self.review_split.setSizes([560, 270])
        outer.addWidget(self.review_split, 1)

    def _toggle_details(self, expanded: bool) -> None:
        self.detail_scroll.setVisible(expanded)
        self.details_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        sizes = self.review_split.sizes()
        delta = 140 if expanded else -140
        self.review_split.setSizes([max(260, sizes[0] - delta), max(260, sizes[1] + delta)])

    def _result_tab_changed(self, index: int) -> None:
        table = self.table if index == 0 else self.fields_table
        if table.currentRow() >= 0:
            if index == 0:
                self._cell_clicked(table.currentRow(), table.currentColumn())
            else:
                self._field_clicked(table.currentRow(), 0)
        elif table.rowCount():
            table.setCurrentCell(0, 0)

    def clear_result(self) -> None:
        """Release the previous document's rasters when preparation replaces it."""
        self.result = None
        self.images = []
        self.canvas.set_document(None)
        self._clear_current()
        self.page_select.clear()
        self.table.setRowCount(0)
        self.fields_table.setRowCount(0)

    def set_result(self, images, result: JobResult) -> None:
        self.images = images
        self.result = result
        self.current_page = 0
        self.current_index = -1
        self.details_toggle.setChecked(False)
        self.page_select.blockSignals(True)
        self.page_select.clear()
        for index in range(min(len(images), len(result.pages))):
            self.page_select.addItem(tr('{p0} из {p1}', p0=index + 1, p1=len(result.pages)))
        self.page_select.setCurrentIndex(0)
        self.page_select.blockSignals(False)
        if images:
            self.canvas.set_document(self._read_page(0), result.template)
        self._populate()
        self.select_next_uncertain()

    def _page_changed(self, index: int) -> None:
        if not self.result or not 0 <= index < min(len(self.images), len(self.result.pages)):
            return
        image = self._read_page(index)
        if image is None:
            self.page_select.blockSignals(True)
            self.page_select.setCurrentIndex(self.current_page)
            self.page_select.blockSignals(False)
            return
        self.current_page = index
        self.current_index = -1
        self.canvas.set_document(image, self.result.template)
        self._populate()
        self.select_next_uncertain(page_only=True)

    def _read_page(self, index):
        try:
            return self.images[index]
        except Exception as exc:
            QMessageBox.warning(self, tr('Исходник недоступен'), str(exc))
            return None
    def _populate(self) -> None:
        if not self.result:
            return
        page = self.result.pages[self.current_page]
        positions = [(table.horizontalScrollBar().value(), table.verticalScrollBar().value())
                     for table in (self.table, self.fields_table)]
        self.table.blockSignals(True)
        self.fields_table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.fields_table.setUpdatesEnabled(False)
        has_fields = bool(page.fields)
        self.results_tabs.blockSignals(True)
        self.results_tabs.setTabEnabled(1, has_fields)
        self.results_tabs.blockSignals(False)
        self.results_tabs.setTabText(1, tr('Поля · {p0}', p0=len(page.fields)))
        self.fields_label.setVisible(has_fields)
        self.fields_table.setVisible(has_fields)
        self.fields_table.setRowCount(len(page.fields))
        for index, item in enumerate(page.fields):
            values = [item.name, item.final_text, fmt('{p0:.0%}', p0=item.confidence)]
            for column, value in enumerate(values):
                widget_item = QTableWidgetItem(value)
                if item.needs_review:
                    widget_item.setBackground(QColor("#FEF3C7"))
                self.fields_table.setItem(index, column, widget_item)

        self.table.setRowCount(self.result.template.rows)
        self.table.setColumnCount(self.result.template.columns)
        self.table.setHorizontalHeaderLabels([
            excel_column_name(index) if rule.name.startswith("Column ") else rule.name
            for index, rule in enumerate(self.result.template.column_rules)
        ])
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setMinimumSectionSize(34)
        for cell in page.cells:
            item = QTableWidgetItem("—" if cell.status == "excluded" else cell.final_text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if cell.status == "excluded":
                item.setBackground(QColor("#ECEFF3"))
                item.setForeground(QColor(MUTED))
            elif cell.needs_review:
                item.setBackground(QColor("#FEF3C7"))
                item.setForeground(QColor("#92400E"))
            elif cell.status in {"confirmed", "corrected"}:
                item.setBackground(QColor("#ECFDF3"))
            self.table.setItem(cell.row, cell.column, item)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(76)
        header.setDefaultSectionSize(96)
        self._update_status()
        self._apply_filter()
        self.refresh_theme()
        for table, (horizontal, vertical) in zip((self.table, self.fields_table), positions):
            table.blockSignals(False)
            table.setUpdatesEnabled(True)
            table.horizontalScrollBar().setValue(horizontal)
            table.verticalScrollBar().setValue(vertical)

    def _update_status(self) -> None:
        if not self.result:
            return
        count = self.result.unresolved_count
        self.unresolved_label.setText(tr('Нужно сверить: {p0}', p0=count) if count else tr('Готово к экспорту'))
        set_theme_style(self.unresolved_label, fmt('color: {p0}; font-weight: 700;', p0=AMBER if count else GREEN))
        self.confirm_all_button.setEnabled(count > 0)

    def _field_clicked(self, row: int, _column: int) -> None:
        if not self.result or not 0 <= row < len(self.result.pages[self.current_page].fields):
            return
        self.current_kind = "field"
        self.current_index = row
        self._show_current()

    def _cell_clicked(self, row: int, column: int) -> None:
        if not self.result:
            return
        page = self.result.pages[self.current_page]
        index = next((i for i, item in enumerate(page.cells) if item.row == row and item.column == column), -1)
        self.current_kind = "cell"
        self.current_index = index
        self._show_current()

    def _show_current(self) -> None:
        if not self.result or self.current_index < 0:
            return
        page = self.result.pages[self.current_page]
        if self.current_kind == "field":
            item = page.fields[self.current_index]
            region_index = next((i for i, region in enumerate(self.result.template.fields)
                                 if region.id == item.region_id), -1)
            self.canvas.set_active_field(region_index)
            address = tr('Поле · {p0}', p0=item.name)
            self.address_label.setText(address)
            self.exclude_row.blockSignals(True)
            self.exclude_row.setChecked(False)
            self.exclude_row.setEnabled(False)
            self.exclude_row.blockSignals(False)
            self.exclude_column.blockSignals(True)
            self.exclude_column.setChecked(False)
            self.exclude_column.setEnabled(False)
            self.exclude_column.blockSignals(False)
        else:
            item = page.cells[self.current_index]
            self.canvas.set_active_cell(item.row, item.column)
            address = tr('Ячейка {p0}{p1}', p0=excel_column_name(item.column), p1=item.row + 1)
            self.address_label.setText(address)
            self.exclude_row.blockSignals(True)
            prepared_exclusion = self.result.template.is_excluded(item.row, item.column)
            self.exclude_row.setEnabled(not prepared_exclusion and item.row >= self.result.template.header_rows)
            self.exclude_row.setChecked(item.row in page.excluded_rows)
            self.exclude_row.blockSignals(False)
            self.exclude_column.blockSignals(True)
            self.exclude_column.setEnabled(not prepared_exclusion and item.column >= self.result.template.row_label_columns)
            self.exclude_column.setChecked(item.column in page.excluded_columns)
            self.exclude_column.blockSignals(False)
        self.confirm_button.setEnabled(item.status != "excluded")
        self.correct_value.setEnabled(item.status != "excluded")
        if getattr(item, "status", "automatic") == "excluded":
            self.value_label.setText(tr('Пусто — исключено'))
        else:
            self.value_label.setText(item.final_text or tr('Пусто'))
        self.correct_value.setText(item.final_text)
        reasons = {
            "template_fixed_value": tr("Value from template; OCR skipped"),
            "slow_mode_selected": tr('Qwen и GLM согласовали другое число. Сравните исправление с оригиналом.'),
            "required_cell_empty": tr("Missing required value"),
            "invalid_numeric_format": tr("Invalid numeric notation"),
            "below_minimum": tr("The value is below the allowed minimum"),
            "above_maximum": tr("The value exceeds the allowed maximum"),
            "low_confidence": tr('Модель не уверена в прочтении — сравните значение с оригиналом'),
            "empty_prediction": tr('Не удалось прочитать значение'),
            "required_field_empty": tr('Обязательное поле осталось пустым'),
            "high_accuracy_consensus": tr('Модели точного режима согласовали результат'),
            "numeric_verification_required": tr('Цифры и десятичный разделитель прошли многоэтапную локальную проверку'),
            "model_disagreement": tr('Локальные модели предлагают разные прочтения'),
            "possible_missing_decimal": tr('Возможно, пропущен десятичный разделитель'),
            "possible_border_digit": tr('Граница таблицы могла быть прочитана как 1'),
            "crossed_out_row": tr('На изображении обнаружен непрерывный штрих через строку'),
            "auto_excluded_crossed_row": tr('Непрерывный штрих найден по всей строке; её значения автоматически оставлены пустыми'),
            "excluded_before_analysis": tr('Excluded before analysis. Restore this area in preparation and analyze again to read its values.'),
            "suspected_crossed_row": tr('Possible crossed-out row or shifted grid. Values were kept. Check the alignment before excluding the row.'),
            "non_numeric_mark_row": tr('В большинстве ячеек строки OCR видит буквы или символы вместо чисел; строка оставлена пустой'),
            "alternative_selected": tr('Выбрано альтернативное прочтение; исходный OCR показан выше'),
            "rule_selected_alternative": tr('Выбрано другое прочтение OCR, соответствующее вашему правилу'),
            "expected_range_selected_alternative": tr('Выбран реально прочитанный OCR-вариант из заданного обычного диапазона — проверьте оригинал'),
            "separator_inferred_from_rule": tr('Разделитель предложен по формату, а не прочитан моделью — проверьте оригинал'),
            "separator_reclassified_from_one": tr('Узкий штрих, прочитанный как 1, предложен как десятичный разделитель по правилу шаблона — проверьте оригинал'),
            "rule_conflict": tr('Ни одно прочтение не соответствует правилу'),
            "ambiguous_rule_proposals": tr('По формату возможны разные значения — автоматического выбора нет'),
            "decimal_places_mismatch": tr('Не совпадает число знаков после разделителя'),
            "decimal_part_required": tr('По шаблону десятичная часть обязательна'),
            "expected_integer": tr('Здесь разрешено только целое число'),
            "expected_decimal_number": tr('Здесь разрешено только простое число'),
            "value_not_allowed": tr('Значения нет в разрешённом списке'),
            "outside_expected_range": tr('Необычное значение: вне ожидаемого диапазона, но не запрещено'),
            "range_not_checkable": tr('Диапазон нельзя проверить для сложной записи'),
            "value_rule_review_required": tr('Значение проверено по правилу шаблона'),
            "constrained_decoder_used": tr('Проверены дополнительные варианты из внутренних вероятностей OCR'),
            "crop_retry_contributed": tr('Дополнительный вырез ячейки повлиял на результат'),
            "decimal_separator_detected": tr('Разделитель найден отдельным геометрическим детектором'),
            "separator_not_visually_confirmed": tr('OCR предложил дробь, но отдельный детектор не подтвердил разделитель'),
            "visible_digit_count_used": tr('Короткий вариант отклонён: на изображении виден дополнительный отдельный знак'),
            "decimal_boundary_inferred_from_glyphs": tr('Позиция дробной части найдена между видимыми знаками с учётом формата шаблона'),
            "unstable_consensus": tr('Недостаточно устойчивое согласие независимых этапов'),
            "digit_verifier_agrees": tr('Независимый распознаватель отдельных цифр согласен'),
            "digit_verifier_disagreement": tr('Дополнительный распознаватель цифр предложил другой вариант; основной консенсус сохранён'),
            "isolated_digit_consensus": tr('All three models agree on the separately read disputed digit'),
            "isolated_digit_disagreement": tr('Separate reading of the disputed digit remains uncertain'),
            "split_consensus_used": tr('Independent models agree when reading the number in parts'),
            "candidate_reranked": tr('Selected another OCR candidate using balanced model evidence; verify against the original'),
            "candidate_ranking_disagreement": tr('Several readings have similar support; verify the ambiguous digit'),
            "weak_sequence_evidence": tr('All numeric readings have weak image evidence; verify the whole value'),
            "adaptive_short_pass": tr('Two models agreed in the short analysis'),
            "adaptive_full_pass": tr('The complete High analysis was required'),
            "adaptive_review_required": tr('Experimental short analysis: compare with the original and confirm'),
            "multistage_cascade": tr('Выполнена многоэтапная локальная проверка'),
            "table_outlier": tr('Значение резко отличается от других сопоставимых ячеек этой строки'),
            "table_outlier_with_plausible_alternative": tr('Среди других прочтений OCR есть вариант, согласующийся со строкой — выберите его только после сверки с фото'),
        }
        audit_flags = {"numeric_verification_required", "high_accuracy_consensus", "constrained_decoder_used",
                       "crop_retry_contributed", "decimal_separator_detected", "digit_verifier_agrees",
                       "multistage_cascade", "adaptive_short_pass", "adaptive_full_pass",
                       "value_rule_review_required"}
        def section(title: str, lines: list[str]) -> str:
            if not lines:
                return ""
            return fmt('<p><b>{p0}</b></p><ul>', p0=escape(title)) + join_text('', (fmt('<li>{p0}</li>', p0=escape(line)) for line in lines)) + "</ul>"
        concerns = [reasons.get(flag, tr('Дополнительная отметка: {p0}', p0=flag)) for flag in item.flags if flag not in audit_flags and not flag.startswith("writer_style_")]
        checks = [reasons[flag] for flag in item.flags if flag in audit_flags]
        details = section(tr('Что проверить'), concerns or [tr('Сравните значение с фрагментом оригинала.')])
        rule_lines = []
        if self.current_kind == "cell" and getattr(item, "applied_rule", "") and "template_fixed_value" not in item.flags:
            rule, rule_name = self.result.template.value_constraints(item.row, item.column)
            rule_lines = [fmt("{p0}: {p1}", p0=rule_name, p1=rule.summary())]
        details += section(tr('Правило значения'), rule_lines)
        evidence = getattr(item, 'slow_mode_evidence', {})
        outcomes = {
            'changed': tr('Slow: Qwen and GLM agreed on a correction. Check it against the original.'),
            'agreed_unchanged': tr('Slow: both models support the original value.'),
            'baseline_supported': tr('Slow: Qwen supports the original value; no valid GLM reading is available.'),
            'disagreement': tr('Slow: the models disagree. The original value was kept.'),
            'qwen_unavailable': tr('Slow: Qwen returned no valid value. The original value was kept.'),
            'glm_unavailable': tr('Slow: GLM returned no valid value. The original value was kept.'),
        }
        outcome_text = outcomes.get(evidence.get('outcome'), '')
        self.slow_summary.setText(outcome_text)
        self.slow_summary.setVisible(bool(outcome_text))
        if evidence:
            details += section(tr('Перепроверка slow mode'), [
                tr('До перепроверки: {p0}', p0=evidence.get('baseline') or tr('Пусто')),
                fmt('Qwen: {p0}', p0=evidence.get('qwen') or '—'),
                fmt('GLM: {p0}', p0=evidence.get('glm') or '—'),
            ])
        readings = []
        if item.raw_text:
            readings.append(tr('Первичное распознавание: ') + item.raw_text)
        if getattr(item, "suggested_text", ""):
            readings.append(tr('До исключения строки: ') + item.suggested_text)
        if item.alternatives:
            readings.append(tr('Другие прочтения: ') + item.alternatives)
        details += section(tr('Варианты прочтения'), readings)
        details += section(tr('Выполненные проверки'), checks)
        details += section(tr('Технические сведения'), [tr('Оценка OCR: {p0:.0%}. Это оценка модели, а не измеренная точность.', p0=item.confidence)])
        self.confidence_label.setText(details)
        self.detail_scroll.verticalScrollBar().setValue(0)
        # Review always uses the same unmodified source page as the left canvas.
        # Ownership masks and inset OCR crops can erase genuine ink or leave border fragments.
        crop = None
        if self.canvas.image is not None:
            image = self.canvas.image
            if self.current_kind == "cell":
                crop = crop_cell(image, self.result.template, item.row, item.column, inset=0)
            else:
                region = next((r for r in self.result.template.fields if r.id == item.region_id), None)
                if region:
                    crop = crop_normalized(image, region.rect)
        self.crop_caption.setText(tr('Фрагмент оригинала · ') + address)
        if crop is not None and crop.size:
            self.crop_label.set_source_pixmap(pixmap_from_bgr(crop))
        else:
            self.crop_label.setText(tr('Фрагмент оригинала недоступен'))

    def refresh_theme(self) -> None:
        if not self.result:
            return
        c = colors()
        page = self.result.pages[self.current_page]
        for collection, table in ((page.cells, self.table), (page.fields, self.fields_table)):
            for index, result in enumerate(collection):
                bg, fg = c["surface"], c["text"]
                if result.status == "excluded":
                    bg, fg = c["excluded"], c["muted"]
                elif result.needs_review:
                    bg, fg = c["warning_bg"], c["warning"]
                elif result.status in {"confirmed", "corrected"}:
                    bg, fg = c["success_bg"], c["text"]
                items = ([table.item(result.row, result.column)] if table is self.table
                         else [table.item(index, column) for column in range(3)])
                for widget_item in items:
                    if widget_item:
                        widget_item.setBackground(QColor(bg))
                        widget_item.setForeground(QColor(fg))


    def confirm_current(self) -> None:
        if not self.result or self.current_index < 0:
            return
        page = self.result.pages[self.current_page]
        collection = page.fields if self.current_kind == "field" else page.cells
        item = collection[self.current_index]
        if item.status == "excluded":
            return
        review_actions.confirm_value(page, item, self.correct_value.text(), self.result.template)
        self._populate()
        self.resultChanged.emit(self.result)
        self.select_next_uncertain()

    def confirm_all_uncertain(self) -> None:
        """Accept every currently valid disputed value in the whole document."""
        if not self.result or not self.result.unresolved_count:
            return
        confirmable, blocked = review_actions.confirmable_items(self.result)
        if not confirmable:
            QMessageBox.information(
                self, tr('Нужны исправления'),
                tr('Оставшиеся значения нарушают правила шаблона. Исправьте их по одному.'),
            )
            return
        suffix = tr('\n\nЕщё {p0} знач. с ошибками правил останутся для ручной проверки.', p0=blocked) if blocked else ""
        answer = QMessageBox.question(
            self,
            tr('Подтвердить все спорные'),
            tr('Принять {p0} знач. точно в том виде, как они показаны?{p1}', p0=len(confirmable), p1=suffix),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for item in confirmable:
            item.status = "confirmed"
        self._populate()
        self.resultChanged.emit(self.result)
        self.select_next_uncertain()

    def _exclude_toggled(self, checked: bool) -> None:
        self._set_axis_excluded('row', checked)

    def _exclude_column_toggled(self, checked: bool) -> None:
        self._set_axis_excluded('column', checked)

    def _set_axis_excluded(self, axis: str, checked: bool) -> None:
        if not self.result or self.current_kind != "cell" or self.current_index < 0:
            return
        page = self.result.pages[self.current_page]
        cell = page.cells[self.current_index]
        review_actions.set_axis_excluded(
            page, self.result.template, axis, cell.row if axis == 'row' else cell.column, checked)
        self._populate()
        self._show_current()
        self.resultChanged.emit(self.result)

    def select_next_uncertain(self, *, page_only: bool = False) -> None:
        if not self.result:
            return
        target = review_actions.next_uncertain(
            self.result, self.current_page, self.current_kind, self.current_index, page_only=page_only)
        if target is None:
            self._clear_current()
            return
        page_index, kind, index, item = target
        if page_index != self.current_page:
            image = self._read_page(page_index)
            if image is None:
                return
            self.current_page = page_index
            self.page_select.blockSignals(True)
            self.page_select.setCurrentIndex(page_index)
            self.page_select.blockSignals(False)
            self.canvas.set_document(image, self.result.template)
            self._populate()
        self.current_kind, self.current_index = kind, index
        self.results_tabs.blockSignals(True)
        self.results_tabs.setCurrentIndex(1 if kind == "field" else 0)
        self.results_tabs.blockSignals(False)
        table = self.fields_table if kind == "field" else self.table
        table.blockSignals(True)
        table.setCurrentCell(index if kind == "field" else item.row, 0 if kind == "field" else item.column)
        table.blockSignals(False)
        table.scrollToItem(table.currentItem())
        self._show_current()
        self.correct_value.setFocus(Qt.FocusReason.OtherFocusReason)
        self.correct_value.selectAll()

    def _clear_current(self) -> None:
        self.current_index = -1
        self.canvas.set_active_field(-1)
        for table in (self.table, self.fields_table):
            table.blockSignals(True)
            table.clearSelection()
            table.setCurrentCell(-1, -1)
            table.blockSignals(False)
        self.crop_label.setText(tr('На этой странице всё проверено') if self.result and self.result.unresolved_count else tr('Сверка завершена'))
        self.crop_caption.setText(tr('Фрагмент оригинала'))
        self.address_label.setText(tr('Выберите ячейку или поле для просмотра'))
        self.value_label.setText("—")
        self.correct_value.clear()
        self.correct_value.setEnabled(False)
        self.confirm_button.setEnabled(False)
        self.exclude_row.blockSignals(True)
        self.exclude_row.setChecked(False)
        self.exclude_row.setEnabled(False)
        self.exclude_row.blockSignals(False)
        self.exclude_column.blockSignals(True)
        self.exclude_column.setChecked(False)
        self.exclude_column.setEnabled(False)
        self.exclude_column.blockSignals(False)
        self.confidence_label.clear()
        self.slow_summary.clear()
        self.slow_summary.hide()
        self.details_toggle.setChecked(False)

    def _apply_filter(self) -> None:
        if not self.result:
            return
        page = self.result.pages[self.current_page]
        filtering = self.uncertain_only.isChecked()
        uncertain_rows = {cell.row for cell in page.cells if cell.needs_review}
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, filtering and row not in uncertain_rows)
        for row, field in enumerate(page.fields):
            self.fields_table.setRowHidden(row, filtering and not field.needs_review)
