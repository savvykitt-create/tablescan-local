"""Visible, keyboard-accessible help beside actions, including disabled ones."""
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolTip

from .i18n import tr
from .localized_widgets import QToolButton, QWidget


class HelpButton(QToolButton):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setText('?')
        self.setProperty('actionHelp', True)
        self.setProperty('hasActionHelp', True)
        self.setAccessibleName(tr('Help'))
        self.setAccessibleDescription(text)
        self.setToolTip(text)
        self.setWhatsThis(text)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setFixedSize(26, 26)
        self.setStyleSheet('QToolButton { min-height: 22px; min-width: 22px; padding: 0; border-radius: 12px; }')
        self.clicked.connect(self.show_help)

    def show_help(self):
        QToolTip.showText(self.mapToGlobal(QPoint(0, self.height())), self.toolTip(), self)


class _ActionHelpWrapper(QWidget):
    def eventFilter(self, watched, event):
        # Only explicit visibility changes propagate. Hiding a containing page
        # must not make its actions stay hidden when that page is reopened.
        if event.type() == QEvent.Type.HideToParent:
            self.hide()
        elif event.type() == QEvent.Type.ShowToParent:
            self.show()
        return super().eventFilter(watched, event)


def button_with_help(button, text):
    """Wrap an existing action without disabling help when the action is disabled."""
    button.setToolTip(text)
    button.setWhatsThis(text)
    button.setProperty('hasActionHelp', True)
    explicitly_hidden = button.parentWidget() is not None and button.isHidden()
    wrapper = _ActionHelpWrapper()
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(button, 1)
    help_button = HelpButton(text, wrapper)
    layout.addWidget(help_button, 0, Qt.AlignmentFlag.AlignVCenter)
    wrapper.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    wrapper.help_button = help_button
    wrapper.action_button = button
    button.installEventFilter(wrapper)
    if explicitly_hidden:
        wrapper.hide()
    return wrapper
