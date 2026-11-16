"""Slow-mode status polling belongs to the visible preparation control."""
from unittest.mock import Mock

from PySide6.QtWidgets import QStackedWidget, QWidget

from tablescan_local.mode_selector import ModeSelector


def test_hidden_selector_refreshes_on_request_without_background_polling(qtbot):
    availability = Mock(return_value=False)
    selector = ModeSelector(availability=availability)
    qtbot.addWidget(selector)
    availability.reset_mock()
    selector.timer.setInterval(10)
    qtbot.wait(35)
    availability.assert_not_called()
    assert not selector.timer.isActive()

    availability.return_value = True
    selector.refresh_availability()
    availability.assert_called_once_with()
    assert selector.mode_buttons[3].isEnabled()
    assert not selector.timer.isActive()


def test_visible_selector_tracks_installation_and_stops_when_closed(qtbot):
    availability = Mock(return_value=False)
    selector = ModeSelector(availability=availability)
    qtbot.addWidget(selector)
    selector.timer.setInterval(10)
    selector.show()
    assert selector.timer.isActive()

    availability.return_value = True
    qtbot.waitUntil(lambda: selector.mode_buttons[3].isEnabled())
    selector.setMode("adaptive")
    availability.return_value = False
    qtbot.waitUntil(lambda: selector.currentData() == "medium")
    assert not selector.mode_buttons[3].isEnabled()

    selector.close()
    availability.reset_mock()
    assert not selector.timer.isActive()
    # Even a timeout queued before close must not query the runtime afterward.
    selector.timer.timeout.emit()
    qtbot.wait(35)
    availability.assert_not_called()


def test_page_switch_suspends_polling_and_refreshes_immediately_on_return(qtbot):
    stack = QStackedWidget()
    qtbot.addWidget(stack)
    availability = Mock(return_value=False)
    selector = ModeSelector(availability=availability)
    stack.addWidget(selector)
    other_page = QWidget()
    stack.addWidget(other_page)
    stack.show()
    assert selector.timer.isActive()

    stack.setCurrentWidget(other_page)
    availability.reset_mock()
    availability.return_value = True
    assert not selector.timer.isActive()
    selector.timer.timeout.emit()
    availability.assert_not_called()

    stack.setCurrentWidget(selector)
    availability.assert_called_once_with()
    assert selector.mode_buttons[3].isEnabled()
    assert selector.timer.isActive()


def test_static_availability_does_not_start_a_timer(qtbot):
    selector = ModeSelector(slow_available=True)
    qtbot.addWidget(selector)
    selector.show()
    assert selector.mode_buttons[3].isEnabled()
    assert not selector.timer.isActive()
