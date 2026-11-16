"""Visible, keyboard-accessible help beside actions, including disabled ones."""
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolTip, QWidget as NativeWidget

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
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(26, 26)
        self.setStyleSheet('QToolButton { min-height: 22px; min-width: 22px; padding: 0; border-radius: 12px; }')
        self.clicked.connect(self.show_help)

    def show_help(self):
        QToolTip.showText(self.mapToGlobal(QPoint(0, self.height())), self.toolTip(), self)


class _NoActionTooltip(QObject):
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ToolTip:
            return True
        if event.type() == QEvent.Type.ToolTipChange and watched.toolTip():
            NativeWidget.setToolTip(watched, '')
        return super().eventFilter(watched, event)


def suppress_action_tooltip(button):
    """Suppress Qt's automatic QAction-label tooltip on obvious toolbar tools."""
    if not hasattr(button, '_no_tooltip_filter'):
        button._no_tooltip_filter = _NoActionTooltip(button)
        button.installEventFilter(button._no_tooltip_filter)
    NativeWidget.setToolTip(button, '')


class _ActionHelpWrapper(QWidget):
    def eventFilter(self, watched, event):
        # Only explicit visibility changes propagate. Hiding a containing page
        # must not make its actions stay hidden when that page is reopened.
        if event.type() == QEvent.Type.ToolTip:
            # Help belongs to the adjacent question mark, never the action.
            return True
        if event.type() == QEvent.Type.ToolTipChange and watched.toolTip():
            # Legacy dynamic setters may update an action's explanation. Move
            # it to its help control, retaining the action's translation binding.
            text = watched.toolTip()
            self.help_button.setToolTip(text)
            self.help_button.setWhatsThis(text)
            self.help_button.setAccessibleDescription(text)
            watched.setAccessibleDescription(text)
            NativeWidget.setToolTip(watched, '')
        if event.type() == QEvent.Type.HideToParent:
            self.hide()
        elif event.type() == QEvent.Type.ShowToParent:
            self.show()
        return super().eventFilter(watched, event)


def set_action_help(button, text):
    """Keep contextual help on its help control and in screen-reader metadata."""
    button.setToolTip('')
    button.setWhatsThis('')
    button.setAccessibleDescription(text)
    button._action_help_text = text
    help_button = getattr(button, '_action_help_button', None)
    if help_button is not None:
        help_button.setToolTip(text)
        help_button.setWhatsThis(text)
        help_button.setAccessibleDescription(text)


def button_with_help(button, text):
    """Wrap a contextual action; only its question mark shows hover help."""
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
    button._action_help_button = help_button
    set_action_help(button, text)
    button.installEventFilter(wrapper)
    if explicitly_hidden:
        wrapper.hide()
    return wrapper
