from __future__ import annotations

from .i18n import tr, fmt, join_text
import json
import os
from .i18n import escape_text as escape
from .i18n import language, set_language, SUPPORTED_LANGUAGES, bind
from bisect import bisect_right
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, QStandardPaths, QTimer, QSettings, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QHeaderView, QInputDialog, QMessageBox, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QVBoxLayout

from .localized_widgets import QAction, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QProgressDialog, QRadioButton, QPushButton, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QToolButton, QWidget

from .domain import CellRuleRegion, ColumnRule, FieldRegion, JobResult, NormalizedRect, TableTemplate, excel_column_name
from .constraints import ValueConstraints
from .rule_editor import ValueRuleDialog, optional_number
from . import __version__
from .exporter import export_job
from .imaging import GridDetection, crop_cell, crop_normalized, detect_grid, evenly_spaced_guides, load_document, rotate_document
from .ocr import LocalOcrEngine, OcrValue, canonical_numeric, constrain_reading
from .pipeline import process_document, suggest_standard_fields
from .storage import LocalStore
from .theme import apply_theme, colors, set_theme_style
from .template_matcher import TemplateMatch, rank_templates


BLUE = "#4F46E5"
LIGHT_BLUE = "#EFF6FF"
AMBER = "#D97706"
GREEN = "#15803D"
BORDER = "#D7DCE3"
TEXT = "#20242B"
MUTED = "#68707C"
SURFACE = "#F7F8FA"



def pixmap_from_bgr(image: np.ndarray) -> QPixmap:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    height, width, channels = rgb.shape
    qimage = QImage(rgb.data, width, height, width * channels, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qimage)


def notify(parent, message):
    window = parent.window()
    if not hasattr(window, "_toast"):
        window._toast = QLabel(window)
        window._toast.setWordWrap(True)
        window._toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        set_theme_style(window._toast, "background: #0F766E; color: white; padding: 14px; border-radius: 8px;")
        window._toast_timer = QTimer(window)
        window._toast_timer.setSingleShot(True)
        window._toast_timer.timeout.connect(window._toast.hide)
    toast = window._toast
    toast.setText(message)
    toast.setFixedWidth(min(420, max(220, window.width() - 40)))
    toast.adjustSize()
    toast.move(window.width() - toast.width() - 24, window.height() - toast.height() - 24)
    toast.show()
    toast.raise_()
    window._toast_timer.start(3500)


