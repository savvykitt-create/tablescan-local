from __future__ import annotations
import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from .localized_widgets import QLabel
from .theme import set_theme_style


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
