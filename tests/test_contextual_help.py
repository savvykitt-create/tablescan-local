"""Help is intentional: explanatory sidecars, never duplicated action hovers."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication, QToolBar, QToolTip

from tablescan_local.button_help import HelpButton, button_with_help, set_action_help
from tablescan_local.i18n import set_language, tr
from tablescan_local.localized_widgets import QPushButton
from tablescan_local.preparation_help import add_preparation_help
from tablescan_local.ui import TablePage


def hover_help(widget):
    position = QPoint(4, 4)
    QApplication.sendEvent(widget, QHelpEvent(QEvent.Type.ToolTip, position,
                                             widget.mapToGlobal(position)))


def test_only_question_mark_shows_help_and_accepts_keyboard(qtbot):
    action = QPushButton('Apply rules')
    wrapper = button_with_help(action, 'Apply the displayed rule to selected cells.')
    qtbot.addWidget(wrapper)
    wrapper.show()
    QToolTip.showText(QPoint(), '')
    hover_help(action)
    assert not QToolTip.isVisible()
    assert action.toolTip() == ''
    hover_help(wrapper.help_button)
    assert QToolTip.text() == 'Apply the displayed rule to selected cells.'
    QToolTip.showText(QPoint(), '')
    wrapper.help_button.setFocus()
    qtbot.keyClick(wrapper.help_button, Qt.Key.Key_Space)
    assert QToolTip.text() == 'Apply the displayed rule to selected cells.'
    QToolTip.showText(QPoint(), '')


def test_dynamic_action_help_stays_on_question_mark(qtbot):
    action = QPushButton('Start')
    wrapper = button_with_help(action, 'Start analysis.')
    qtbot.addWidget(wrapper)
    wrapper.show()
    set_action_help(action, 'Wait for preparation to finish.')
    assert action.toolTip() == ''
    assert wrapper.help_button.toolTip() == 'Wait for preparation to finish.'
    # Existing callers that set a tooltip after construction are migrated too.
    action.setToolTip('Review the selected files first.')
    assert action.toolTip() == ''
    assert wrapper.help_button.toolTip() == 'Review the selected files first.'
    hover_help(action)
    assert not QToolTip.isVisible()


def test_dynamic_legacy_help_keeps_live_translation(qtbot):
    action = QPushButton('Example')
    wrapper = button_with_help(action, tr('Help'))
    qtbot.addWidget(wrapper)
    try:
        action.setToolTip(tr('Choose cells, rows or columns to skip during recognition.'))
        for language in ('ru', 'cs', 'en'):
            set_language(language)
            assert action.toolTip() == ''
            assert wrapper.help_button.toolTip() == str(tr('Choose cells, rows or columns to skip during recognition.'))
    finally:
        set_language('en')


def test_obvious_document_tools_have_no_question_marks(qtbot):
    page = TablePage()
    qtbot.addWidget(page)
    bars = page.findChildren(QToolBar)
    zoom = bars[0]
    assert len(zoom.actions()) == 3
    assert not zoom.findChildren(HelpButton)
    assert len(page.findChildren(QToolBar)[1].findChildren(HelpButton)) == 1
    # Reapplying help cannot grow a second set of toolbar controls.
    add_preparation_help(page)
    assert len(zoom.actions()) == 3
    assert len(bars[1].findChildren(HelpButton)) == 1
    for language in ('ru', 'cs', 'en'):
        set_language(language)
        for bar in bars:
            for item in bar.actions():
                button = bar.widgetForAction(item)
                if not isinstance(button, HelpButton):
                    assert button.toolTip() == ''


def test_preparation_actions_do_not_show_duplicate_hover_help(qtbot):
    from PySide6.QtWidgets import QAbstractButton
    page = TablePage()
    qtbot.addWidget(page)
    page.resize(960, 650)
    page.show()
    for index in range(page.tabs.count()):
        page.tabs.setCurrentIndex(index)
        QApplication.processEvents()
        for button in page.findChildren(QAbstractButton):
            if button.isVisible() and not isinstance(button, HelpButton):
                assert not button.toolTip(), button.text()
    assert len(page.mode_selector.findChildren(HelpButton)) == 1
    assert page.continue_button.toolTip() == ''
