"""One discrete, keyboard-accessible control for the four analysis modes."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QSlider, QVBoxLayout

from .i18n import bind, tr
from .button_help import HelpButton, button_with_help
from .localized_widgets import QLabel, QPushButton, QToolButton, QWidget
from .theme import colors, set_theme_style


MODES = ("fast", "medium", "slow", "adaptive")
def _labels():
    return (tr("Fast"), tr("Medium"), tr("Slow"), tr("Adaptive"))


def _descriptions():
    return (
        tr("Fast: a short local recognition pass. Review the results before export."),
        tr("Medium: compares local models and checks uncertain numbers."),
        tr("Slow: adds local model verification for uncertain measurements."),
        tr("Adaptive: uses a short pass when models agree and full verification otherwise."),
    )


def _lock_note():
    return tr("Slow and Adaptive require Slow mode. Install it in Settings to unlock them.")


class _ModeSlider(QSlider):
    """Keep all four stops visible while preventing selection of locked stops."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.available_maximum = 1
        self.setRange(0, 3)
        self.setSingleStep(1)
        self.setPageStep(1)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(40)
        self.valueChanged.connect(self._enforce_available)

    def set_available_maximum(self, maximum: int) -> None:
        self.available_maximum = maximum
        self.setValue(self.value())
        self.update()

    def setValue(self, value: int) -> None:
        super().setValue(min(max(0, value), self.available_maximum))

    def _enforce_available(self, value: int) -> None:
        if value > self.available_maximum:
            self.setValue(self.available_maximum)

    def _position_value(self, x: float) -> int:
        fraction = min(1., max(0., (x - 16) / max(1, self.width() - 32)))
        if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            fraction = 1 - fraction
        return round(fraction * 3)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.setSliderDown(True)
            self.setValue(self._position_value(event.position().x()))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._position_value(event.position().x()))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        direction = -1 if self.layoutDirection() == Qt.LayoutDirection.RightToLeft else 1
        values = {
            Qt.Key.Key_Left: self.value() - direction,
            Qt.Key.Key_Right: self.value() + direction,
            Qt.Key.Key_Down: self.value() - 1,
            Qt.Key.Key_Up: self.value() + 1,
            Qt.Key.Key_PageDown: self.value() - 1,
            Qt.Key.Key_PageUp: self.value() + 1,
            Qt.Key.Key_Home: 0,
            Qt.Key.Key_End: self.available_maximum,
        }
        if event.key() in values:
            self.setValue(values[event.key()])
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        # Scrolling a preparation panel must not silently change its mode.
        if self.hasFocus() and event.angleDelta().y():
            self.setValue(self.value() + (1 if event.angleDelta().y() > 0 else -1))
            event.accept()
        else:
            event.ignore()

    def paintEvent(self, _event) -> None:
        c = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right, y = 16., float(self.width() - 16), self.height() / 2
        positions = [left + (right - left) * index / 3 for index in range(4)]
        if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            positions.reverse()
        painter.setPen(QPen(QColor(c["border"]), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(left, y), QPointF(right, y))
        painter.setPen(QPen(QColor(c["accent"]), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(positions[0], y), QPointF(positions[self.value()], y))
        for index, x in enumerate(positions):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["muted"] if index > self.available_maximum else c["surface"]))
            painter.drawEllipse(QPointF(x, y), 3, 3)
        painter.setBrush(QColor(c["surface"]))
        painter.setPen(QPen(QColor(c["accent"]), 2))
        painter.drawEllipse(QPointF(positions[self.value()], y), 13, 13)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(c["accent"]), 1, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)


