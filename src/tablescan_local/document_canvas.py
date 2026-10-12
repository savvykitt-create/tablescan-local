from __future__ import annotations
from .i18n import localized_rule_name
from .i18n import tr, fmt
from .i18n import bind
from bisect import bisect_right
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView
from .localized_widgets import QFrame
from .domain import NormalizedRect, TableTemplate, excel_column_name
from .theme import colors
from .ui_common import BLUE, MUTED, pixmap_from_bgr


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

    def set_document(self, image: np.ndarray | None, template: TableTemplate | None = None) -> None:
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
            if self.selection_enabled:
                band = self._header_band()
                target = target.united(QRectF(self.template.column_guides[0]*image_width - band,
                                              self.template.row_guides[0]*image_height - band,
                                              width + band, height + band))
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
        if self.selection_enabled:
            band = self._header_band()
            bounds = QRectF(self.template.column_guides[0]*width-band,
                            self.template.row_guides[0]*height-band,
                            (self.template.column_guides[-1]-self.template.column_guides[0])*width+band,
                            (self.template.row_guides[-1]-self.template.row_guides[0])*height+band)
            self.scene().setSceneRect(pixmap_item.boundingRect().united(bounds))
        x, y, rect_width, rect_height = self.template.table_rect.to_pixels(self.image.shape)
        table_bounds = QRectF(x, y, rect_width, rect_height)
        excluded = self.template.exclusion_mask()
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
                text = self.scene().addSimpleText(fmt('{p0}. {p1}', p0=index + 1, p1=localized_rule_name(region.name)))
                text.setPos(rect.left() + 5, rect.top() + 3); text.setBrush(color)
                text.setScale(1.15); text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        if not self.read_only:
            for cell in self.template.fixed_cells:
                if not (0 <= cell.row < self.template.rows and 0 <= cell.column < self.template.columns):
                    continue
                if (cell.row, cell.column) in excluded:
                    continue
                left, right = self.template.column_guides[cell.column:cell.column + 2]
                top, bottom = self.template.row_guides[cell.row:cell.row + 2]
                rect = QRectF(left * width, top * height, (right-left) * width, (bottom-top) * height)
                item = self.scene().addRect(rect, QPen(QColor("#94A3B8"), 1), QColor(241, 245, 249, 230))
                item.setZValue(22); item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                text = self.scene().addSimpleText(cell.value or "∅")
                text.setBrush(QColor("#0F766E")); text.setZValue(23)
                text.setScale(min(1.2, max(.2, (rect.width()-4) / max(1, text.boundingRect().width()))))
                text.setPos(rect.left()+2, rect.top()+2); text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            for row in range(self.template.rows):
                for column in range(self.template.columns):
                    if (row, column) not in excluded:
                        continue
                    left, right = self.template.column_guides[column:column+2]
                    top, bottom = self.template.row_guides[row:row+2]
                    rect = QRectF(left*width, top*height, (right-left)*width, (bottom-top)*height)
                    pen = QPen(QColor(120, 70, 70, 160), 1)
                    item = self.scene().addRect(rect, pen, QColor(100, 116, 139, 75))
                    item.setZValue(23); item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                    for x1, y1, x2, y2 in ((rect.left(), rect.top(), rect.right(), rect.bottom()),
                                           (rect.right(), rect.top(), rect.left(), rect.bottom())):
                        line = self.scene().addLine(x1, y1, x2, y2, pen)
                        line.setZValue(23); line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

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
                    if self.template.fixed_value(row, column) is not None:
                        tooltip = tr("Value from template; OCR skipped")
                    if (row, column) in excluded:
                        tooltip = tr('Excluded before analysis')
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