class OverlayRectItem(QGraphicsRectItem):
    def __init__(self, rect: QRectF, color: str, callback=None, label: str = "") -> None:
        super().__init__(rect)
        self.callback = callback
        self.label = label
        self.setPen(QPen(QColor(color), 2))
        fill = QColor(color)
        fill.setAlpha(20)
        self.setBrush(fill)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setZValue(9 if not label else 10)
        self.setAcceptHoverEvents(True)
        self._resize_edges = ""

    def shape(self):
        if self.label:
            return super().shape()
        path = QPainterPath(); path.addRect(self.rect())
        scale = self.scene().views()[0].transform().m11() if self.scene() and self.scene().views() else 1
        stroke = QPainterPathStroker(); stroke.setWidth(12 / max(scale, .01))
        return stroke.createStroke(path)

    def _edges(self, point):
        scale = self.scene().views()[0].transform().m11() if self.scene().views() else 1
        margin = 8 / max(scale, .01)
        rect = self.rect()
        return (("l" if abs(point.x() - rect.left()) < margin else "r" if abs(point.x() - rect.right()) < margin else "") +
                ("t" if abs(point.y() - rect.top()) < margin else "b" if abs(point.y() - rect.bottom()) < margin else ""))

    def hoverMoveEvent(self, event):
        edges = self._edges(event.pos())
        cursor = Qt.CursorShape.SizeAllCursor
        if len(edges) == 2:
            cursor = Qt.CursorShape.SizeFDiagCursor if edges in {"lt", "rb"} else Qt.CursorShape.SizeBDiagCursor
        elif edges:
            cursor = Qt.CursorShape.SizeHorCursor if edges in {"l", "r"} else Qt.CursorShape.SizeVerCursor
        self.setCursor(cursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        self._resize_edges = self._edges(event.pos())
        self._original_rect = QRectF(self.rect())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        bounds = self.mapRectFromScene(self.scene().sceneRect())
        if self._resize_edges:
            rect = QRectF(self._original_rect)
            point = event.pos()
            if "l" in self._resize_edges: rect.setLeft(max(bounds.left(), min(point.x(), rect.right() - 4)))
            if "r" in self._resize_edges: rect.setRight(min(bounds.right(), max(point.x(), rect.left() + 4)))
            if "t" in self._resize_edges: rect.setTop(max(bounds.top(), min(point.y(), rect.bottom() - 4)))
            if "b" in self._resize_edges: rect.setBottom(min(bounds.bottom(), max(point.y(), rect.top() + 4)))
            self.setRect(rect)
            event.accept()
        else:
            super().mouseMoveEvent(event)
            rect = self.mapRectToScene(self.rect())
            image = self.scene().sceneRect()
            self.moveBy(max(image.left() - rect.left(), min(0, image.right() - rect.right())),
                        max(image.top() - rect.top(), min(0, image.bottom() - rect.bottom())))

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            scale = painter.worldTransform().m11()
            size = 7 / max(scale, .01)
            painter.setBrush(QColor("white"))
            rect = self.rect()
            for point in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
                painter.drawRect(QRectF(point.x()-size/2, point.y()-size/2, size, size))

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if self.callback:
            self.callback(self.mapRectToScene(self.rect()))


class GuideLineItem(QGraphicsLineItem):
    def __init__(self, orientation: str, position: float, extent: tuple[float, float], bounds: QRectF, callback) -> None:
        self.orientation = orientation
        self.bounds = bounds
        self.callback = callback
        if orientation == "vertical":
            super().__init__(0, extent[0], 0, extent[1])
            self.setPos(position, 0)
        else:
            super().__init__(extent[0], 0, extent[1], 0)
            self.setPos(0, position)
        self.setPen(QPen(QColor("#3B82F6"), 1, Qt.PenStyle.DashLine))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCursor(Qt.CursorShape.SizeHorCursor if orientation == "vertical" else Qt.CursorShape.SizeVerCursor)
        self.setZValue(8)

    def shape(self):
        path = QPainterPath()
        path.moveTo(self.line().p1()); path.lineTo(self.line().p2())
        scale = self.scene().views()[0].transform().m11() if self.scene() and self.scene().views() else 1
        stroke = QPainterPathStroker(); stroke.setWidth(12 / max(scale, .01))
        return stroke.createStroke(path)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and isinstance(value, QPointF):
            if self.orientation == "vertical":
                return QPointF(max(self.bounds.left(), min(self.bounds.right(), value.x())), 0)
            return QPointF(0, max(self.bounds.top(), min(self.bounds.bottom(), value.y())))
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.callback(self.pos().x() if self.orientation == "vertical" else self.pos().y())


class DocumentCanvas(QGraphicsView):
    regionCreated = Signal(str, QRectF)
    cellSelectionChanged = Signal(object)
    geometryChanged = Signal()
    fieldSelected = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(colors()["canvas"]))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.image: np.ndarray | None = None
        self.template: TableTemplate | None = None
        self.draw_kind = ""
        self.draw_start: QPointF | None = None
        self.preview_rect: QGraphicsRectItem | None = None
        self.show_grid = True
        self.show_fields = True
        self.show_rules = False
        self.active_rule = -1
        self.active_field = -1
        self.active_cell: tuple[int, int] | None = None
        self.read_only = False
        self._active_overlay = None
        self.guide_callback = None
        self.selection_enabled = False
        self.selected_cells: set[tuple[int, int]] = set()
        self.selection_anchor: tuple[int, int] | None = None
        self._selection_drag_start: tuple[int, int] | None = None
        self._selection_drag_base: set[tuple[int, int]] = set()
        self._selection_subtract = False
        self._selection_history: list[frozenset[tuple[int, int]]] = [frozenset()]
        self._selection_history_index = 0

    def set_document(self, image: np.ndarray, template: TableTemplate | None = None) -> None:
        self.image = image
        self.template = template
        self.selected_cells = set()
        self.active_cell = None
        self.active_field = -1
        self.selection_anchor = None
        self._selection_history = [frozenset()]
        self._selection_history_index = 0
        self.redraw()
        self.fit_document()

    def set_active_cell(self, row: int | None, column: int | None = None) -> None:
        """Highlight one result cell over the source image."""
        if row is None or column is None or not self.template:
            active = None
        elif 0 <= row < self.template.rows and 0 <= column < self.template.columns:
            active = (row, column)
        else:
            active = None
        if active == self.active_cell:
            return
        self.active_cell = active
        self.active_field = -1
        self._update_active_overlay()

    def set_active_field(self, index: int = -1) -> None:
        self.active_cell = None
        self.active_field = index
        self._update_active_overlay()

    def _update_active_overlay(self) -> None:
        if self._active_overlay is not None:
            self.scene().removeItem(self._active_overlay)
            self._active_overlay = None
        if self.image is None or not self.template:
            return
        height, width = self.image.shape[:2]
        if self.active_cell is not None:
            row, column = self.active_cell
            left, right = self.template.column_guides[column:column + 2]
            top, bottom = self.template.row_guides[row:row + 2]
            rect = QRectF(left * width, top * height, (right - left) * width, (bottom - top) * height)
        elif 0 <= self.active_field < len(self.template.fields):
            rect = QRectF(*self.template.fields[self.active_field].rect.to_pixels(self.image.shape))
        else:
            return
        color = QColor("#F97316")
        fill = QColor(color)
        fill.setAlpha(40)
        pen = QPen(color, 2)
        pen.setCosmetic(True)
        self._active_overlay = self.scene().addRect(rect, pen, fill)
        self._active_overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._active_overlay.setZValue(30)
        if self.read_only:
            self.ensureVisible(rect, 24, 24)

    def refresh_theme(self) -> None:
        self.setBackgroundBrush(QColor(colors()["canvas"]))

    def fit_document(self) -> None:
        if not self.scene() or self.scene().itemsBoundingRect().isEmpty():
            return
        target = self.scene().itemsBoundingRect()
        if self.image is not None and self.template is not None:
            image_height, image_width = self.image.shape[:2]
            x, y, width, height = self.template.table_rect.to_pixels(self.image.shape)
            left = max(0, x - image_width * 0.025)
            right = min(image_width, x + width + image_width * 0.025)
            top = max(0, y - image_height * 0.10)
            bottom = min(image_height, y + height + image_height * 0.08)
            target = QRectF(left, top, right - left, bottom - top)
        self.fitInView(target, Qt.AspectRatioMode.KeepAspectRatio)

    def zoom_by(self, factor: float) -> None:
        self.scale(factor, factor)

    def begin_draw(self, kind: str) -> None:
        self.draw_kind = kind
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_selection_enabled(self, enabled: bool) -> None:
        self.selection_enabled = enabled
        self.setDragMode(QGraphicsView.DragMode.NoDrag if enabled else QGraphicsView.DragMode.ScrollHandDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor if enabled else Qt.CursorShape.OpenHandCursor)
        self.redraw()

    def select_cells(self, cells: set[tuple[int, int]], *, emit: bool = True, record: bool = True) -> None:
        if not self.template:
            return
        self.selected_cells = {
            (row, column) for row, column in cells
            if 0 <= row < self.template.rows and 0 <= column < self.template.columns
        }
        if self.selected_cells:
            self.selection_anchor = min(self.selected_cells)
        if record:
            self._record_selection()
        self.redraw()
        if emit:
            self.cellSelectionChanged.emit(set(self.selected_cells))

    def _record_selection(self) -> None:
        current = frozenset(self.selected_cells)
        if self._selection_history[self._selection_history_index] == current:
            return
        self._selection_history = self._selection_history[: self._selection_history_index + 1]
        self._selection_history.append(current)
        self._selection_history_index += 1

    def undo_selection(self) -> None:
        if self._selection_history_index <= 0:
            return
        self._selection_history_index -= 1
        self.select_cells(set(self._selection_history[self._selection_history_index]), record=False)

    def redo_selection(self) -> None:
        if self._selection_history_index >= len(self._selection_history) - 1:
            return
        self._selection_history_index += 1
        self.select_cells(set(self._selection_history[self._selection_history_index]), record=False)

    def select_region(self, row_start: int, row_end: int, column_start: int, column_end: int) -> None:
        self.select_cells({
            (row, column)
            for row in range(row_start, row_end + 1)
            for column in range(column_start, column_end + 1)
        })

    def select_all_data(self) -> None:
        if self.template:
            self.select_region(
                self.template.header_rows, self.template.rows - 1,
                self.template.row_label_columns, self.template.columns - 1,
            )

    def _header_band(self) -> float:
        if not self.template or self.image is None or not self.template.rows or not self.template.columns:
            return 28.0
        height, width = self.image.shape[:2]
        cell_width = (self.template.column_guides[-1] - self.template.column_guides[0]) * width / self.template.columns
        cell_height = (self.template.row_guides[-1] - self.template.row_guides[0]) * height / self.template.rows
        return max(20.0, min(42.0, min(cell_width, cell_height) * .55))

    def _cell_or_header_at(self, point: QPointF) -> tuple[str, int, int] | None:
        if not self.template or self.image is None:
            return None
        height, width = self.image.shape[:2]
        xs = [value * width for value in self.template.column_guides]
        ys = [value * height for value in self.template.row_guides]
        band = self._header_band()
        if xs[0] <= point.x() < xs[-1] and ys[0] <= point.y() < ys[-1]:
            column = bisect_right(xs, point.x()) - 1
            row = bisect_right(ys, point.y()) - 1
            return "cell", row, column
        if xs[0] <= point.x() < xs[-1] and ys[0] - band <= point.y() < ys[0]:
            return "column", -1, bisect_right(xs, point.x()) - 1
        if ys[0] <= point.y() < ys[-1] and xs[0] - band <= point.x() < xs[0]:
            return "row", bisect_right(ys, point.y()) - 1, -1
        if xs[0] - band <= point.x() < xs[0] and ys[0] - band <= point.y() < ys[0]:
            return "all", -1, -1
        return None

    @staticmethod
    def _rectangle_cells(start: tuple[int, int], end: tuple[int, int]) -> set[tuple[int, int]]:
        row_start, row_end = sorted((start[0], end[0]))
        column_start, column_end = sorted((start[1], end[1]))
        return {(row, column) for row in range(row_start, row_end + 1) for column in range(column_start, column_end + 1)}

    def _apply_live_selection(self, end: tuple[int, int]) -> None:
        if self._selection_drag_start is None:
            return
        block = self._rectangle_cells(self._selection_drag_start, end)
        self.selected_cells = self._selection_drag_base - block if self._selection_subtract else self._selection_drag_base | block
        self.redraw()

    def redraw(self) -> None:
        self._active_overlay = None
        self.scene().clear()
        if self.image is None:
            return
        pixmap_item = QGraphicsPixmapItem(pixmap_from_bgr(self.image))
        self.scene().addItem(pixmap_item)
        self.scene().setSceneRect(pixmap_item.boundingRect())
        if not self.template:
            return
        height, width = self.image.shape[:2]
        x, y, rect_width, rect_height = self.template.table_rect.to_pixels(self.image.shape)
        table_bounds = QRectF(x, y, rect_width, rect_height)
        table_item = OverlayRectItem(table_bounds, BLUE, self._table_moved)
        if self.show_rules or self.read_only:
            table_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            table_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.scene().addItem(table_item)

        if self.show_grid:
            for index, normalized in enumerate(self.template.column_guides):
                item = GuideLineItem(
                    "vertical", normalized * width, (table_bounds.top(), table_bounds.bottom()), table_bounds,
                    lambda value, guide=index: self._guide_moved("column", guide, value / width),
                )
                self.scene().addItem(item)
            for index, normalized in enumerate(self.template.row_guides):
                item = GuideLineItem(
                    "horizontal", normalized * height, (table_bounds.left(), table_bounds.right()), table_bounds,
                    lambda value, guide=index: self._guide_moved("row", guide, value / height),
                )
                self.scene().addItem(item)

        if self.show_fields:
            for index, region in enumerate(self.template.fields):
                rx, ry, rw, rh = region.rect.to_pixels(self.image.shape)
                item = OverlayRectItem(
                    QRectF(rx, ry, rw, rh), region.color,
                    lambda rect, field_index=index: self._field_moved(field_index, rect), region.name,
                )
                if self.read_only:
                    item.setFlags(QGraphicsItem.GraphicsItemFlag(0))
                    item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                if index == self.active_field:
                    item.setPen(QPen(QColor(region.color), 3))
                    item.setSelected(True)
                self.scene().addItem(item)

        self._update_active_overlay()

        if self.show_rules:
            for index, region in enumerate(self.template.cell_rules):
                if not (0 <= region.row_start <= region.row_end < self.template.rows and 0 <= region.column_start <= region.column_end < self.template.columns):
                    continue
                x1, x2 = self.template.column_guides[region.column_start], self.template.column_guides[region.column_end + 1]
                y1, y2 = self.template.row_guides[region.row_start], self.template.row_guides[region.row_end + 1]
                rect = QRectF(x1 * width, y1 * height, (x2 - x1) * width, (y2 - y1) * height)
                color = QColor(region.color)
                item = self.scene().addRect(rect, QPen(color, 4 if index == self.active_rule else 2))
                fill = QColor(color); fill.setAlpha(32); item.setBrush(fill)
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                text = self.scene().addSimpleText(fmt('{p0}. {p1}', p0=index + 1, p1=region.name))
                text.setPos(rect.left() + 5, rect.top() + 3); text.setBrush(color)
                text.setScale(1.15); text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        if self.selection_enabled:
            static_pen = QPen(QColor("#CBD5E1"), 1)
            for normalized in self.template.column_guides:
                self.scene().addLine(normalized * width, table_bounds.top(), normalized * width, table_bounds.bottom(), static_pen).setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            for normalized in self.template.row_guides:
                self.scene().addLine(table_bounds.left(), normalized * height, table_bounds.right(), normalized * height, static_pen).setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            band = self._header_band()
            header_fill = QColor("#F8FAFC")
            header_pen = QPen(QColor("#CBD5E1"), 1)
            for column in range(self.template.columns):
                left, right = self.template.column_guides[column] * width, self.template.column_guides[column + 1] * width
                self.scene().addRect(QRectF(left, table_bounds.top() - band, right - left, band), header_pen, header_fill).setZValue(20)
                label = self.scene().addSimpleText(excel_column_name(column)); label.setBrush(QColor(MUTED)); label.setZValue(21)
                label.setPos((left + right - label.boundingRect().width()) / 2, table_bounds.top() - band + (band - label.boundingRect().height()) / 2)
            for row in range(self.template.rows):
                top, bottom = self.template.row_guides[row] * height, self.template.row_guides[row + 1] * height
                self.scene().addRect(QRectF(table_bounds.left() - band, top, band, bottom - top), header_pen, header_fill).setZValue(20)
                label = self.scene().addSimpleText(str(row + 1)); label.setBrush(QColor(MUTED)); label.setZValue(21)
                label.setPos(table_bounds.left() - band + (band - label.boundingRect().width()) / 2, (top + bottom - label.boundingRect().height()) / 2)
            self.scene().addRect(QRectF(table_bounds.left() - band, table_bounds.top() - band, band, band), header_pen, header_fill).setZValue(20)
            selection_fill = QColor("#2563EB"); selection_fill.setAlpha(38)
            selection_pen = QPen(QColor("#2563EB"), 2)
            for row, column in self.selected_cells:
                left, right = self.template.column_guides[column] * width, self.template.column_guides[column + 1] * width
                top, bottom = self.template.row_guides[row] * height, self.template.row_guides[row + 1] * height
                item = self.scene().addRect(QRectF(left, top, right - left, bottom - top), selection_pen, selection_fill)
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton); item.setZValue(25)
            for row in range(self.template.rows):
                for column in range(self.template.columns):
                    left, right = self.template.column_guides[column] * width, self.template.column_guides[column + 1] * width
                    top, bottom = self.template.row_guides[row] * height, self.template.row_guides[row + 1] * height
                    hover = self.scene().addRect(QRectF(left, top, right - left, bottom - top), QPen(Qt.PenStyle.NoPen))
                    rule, source = self.template.value_constraints(row, column)
                    tooltip = fmt('{p0}{p1}\n{p2}\n{p3}', p0=excel_column_name(column), p1=row + 1, p2=source, p3=rule.summary())
                    hover.setToolTip(tooltip)
                    bind(hover, 'tooltip', 'setToolTip', (tooltip,))
                    hover.setAcceptHoverEvents(True)
                    hover.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                    hover.setZValue(24)

    def _table_moved(self, rect: QRectF) -> None:
        if self.image is None or not self.template:
            return
        old = self.template.table_rect
        self.template.table_rect = NormalizedRect.from_pixels(
            (round(rect.x()), round(rect.y()), round(rect.width()), round(rect.height())), self.image.shape
        )
        new = self.template.table_rect
        self.template.column_guides = [new.x + (v - old.x) / old.width * new.width for v in self.template.column_guides]
        self.template.row_guides = [new.y + (v - old.y) / old.height * new.height for v in self.template.row_guides]
        self.geometryChanged.emit()
        QTimer.singleShot(0, self.redraw)

    def _field_moved(self, index: int, rect: QRectF) -> None:
        if self.image is None or not self.template or index >= len(self.template.fields):
            return
        self.template.fields[index].rect = NormalizedRect.from_pixels(
            (round(rect.x()), round(rect.y()), round(rect.width()), round(rect.height())), self.image.shape
        )
        self.active_field = index
        self.geometryChanged.emit()
        QTimer.singleShot(0, lambda: self.fieldSelected.emit(index))

    def _guide_moved(self, kind: str, index: int, value: float) -> None:
        if not self.template:
            return
        guides = self.template.column_guides if kind == "column" else self.template.row_guides
        lower = guides[index - 1] + 0.002 if index > 0 else 0
        upper = guides[index + 1] - 0.002 if index < len(guides) - 1 else 1
        guides[index] = max(0, min(1, max(lower, min(upper, value))))
        guides.sort()
        rect = self.template.table_rect
        if kind == "column":
            self.template.table_rect = NormalizedRect(guides[0], rect.y, guides[-1] - guides[0], rect.height)
        else:
            self.template.table_rect = NormalizedRect(rect.x, guides[0], rect.width, guides[-1] - guides[0])
        self.geometryChanged.emit()
        QTimer.singleShot(0, self.redraw)

    def mousePressEvent(self, event) -> None:
        if self.draw_kind and event.button() == Qt.MouseButton.LeftButton:
            self.draw_start = self.mapToScene(event.position().toPoint())
            self.preview_rect = self.scene().addRect(QRectF(self.draw_start, self.draw_start), QPen(QColor(BLUE), 2, Qt.PenStyle.DashLine))
            return
        if self.selection_enabled and event.button() == Qt.MouseButton.LeftButton:
            hit = self._cell_or_header_at(self.mapToScene(event.position().toPoint()))
            if hit is None:
                return
            kind, row, column = hit
            modifiers = event.modifiers()
            additive = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
            if kind == "all":
                self.select_region(0, self.template.rows - 1, 0, self.template.columns - 1)
                return
            if kind == "row":
                cells = {(row, value) for value in range(self.template.columns)}
                self.select_cells((self.selected_cells ^ cells) if additive else cells)
                return
            if kind == "column":
                cells = {(value, column) for value in range(self.template.rows)}
                self.select_cells((self.selected_cells ^ cells) if additive else cells)
                return
            current = (row, column)
            start = self.selection_anchor if modifiers & Qt.KeyboardModifier.ShiftModifier and self.selection_anchor else current
            self._selection_drag_start = start
            self._selection_subtract = additive and current in self.selected_cells
            self._selection_drag_base = set(self.selected_cells) if additive else set()
            if not additive:
                self.selection_anchor = start
            self._apply_live_selection(current)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.draw_start is not None and self.preview_rect:
            current = self.mapToScene(event.position().toPoint())
            self.preview_rect.setRect(QRectF(self.draw_start, current).normalized().intersected(self.sceneRect()))
            return
        if self.selection_enabled and self._selection_drag_start is not None:
            hit = self._cell_or_header_at(self.mapToScene(event.position().toPoint()))
            if hit and hit[0] == "cell":
                self._apply_live_selection((hit[1], hit[2]))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.draw_start is not None and self.preview_rect:
            rect = self.preview_rect.rect()
            self.scene().removeItem(self.preview_rect)
            kind = self.draw_kind
            self.draw_kind = ""
            self.draw_start = None
            self.preview_rect = None
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.unsetCursor()
            if rect.width() >= 10 and rect.height() >= 10:
                self.regionCreated.emit(kind, rect)
            return
        if self.selection_enabled and self._selection_drag_start is not None:
            self._selection_drag_start = None
            self._selection_drag_base = set()
            self._selection_subtract = False
            self._record_selection()
            self.cellSelectionChanged.emit(set(self.selected_cells))
            return
        super().mouseReleaseEvent(event)


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

    def __init__(self) -> None:
        super().__init__()
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
        formats = QLabel(tr('или перетащите сюда PDF, PNG, JPG либо TIFF'))
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
        self.recent = QTableWidget(0, 3)
        self.recent.setHorizontalHeaderLabels([tr('Файл'), tr('Изменён'), tr('Состояние')])
        self.recent.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.recent.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.recent.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.recent.verticalHeader().hide()
        self.recent.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recent.doubleClicked.connect(lambda index: self.recentOpened.emit(self.recent.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.recent, 2)

    def set_recent(self, rows: list[dict[str, str]]) -> None:
        self.recent.setRowCount(len(rows))
        labels = {"imported": tr('Импортирован'), "review": tr('Нужна сверка'), "ready": tr('Готов к экспорту')}
        for row_index, row in enumerate(rows):
            name = QTableWidgetItem(row["source_name"])
            name.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.recent.setItem(row_index, 0, name)
            self.recent.setItem(row_index, 1, QTableWidgetItem(row["updated_at"].replace("T", " ")[:16]))
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
                tr('{p0} · v{p1} — совпадение {p2}%\n', p0=match.template.name, p1=match.template.template_version, p2=percent)
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
        ordered = sorted(templates, key=lambda item: (item.name.casefold(), -item.template_version))
        signature = tuple((item.id, item.template_version, item.name, len(item.cell_rules)) for item in ordered)
        if signature == self._template_signature:
            return
        self._template_signature = signature
        self.list.clear()
        for template in ordered:
            sample = tr('есть') if template.reference_source_path and Path(template.reference_source_path).exists() else tr('нет')
            item = QListWidgetItem(
                tr('{p0} · v{p1}\nGrid: {p2} × {p3} · Regions: {p4} · Sample: {p5}', p0=template.name, p1=template.template_version, p2=template.rows, p3=template.columns, p4=len(template.cell_rules), p5=sample)
            )
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            self.list.addItem(item)
        if ordered:
            self.list.setCurrentRow(0)


class TablePage(QWidget):
    continueRequested = Signal(object)
    saveVersionRequested = Signal(object)
    saveTemplateRequested = Signal(object)
    templateChanged = Signal(object)
    savedTemplateRequested = Signal(str)
    rotationRequested = Signal(int)

    def __init__(self, mode: str = "document") -> None:
        super().__init__()
        self.mode = mode
        self.image: np.ndarray | None = None
        self.template: TableTemplate | None = None
        self._loading_form = False
        self._selected_cells: set[tuple[int, int]] = set()
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
            save_version = QPushButton(tr('Сохранить новую версию'))
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
        tools.addStretch()
        left_layout.addLayout(tools)
        self.canvas = DocumentCanvas()
        self.canvas.regionCreated.connect(self._region_created)
        self.canvas.geometryChanged.connect(lambda: self.templateChanged.emit(self.template))
        self.canvas.fieldSelected.connect(lambda index: self.field_list.setCurrentRow(index))
        self.canvas.cellSelectionChanged.connect(self._selection_changed)
        left_layout.addWidget(self.canvas)
        split.addWidget(left)

        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(400)
        self.tabs.setMaximumWidth(640)
        self.tabs.addTab(self._build_grid_tab(), tr('Сетка'))
        self.tabs.addTab(self._build_fields_tab(), tr('Поля'))
        self.tabs.addTab(self._build_cell_rules_tab(), tr('Правила'))
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
        layout.addWidget(self.high_accuracy)
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
        layout.addWidget(detect)
        layout.addWidget(redraw)
        save_as_template = QPushButton(tr('Сохранить как новую версию шаблона'))
        save_as_template.clicked.connect(lambda: self.saveTemplateRequested.emit(self.template) if self.template else None)
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
        self.rows_spin.valueChanged.connect(self._grid_count_changed)
        self.columns_spin.valueChanged.connect(self._grid_count_changed)
        self.template_name.editingFinished.connect(self._save_common)
        self.header_rows_spin.valueChanged.connect(self._save_common)
        self.row_labels_spin.valueChanged.connect(self._save_common)
        self.crossed.toggled.connect(self._save_common)
        return tab

    def set_recognition_running(self, running: bool) -> None:
        self.continue_button.setEnabled(not running)
        self.continue_button.setText(tr('Распознавание выполняется…') if running else tr('Сохранить сетку и продолжить'))

    def set_saved_templates(self, templates: list[TableTemplate]) -> None:
        selected_id = self.saved_template_select.currentData()
        self.saved_template_select.clear()
        self.saved_template_select.addItem(tr('Выберите сохранённый шаблон…'), "")
        for template in templates:
            self.saved_template_select.addItem(template.name, template.id)
        if selected_id:
            index = self.saved_template_select.findData(selected_id)
            self.saved_template_select.setCurrentIndex(max(0, index))

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
        for widget in (self.quick_minimum, self.quick_maximum): widget.textChanged.connect(self._quick_preview)
        self.quick_places.valueChanged.connect(self._quick_preview)
        self.quick_kind.currentIndexChanged.connect(self._quick_kind_changed)
        self.quick_require_decimal.toggled.connect(self._quick_preview)

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
        self._quick_preview()

    def _read_quick_rule(self) -> ValueConstraints:
        return ValueConstraints(
            value_format=str(self.quick_kind.currentData()),
            minimum=optional_number(self.quick_minimum.text()),
            maximum=optional_number(self.quick_maximum.text()),
            decimal_places=None if self.quick_places.value() < 0 else self.quick_places.value(),
            allow_empty=self.quick_allow_empty.isChecked(),
            require_decimal=self.quick_require_decimal.isChecked(),
        )

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

    def _append_rule_regions(self, name: str, rule: ValueConstraints, color: str) -> None:
        if not self.template or not self._selected_cells:
            QMessageBox.information(self, tr('Выделение'), tr('Сначала выберите ячейку или диапазон на таблице.'))
            return
        for row_start, row_end, column_start, column_end in self._selection_rectangles(self._selected_cells):
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
            self._append_rule_regions(self.quick_rule_name.text(), dialog.rule, str(self.quick_color.currentData()))

    def _refresh_cell_rules(self) -> None:
        self.rule_list.blockSignals(True); self.rule_list.clear()
        if self.template:
            for index, region in enumerate(self.template.cell_rules):
                outside = region.row_end >= self.template.rows or region.column_end >= self.template.columns
                self.rule_list.addItem(fmt('{p0}. {p1}\n{p2}', p0=index + 1, p1=region.name, p2=region.address()) + (tr(' — вне сетки!') if outside else ""))
        self.rule_list.blockSignals(False)
        self.rule_list.setCurrentRow(self.rule_list.count() - 1)
        self._rule_selected(self.rule_list.currentRow())

    def _rule_selected(self, index: int) -> None:
        self.canvas.active_rule = index
        if self.template and 0 <= index < len(self.template.cell_rules):
            region = self.template.cell_rules[index]
            self.rule_summary.setText(region.constraints.summary())
            self.canvas.select_region(region.row_start, region.row_end, region.column_start, region.column_end)
            self.quick_rule_name.setText(region.name)
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
        self.image = image
        self.template = template
        self.version_label.setText(fmt('v{p0}', p0=template.template_version))
        saved_index = self.saved_template_select.findData(template.id)
        if saved_index >= 0:
            self.saved_template_select.setCurrentIndex(saved_index)
        template.ensure_column_rules()
        self._loading_form = True
        self.template_name.setText(template.name)
        self.rows_spin.setValue(template.rows)
        self.columns_spin.setValue(template.columns)
        self.header_rows_spin.setValue(template.header_rows)
        self.row_labels_spin.setValue(template.row_label_columns)
        self.crossed.setChecked(template.detect_crossed_rows)
        self._loading_form = False
        self._refresh_fields()
        self._refresh_columns()
        self._refresh_cell_rules()
        self.canvas.show_grid = True
        self.canvas.show_fields = False
        self.canvas.show_rules = False
        self.canvas.set_document(image, template)
        self._selection_changed(set())
        self._tab_changed(self.tabs.currentIndex())

    def _fit_canvas(self) -> None:
        self.canvas.resetTransform()
        self.canvas.fit_document()

    def _tab_changed(self, index: int) -> None:
        self.canvas.show_grid = index == 0
        self.canvas.show_fields = index == 1
        self.canvas.show_rules = index == 2
        self.canvas.set_selection_enabled(index == 2)

    def _save_common(self) -> None:
        if self._loading_form or not self.template:
            return
        self.template.name = str(self.template_name.text().strip() or "New template")
        self.template.header_rows = min(self.header_rows_spin.value(), max(0, self.template.rows - 1))
        self.template.row_label_columns = min(self.row_labels_spin.value(), max(0, self.template.columns - 1))
        self.template.detect_crossed_rows = self.crossed.isChecked()
        self.template.ensure_column_rules()
        for index in range(min(self.template.row_label_columns, len(self.template.column_rules))):
            self.template.column_rules[index].role = "row_label"
        self.templateChanged.emit(self.template)

    def _grid_count_changed(self) -> None:
        if self._loading_form or not self.template:
            return
        self.template.row_guides, self.template.column_guides = evenly_spaced_guides(
            self.template.table_rect, self.rows_spin.value(), self.columns_spin.value()
        )
        self.template.ensure_column_rules()
        self._refresh_columns()
        self._refresh_cell_rules()
        self.canvas.redraw()
        self.templateChanged.emit(self.template)

    def detect_again(self) -> None:
        if self.image is None:
            return
        try:
            detection = detect_grid(self.image)
        except ValueError as exc:
            QMessageBox.warning(self, tr('Сетка не найдена'), str(exc))
            return
        self.template.table_rect = detection.table_rect
        self.template.row_guides = detection.row_guides
        self.template.column_guides = detection.column_guides
        self.template.ensure_column_rules()
        self.set_document(self.image, self.template)
        self.grid_warning.setText(join_text(' ', detection.warnings))
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
            self.field_list.addItem(fmt('{p0}{p1}', p0=region.name, p1=value))
        maximum = max(0, self.template.columns - 1)
        self.field_column_start.setRange(0, maximum)
        self.field_column_end.setRange(0, maximum)

    def _field_selected(self, index: int) -> None:
        if not self.template or index < 0 or index >= len(self.template.fields):
            return
        region = self.template.fields[index]
        self._loading_form = True
        self.field_name.setText(region.name)
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
                self.column_select.addItem(fmt('{p0}: {p1}', p0=index + 1, p1=rule.name))
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
        self.template.schema_version = 2
        self._refresh_columns()
        self.column_select.setCurrentIndex(index)
        self.templateChanged.emit(self.template)
        notify(self, tr("Column settings saved"))

    def _continue(self) -> None:
        self._save_common()
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
        self._save_common()
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


class RecognitionWorker(QThread):
    progress = Signal(int, int, object)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, images: list[np.ndarray], source_path: str, template: TableTemplate, crop_root: Path, high_accuracy: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.images = images
        self.source_path = source_path
        self.template = template
        self.crop_root = crop_root
        self.high_accuracy = high_accuracy

    def run(self) -> None:
        try:
            def report(value: int, maximum: int, label: str) -> None:
                if self.isInterruptionRequested():
                    raise InterruptedError
                self.progress.emit(value, maximum, label)

            result = process_document(
                self.images, self.source_path, self.template,
                report, self.crop_root, self.high_accuracy,
            )
            self.completed.emit(result)
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:  # UI boundary: surface actionable error
            self.failed.emit(str(exc))


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
        self.images: list[np.ndarray] = []
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
        self.fields_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
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
        value_row.addWidget(value_title)
        value_row.addWidget(self.correct_value, 1)
        edit_box.addLayout(value_row)
        self.writer_suggestion_button = QPushButton(tr('Подставить вариант почерка'))
        self.writer_suggestion_button.setToolTip(tr('Подставить предложенное значение для сверки; сохранится после подтверждения. Alt+A'))
        self.writer_suggestion_button.setShortcut("Alt+A")
        self.writer_suggestion_button.clicked.connect(self._use_writer_suggestion)
        self.writer_suggestion_button.hide()
        edit_box.addWidget(self.writer_suggestion_button)
        actions = QHBoxLayout()
        self.confirm_button = QPushButton(tr('Подтвердить и далее'))
        self.confirm_button.setProperty("primary", True)
        self.confirm_button.setToolTip(tr('Сохранить значение и перейти к следующему спорному. Enter в поле ввода'))
        self.confirm_button.clicked.connect(self.confirm_current)
        self.next_button = QPushButton(tr('Следующее спорное →'))
        self.next_button.setShortcut("Alt+Right")
        self.next_button.setToolTip(tr('Перейти без подтверждения текущего значения. Alt+→'))
        self.next_button.clicked.connect(self.select_next_uncertain)
        actions.addWidget(self.confirm_button, 1)
        actions.addWidget(self.next_button, 1)
        edit_box.addLayout(actions)
        secondary = QHBoxLayout()
        self.exclude_row = QCheckBox(tr('Исключить эту строку'))
        self.exclude_row.setToolTip(tr('Оставить измерения строки пустыми. Снимите отметку, чтобы восстановить значения.'))
        self.exclude_row.toggled.connect(self._exclude_toggled)
        secondary.addWidget(self.exclude_row)
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

    def set_result(self, images: list[np.ndarray], result: JobResult) -> None:
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
            self.canvas.set_document(images[0], result.template)
        self._populate()
        self.select_next_uncertain()

    def _page_changed(self, index: int) -> None:
        if not self.result or not 0 <= index < min(len(self.images), len(self.result.pages)):
            return
        self.current_page = index
        self.current_index = -1
        self.canvas.set_document(self.images[index], self.result.template)
        self._populate()
        self.select_next_uncertain(page_only=True)

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
            self.writer_suggestion_button.hide()
            region_index = next((i for i, region in enumerate(self.result.template.fields)
                                 if region.id == item.region_id), -1)
            self.canvas.set_active_field(region_index)
            address = tr('Поле · {p0}', p0=item.name)
            self.address_label.setText(address)
            self.exclude_row.blockSignals(True)
            self.exclude_row.setChecked(False)
            self.exclude_row.setEnabled(False)
            self.exclude_row.blockSignals(False)
        else:
            item = page.cells[self.current_index]
            suggestion = getattr(item, "writer_suggestion", "")
            self.writer_suggestion_button.setText(
                tr('Подставить вариант почерка: {p0}', p0=suggestion) if suggestion else tr('Подставить вариант почерка'),
            )
            self.writer_suggestion_button.setVisible(bool(suggestion and suggestion != item.final_text))
            self.canvas.set_active_cell(item.row, item.column)
            address = tr('Ячейка {p0}{p1}', p0=excel_column_name(item.column), p1=item.row + 1)
            self.address_label.setText(address)
            self.exclude_row.blockSignals(True)
            self.exclude_row.setEnabled(item.row >= self.result.template.header_rows)
            self.exclude_row.setChecked(item.row in page.excluded_rows)
            self.exclude_row.blockSignals(False)
        self.confirm_button.setEnabled(item.status != "excluded")
        self.correct_value.setEnabled(item.status != "excluded")
        self.writer_suggestion_button.setEnabled(item.status != "excluded")
        if getattr(item, "status", "automatic") == "excluded":
            self.value_label.setText(tr('Пусто — исключено'))
        else:
            self.value_label.setText(item.final_text or tr('Пусто'))
        self.correct_value.setText(item.final_text)
        reasons = {
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
            "writer_style_profile_used": tr('Ответ сравнивался с устойчивыми образцами почерка из других ячеек этой страницы'),
            "writer_style_agrees": tr('Профиль почерка страницы независимо подтверждает выбранные цифры'),
            "writer_style_selected": tr('Профиль почерка выбрал другой реально прочитанный OCR-вариант — проверьте его по изображению'),
            "writer_style_ambiguous": tr('Форма цифры противоречит первичному OCR, но доказательств пока недостаточно для уверенного выбора'),
            "writer_style_conflict_rejected": tr('Похожий образец почерка не принят: независимый распознаватель цифр подтвердил исходный ответ'),
            "multistage_cascade": tr('Выполнена многоэтапная локальная проверка'),
            "table_outlier": tr('Значение резко отличается от других сопоставимых ячеек этой строки'),
            "table_outlier_with_plausible_alternative": tr('Среди других прочтений OCR есть вариант, согласующийся со строкой — выберите его только после сверки с фото'),
        }
        audit_flags = {"numeric_verification_required", "high_accuracy_consensus", "constrained_decoder_used",
                       "crop_retry_contributed", "decimal_separator_detected", "digit_verifier_agrees",
                       "multistage_cascade", "writer_style_profile_used", "writer_style_agrees",
                       "value_rule_review_required"}
        def section(title: str, lines: list[str]) -> str:
            if not lines:
                return ""
            return fmt('<p><b>{p0}</b></p><ul>', p0=escape(title)) + join_text('', (fmt('<li>{p0}</li>', p0=escape(line)) for line in lines)) + "</ul>"
        concerns = [reasons.get(flag, tr('Дополнительная отметка: {p0}', p0=flag)) for flag in item.flags if flag not in audit_flags]
        checks = [reasons[flag] for flag in item.flags if flag in audit_flags]
        details = section(tr('Что проверить'), concerns or [tr('Сравните значение с фрагментом оригинала.')])
        rule_lines = []
        if self.current_kind == "cell" and getattr(item, "applied_rule", ""):
            rule, rule_name = self.result.template.value_constraints(item.row, item.column)
            rule_lines = [fmt("{p0}: {p1}", p0=rule_name, p1=rule.summary())]
        details += section(tr('Правило значения'), rule_lines)
        readings = []
        if item.raw_text:
            readings.append(tr('Первичное распознавание: ') + item.raw_text)
        if getattr(item, "suggested_text", ""):
            readings.append(tr('До исключения строки: ') + item.suggested_text)
        if item.alternatives:
            readings.append(tr('Другие прочтения: ') + item.alternatives)
        details += section(tr('Варианты прочтения'), readings)
        details += section(tr('Выполненные проверки'), checks)
        details += section(tr('Технические сведения'), [tr('Оценка OCR: {p0:.0%}. Это оценка модели, а не измеренная точность.', p0=item.confidence)]
                           + ([tr('Почерк: ') + item.writer_evidence] if getattr(item, "writer_evidence", "") else []))
        self.confidence_label.setText(details)
        self.detail_scroll.verticalScrollBar().setValue(0)
        # Review always uses the same unmodified source page as the left canvas.
        # Ownership masks and inset OCR crops can erase genuine ink or leave border fragments.
        crop = None
        if self.current_page < len(self.images):
            image = self.images[self.current_page]
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


    def _use_writer_suggestion(self) -> None:
        if not self.result or self.current_kind != "cell" or self.current_index < 0:
            return
        item = self.result.pages[self.current_page].cells[self.current_index]
        if item.writer_suggestion:
            self.correct_value.setText(item.writer_suggestion)
            self.correct_value.setFocus()

    def confirm_current(self) -> None:
        if not self.result or self.current_index < 0:
            return
        page = self.result.pages[self.current_page]
        collection = page.fields if self.current_kind == "field" else page.cells
        item = collection[self.current_index]
        if item.status == "excluded":
            return
        corrected = self.correct_value.text().strip()
        if self.current_kind == "field" and "required_field_empty" in item.flags and not corrected:
            QMessageBox.warning(self, tr('Заполните поле'), tr('Это обязательное поле. Введите значение по оригиналу.'))
            return
        if self.current_kind == "cell" and item.status != "excluded":
            rule, name = self.result.template.value_constraints(item.row, item.column)
            if rule.value_format in {"numeric", "integer", "complex_numeric"}:
                corrected = canonical_numeric(corrected)
            if rule.hard_errors(corrected):
                QMessageBox.warning(self, tr('Значение не соответствует правилу'), tr('{p0}: {p1}\n\nИсправьте значение или измените правило и повторите распознавание.', p0=name, p1=rule.summary()))
                return
        elif self.current_kind == "field":
            region = next((region for region in self.result.template.fields if region.id == item.region_id), None)
            if region and region.kind in {"numeric", "integer", "complex_numeric"}:
                corrected = canonical_numeric(corrected)
        item.final_text = corrected
        if getattr(item, "status", "automatic") != "excluded":
            item.status = "confirmed" if corrected == item.raw_text else "corrected"
        self._populate()
        self.resultChanged.emit(self.result)
        self.select_next_uncertain()

    def confirm_all_uncertain(self) -> None:
        """Accept every currently valid disputed value in the whole document."""
        if not self.result or not self.result.unresolved_count:
            return
        confirmable: list[object] = []
        blocked = 0
        for page in self.result.pages:
            for field in page.fields:
                if not field.needs_review:
                    continue
                if "required_field_empty" in field.flags and not field.final_text.strip():
                    blocked += 1
                else:
                    confirmable.append(field)
            for cell in page.cells:
                if not cell.needs_review:
                    continue
                constraints, _ = self.result.template.value_constraints(cell.row, cell.column)
                if cell.applied_rule and constraints.hard_errors(cell.final_text):
                    blocked += 1
                else:
                    confirmable.append(cell)
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
        if not self.result or self.current_kind != "cell" or self.current_index < 0:
            return
        page = self.result.pages[self.current_page]
        row = page.cells[self.current_index].row
        if checked and row not in page.excluded_rows:
            page.excluded_rows.append(row)
        elif not checked and row in page.excluded_rows:
            page.excluded_rows.remove(row)
        for cell in page.cells:
            if cell.row == row and cell.column >= self.result.template.row_label_columns:
                if checked:
                    if cell.final_text:
                        cell.suggested_text = cell.final_text
                    cell.final_text = ""
                    cell.status = "excluded"
                elif cell.status == "excluded":
                    cell.final_text = cell.suggested_text or cell.raw_text
                    cell.status = "automatic"
        self._populate()
        self._show_current()
        self.resultChanged.emit(self.result)

    def select_next_uncertain(self, *, page_only: bool = False) -> None:
        if not self.result:
            return
        entries = [(page_index, kind, index, item)
                   for page_index, page in enumerate(self.result.pages)
                   for kind, items in (("field", page.fields), ("cell", page.cells))
                   for index, item in enumerate(items)]
        current = next((i for i, entry in enumerate(entries)
                        if entry[:3] == (self.current_page, self.current_kind, self.current_index)), -1)
        ordered = entries[current + 1:] + entries[:current + 1]
        if current < 0:
            ordered = [e for e in entries if e[0] >= self.current_page] + [e for e in entries if e[0] < self.current_page]
        target = next((e for e in ordered if e[3].needs_review and (not page_only or e[0] == self.current_page)), None)
        if target is None:
            self._clear_current()
            return
        page_index, kind, index, item = target
        if page_index != self.current_page:
            self.current_page = page_index
            self.page_select.blockSignals(True)
            self.page_select.setCurrentIndex(page_index)
            self.page_select.blockSignals(False)
            if page_index < len(self.images):
                self.canvas.set_document(self.images[page_index], self.result.template)
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
        self.writer_suggestion_button.hide()
        self.exclude_row.blockSignals(True)
        self.exclude_row.setChecked(False)
        self.exclude_row.setEnabled(False)
        self.exclude_row.blockSignals(False)
        self.confidence_label.clear()
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
        note = QLabel(tr('Дополнительно: Data для анализа и Audit с исходными прочтениями, исправлениями, оценками и вариантами почерка.'))
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
        self.preferences = QSettings(str(app_root / "preferences.ini"), QSettings.Format.IniFormat)
        set_language(str(self.preferences.value("appearance/language", "en")))
        apply_theme(QApplication.instance(), str(self.preferences.value("appearance/theme", "light")))
        self.job_id = ""
        self.source_path = ""
        self.stored_source_path = ""
        self.images: list[np.ndarray] = []
        self.template: TableTemplate | None = None
        self.result: JobResult | None = None
        self.worker: RecognitionWorker | None = None
        self._workers: list[RecognitionWorker] = []
        self.progress_dialog: QProgressDialog | None = None
        self.pending_sources: list[str] = []
        self.template_editor_working: TableTemplate | None = None
        self.template_editor_reference = ""
        self._build_ui()
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
        self.theme_select = QComboBox()
        self.theme_select.addItem(tr('Светлая тема'), "light")
        self.theme_select.addItem(tr('Тёмная тема'), "dark")
        self.theme_select.setAccessibleName(tr('Цветовая тема приложения'))
        self.theme_select.setCurrentIndex(1 if QApplication.instance().property("tablescanTheme") == "dark" else 0)
        self.theme_select.currentIndexChanged.connect(self._change_theme)
        top_layout.addWidget(self.theme_select)
        root_layout.addWidget(top)

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
        settings_layout.addStretch()

        for page in (document_workspace, templates_workspace, settings_page): self.main_stack.addWidget(page)
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
        self.files_page.set_recent(self.store.recent_jobs())

    def _refresh_templates(self) -> None:
        templates = self.store.load_templates()
        if hasattr(self, "template_library"):
            self.template_library.set_templates(templates)
        if hasattr(self, "table_page"):
            self.table_page.set_saved_templates(templates)

    def _template_editor_changed(self, template: TableTemplate) -> None:
        self.template_editor_working = template

    def create_template_from_sample(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr('Выберите образец таблицы'), "", "Documents (*.pdf *.png *.jpg *.jpeg *.tif *.tiff)")
        if not path:
            return
        try:
            images = load_document(path)
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
            source, _ = QFileDialog.getOpenFileName(self, tr('Укажите образец для шаблона'), "", "Documents (*.pdf *.png *.jpg *.jpeg *.tif *.tiff)")
            if not source:
                return
        try:
            images = rotate_document(load_document(source), template.rotation_degrees)
            if not images:
                raise ValueError(tr('Файл не содержит страниц'))
        except Exception as exc:
            QMessageBox.warning(self, tr('Образец недоступен'), str(exc)); return
        working = TableTemplate.from_dict(template.to_dict())
        self.template_editor_working = working; self.template_editor_reference = source
        self.template_editor.set_document(images[0], working)
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
        self.template_editor_reference = saved.reference_source_path
        self._refresh_templates()
        try:
            image = rotate_document(load_document(saved.reference_source_path), saved.rotation_degrees)[0]
            self.template_editor.set_document(image, saved)
        except Exception:
            pass
        notify(self, tr('Сохранена версия v{p0}. Предыдущие версии не изменены.', p0=saved.template_version))

    def _save_document_template_version(self, template: TableTemplate) -> None:
        if not self.images:
            return
        template.reference_page_aspect = self.images[0].shape[1] / max(1, self.images[0].shape[0])
        try:
            saved = self.store.save_template_version(template, self.stored_source_path or self.source_path)
        except Exception as exc:
            QMessageBox.warning(self, tr('Шаблон не сохранён'), str(exc)); return
        self.template = saved
        self.table_page.set_document(self.images[0], saved)
        self._refresh_templates()
        notify(self, tr('Создана версия v{p0}: {p1}', p0=saved.template_version, p1=saved.name))

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
            tr('Будет удалена только версия v{p0} шаблона «{p1}». Обработанные документы сохранятся.', p0=template.template_version, p1=template.name),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.store.delete_template(template_id); self._refresh_templates()

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr('Открыть изображения таблиц'), "", tr('Документы (*.pdf *.png *.jpg *.jpeg *.tif *.tiff)'))
        if paths:
            self.open_files(paths)

    def open_files(self, paths: list[str]) -> None:
        if not paths:
            return
        self.pending_sources = list(paths[1:])
        if len(paths) > 1:
            QMessageBox.information(
                self,
                tr('Очередь создана'),
                tr('The first file opens now. {p0} remaining file(s) will reuse this template after each export.', p0=len(paths) - 1),
            )
        self.open_source(paths[0])

    def open_source(self, path: str, reused_template: TableTemplate | None = None) -> None:
        selected_match: TemplateMatch | None = None
        try:
            self.job_id, copied = self.store.import_source(path)
            self.stored_source_path = str(copied)
            self.source_path = path
            self.images = load_document(copied)
            if not self.images:
                raise ValueError(tr('The document contains no pages.'))
            detection: GridDetection | None = None if reused_template else self._detect_or_manual(self.images[0])
            chosen_template = reused_template
            if reused_template is None:
                matches = rank_templates(self.images[0].shape, detection, self.store.load_templates())
                if matches:
                    chooser = TemplateChoiceDialog(matches, self)
                    if chooser.exec() != QDialog.DialogCode.Accepted:
                        self.store.delete_job(self.job_id)
                        self.job_id = ""; self.source_path = ""; self.stored_source_path = ""; self.images = []
                        self.refresh_recent()
                        return
                    if chooser.selected_template_id:
                        selected_match = next((item for item in matches if item.template.id == chooser.selected_template_id), None)
                        chosen_template = selected_match.template if selected_match else None
        except Exception as exc:
            QMessageBox.critical(self, tr('Не удалось открыть документ'), str(exc))
            return
        if chosen_template:
            self.images = rotate_document(self.images, chosen_template.rotation_degrees)
            self.template = TableTemplate.from_dict(chosen_template.to_dict())
        else:
            identifier = str(uuid4())
            self.template = TableTemplate(
                id=identifier, name=f"{Path(path).stem} — template", table_rect=detection.table_rect,
                row_guides=detection.row_guides, column_guides=detection.column_guides,
                template_version=0, family_id=identifier, reference_source_path=str(copied),
                reference_page_aspect=self.images[0].shape[1] / max(1, self.images[0].shape[0]),
            )
        self.template.ensure_column_rules()
        self.result = None
        self.file_title.setText(Path(path).name)
        self.table_page.set_document(self.images[0], self.template)
        if selected_match:
            self.table_page.grid_warning.setText(
                tr('Template match: {p0}%', p0=round(selected_match.score * 100))
            )
        else:
            self.table_page.grid_warning.setText(join_text(' ', detection.warnings) if detection else tr('Проверьте наложение сохранённого шаблона и число строк заголовка.'))
        self._navigate(1)
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
        if template.fields or template.cell_rules:
            answer = QMessageBox.question(self, tr('Rotate template'), tr('Rotation will detect a new grid and clear fields and rules. Continue?'))
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
        template.column_rules = []
        template.ensure_column_rules()
        template.reference_page_aspect = rotated.shape[1] / rotated.shape[0]
        self.template_editor.set_document(rotated, template)
        notify(self, tr('Template rotated'))

    def _rotate_pages(self, degrees: int) -> None:
        if not self.images or not self.template:
            return
        if self.template.fields or self.result:
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
        self._update_steps()
        notify(self, tr("Document rotated"))

    def open_recent(self, job_id: str) -> None:
        loaded = self.store.load_job(job_id)
        if not loaded:
            QMessageBox.warning(self, tr('Задание недоступно'), tr('Не удалось открыть сохранённое задание.'))
            return
        metadata, result = loaded
        try:
            self.images = load_document(metadata["stored_source_path"])
        except Exception as exc:
            QMessageBox.warning(self, tr('Исходник недоступен'), str(exc))
            return
        self.job_id = job_id
        self.source_path = metadata["source_path"]
        self.stored_source_path = metadata["stored_source_path"]
        self.file_title.setText(metadata["source_name"])
        if result:
            self.result = result
            self.template = TableTemplate.from_dict(result.template.to_dict())
            self.images = rotate_document(self.images, self.template.rotation_degrees)
            self.table_page.set_document(self.images[0], self.template)
            self.review_page.set_result(self.images, result)
            self._navigate(2)
        else:
            QMessageBox.information(self, tr('Импортированный файл'), tr('Для этого файла ещё нет шаблона. Импортируйте его снова, чтобы настроить таблицу.'))

    def _template_changed(self, template: TableTemplate) -> None:
        self.template = template
        if self.result and self.result.template.to_dict() != template.to_dict():
            # Keep the saved job intact; a changed template must not reinterpret
            # old confirmed results as if recognition had been rerun.
            self.result = None
        self._update_steps()

    def _load_saved_template(self, template_id: str) -> None:
        if not self.images:
            return
        template = next((item for item in self.store.load_templates() if item.id == template_id), None)
        if template is None:
            QMessageBox.warning(self, tr('Шаблон недоступен'), tr('Не удалось загрузить сохранённый шаблон.'))
            return
        current_rotation = self.template.rotation_degrees if self.template else 0
        self.images = rotate_document(self.images, template.rotation_degrees - current_rotation)
        self.template = TableTemplate.from_dict(template.to_dict())
        self.result = None
        self.table_page.set_document(self.images[0], self.template)
        self.table_page.grid_warning.clear()

    def start_recognition(self, template: TableTemplate) -> None:
        if not self.images or not self.job_id:
            return
        running = next((worker for worker in self._workers if worker.isRunning()), None)
        if running is not None:
            # A fast double click can queue the same action twice before the
            # modal progress window is painted. Never replace the only strong
            # reference to a live QThread.
            if self.progress_dialog:
                self.progress_dialog.show()
                self.progress_dialog.raise_()
                self.progress_dialog.activateWindow()
            return
        try:
            template.validate_value_rules()
        except ValueError as exc:
            QMessageBox.warning(self, tr('Проверьте правила'), str(exc)); return
        self.template = template
        crop_root = self.store.jobs_dir / self.job_id / "crops"
        self.progress_dialog = QProgressDialog(tr('Подготовка локального OCR…'), tr('Отмена'), 0, 100, self)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.worker = RecognitionWorker(
            self.images, self.source_path, TableTemplate.from_dict(template.to_dict()), crop_root,
            self.table_page.high_accuracy.isChecked(),
            self,
        )
        self._workers.append(self.worker)
        self.table_page.set_recognition_running(True)
        self.worker.progress.connect(self._recognition_progress)
        self.worker.completed.connect(self._recognition_done)
        self.worker.failed.connect(self._recognition_failed)
        self.worker.cancelled.connect(self._recognition_cancelled)
        current_worker = self.worker
        self.worker.finished.connect(lambda: self._recognition_thread_finished(current_worker))
        self.progress_dialog.canceled.connect(self.worker.requestInterruption)
        self.worker.start()

    def _recognition_thread_finished(self, worker: RecognitionWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        if self.worker is worker:
            self.worker = None
            self.table_page.set_recognition_running(False)
            if self.progress_dialog:
                self.progress_dialog.close()
                self.progress_dialog = None
        worker.deleteLater()

    def _recognition_progress(self, value: int, maximum: int, label: str) -> None:
        if not self.progress_dialog:
            return
        self.progress_dialog.setMaximum(maximum)
        self.progress_dialog.setValue(value)
        self.progress_dialog.setLabelText(label)

    def _recognition_done(self, result: JobResult) -> None:
        if self.progress_dialog:
            self.progress_dialog.close()
        self.result = result
        self.template = TableTemplate.from_dict(result.template.to_dict())
        self.table_page.set_document(self.images[0], self.template)
        self.store.save_result(self.job_id, result)
        self.review_page.set_result(self.images, result)
        self._navigate(2)
        self.refresh_recent()

    def _recognition_failed(self, message: str) -> None:
        if self.progress_dialog:
            self.progress_dialog.close()
        QMessageBox.critical(self, tr('Ошибка распознавания'), message)

    def _recognition_cancelled(self) -> None:
        if self.progress_dialog:
            self.progress_dialog.close()

    def closeEvent(self, event) -> None:
        running = [worker for worker in self._workers if worker.isRunning()]
        if running:
            for worker in running:
                worker.requestInterruption()
            event.ignore()
            QMessageBox.information(
                self,
                tr('Завершение анализа'),
                tr('Останавливаю локальный анализ. Закройте приложение ещё раз после завершения остановки.'),
            )
            return
        super().closeEvent(event)

    def _result_changed(self, result: JobResult) -> None:
        self.result = result
        if self.job_id:
            self.store.save_result(self.job_id, result)
        self.refresh_recent()
        self._update_steps()

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
        except Exception as exc:
            QMessageBox.critical(self, tr('Ошибка экспорта'), str(exc))
            return
        notify(self, tr('Проверенная таблица сохранена:\n{p0}', p0=saved))
        if self.pending_sources and self.template:
            next_source = self.pending_sources.pop(0)
            self.open_source(next_source, self.template)
            return
        self._update_steps()


def create_application() -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("TableScan Local")
    app.setOrganizationName("TableScan Local")
    app.setStyle("Fusion")
    window = MainWindow()
    return app, window