class ModeSelector(QWidget):
    modeChanged = Signal(str)
    currentIndexChanged = Signal(int)
    setupRequested = Signal()

    def __init__(self, parent=None, *, slow_available: bool | None = None,
                 availability: Callable[[], bool] | None = None):
        super().__init__(parent)
        self._index = 1
        self._slow_available = bool(slow_available)
        self._availability = availability
        if slow_available is None and availability is None:
            from .slow_setup import setup_status
            self._availability = lambda: setup_status() == "ready"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.title = QLabel(tr("Analysis mode"))
        set_theme_style(self.title, "font-weight: 700;")
        heading = QHBoxLayout()
        heading.addWidget(self.title, 1)
        heading.addWidget(HelpButton(tr("Move the slider or choose a mode below it. Slower modes add verification; all results still need review.")))
        self.install_button = QPushButton(tr("Install modes…"))
        set_theme_style(self.install_button, "min-height: 26px; padding: 0 6px;")
        self.install_button.clicked.connect(self.setupRequested)
        self.install_action = button_with_help(self.install_button, _lock_note())
        heading.addWidget(self.install_action)
        layout.addLayout(heading)
        self.slider = _ModeSlider(self)
        name = tr("Analysis mode")
        self.slider.setAccessibleName(str(name))
        bind(self.slider, "setAccessibleName", "setAccessibleName", (name,))
        layout.addWidget(self.slider)
        labels = QHBoxLayout()
        labels.setSpacing(2)
        self.mode_buttons = []
        self.mode_help = []
        for index, label in enumerate(_labels()):
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setToolTip(_descriptions()[index])
            button.clicked.connect(lambda _checked=False, i=index: self.setCurrentIndex(i))
            set_theme_style(button, "QToolButton { padding: 0; min-height: 26px; border: 0; font-size: 12px; } "
                            "QToolButton:checked { background: #EAF1FF; font-weight: 700; }")
            action = button_with_help(button, _descriptions()[index])
            labels.addWidget(action, 1)
            self.mode_help.append(action.help_button)
            self.mode_buttons.append(button)
        layout.addLayout(labels)
        self.availability_note = QLabel(tr("Install extra models to unlock Slow and Adaptive."))
        self.availability_note.setToolTip(_lock_note())
        self.availability_note.setWordWrap(True)
        set_theme_style(self.availability_note, "color: #68707C;")
        layout.addWidget(self.availability_note)
        self.slider.valueChanged.connect(self._slider_changed)
        self.set_slow_available(self._slow_available)
        self.slider.setValue(self._index)
        self._update_labels()
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh_availability)
        if self._availability is not None:
            self.refresh_availability()
            self.timer.start()

    def refresh_availability(self) -> None:
        if self._availability is not None:
            self.set_slow_available(bool(self._availability()))

    def set_slow_available(self, available: bool) -> None:
        self._slow_available = bool(available)
        self.slider.set_available_maximum(3 if available else 1)
        for index in (2, 3):
            button = self.mode_buttons[index]
            button.setEnabled(available)
            button.setText(_labels()[index] if available else tr("{mode}\nLocked", mode=_labels()[index]))
            description = _descriptions()[index] if available else _lock_note()
            button.setToolTip(description)
            button.setWhatsThis(description)
            self.mode_help[index].setToolTip(description)
            self.mode_help[index].setWhatsThis(description)
            self.mode_help[index].setAccessibleDescription(description)
        self.availability_note.setVisible(not available)
        self.install_action.setVisible(not available)

    def _slider_changed(self, index: int) -> None:
        # Native accessibility actions can bypass Python's setValue override.
        if index > self.slider.available_maximum:
            return
        if index == self._index:
            return
        self._index = index
        self._update_labels()
        self.currentIndexChanged.emit(index)
        self.modeChanged.emit(self.currentData())

    def _update_labels(self) -> None:
        self.title.setToolTip(_descriptions()[self._index])
        for index, button in enumerate(self.mode_buttons):
            button.setChecked(index == self._index)
        description = _descriptions()[self._index]
        self.slider.setAccessibleDescription(str(description))
        bind(self.slider, "setAccessibleDescription", "setAccessibleDescription", (description,))
        self.slider.setToolTip(str(description))
        bind(self.slider, "setToolTip", "setToolTip", (description,))

    def currentData(self) -> str:
        return MODES[self._index]

    def currentIndex(self) -> int:
        return self._index

    def findData(self, mode: str) -> int:
        return MODES.index(mode) if mode in MODES else -1

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < len(MODES):
            self.slider.setValue(index)
            self._update_labels()

    def setMode(self, mode: str) -> None:
        self.setCurrentIndex(self.findData(mode))

    def flags(self) -> dict[str, bool]:
        mode = self.currentData()
        return {"high_accuracy": mode != "fast", "slow_mode": mode in {"slow", "adaptive"},
                "adaptive": mode == "adaptive"}
