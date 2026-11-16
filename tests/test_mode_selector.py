import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt

from tablescan_local.mode_selector import ModeSelector
from tablescan_local.i18n import set_language
from tablescan_local.theme import apply_theme


@pytest.mark.parametrize("mode,expected", [
    ("fast", dict(high_accuracy=False, slow_mode=False, adaptive=False)),
    ("medium", dict(high_accuracy=True, slow_mode=False, adaptive=False)),
    ("slow", dict(high_accuracy=True, slow_mode=True, adaptive=False)),
    ("adaptive", dict(high_accuracy=True, slow_mode=True, adaptive=True)),
])
def test_modes_have_unambiguous_runtime_flags(qtbot, mode, expected):
    selector = ModeSelector(slow_available=True)
    qtbot.addWidget(selector)
    selector.setMode(mode)
    assert selector.currentData() == mode
    assert selector.flags() == expected


def test_locked_modes_cannot_be_chosen_by_click_keyboard_or_program(qtbot):
    selector = ModeSelector(slow_available=False)
    qtbot.addWidget(selector)
    selector.resize(320, 250)
    selector.show()
    assert selector.currentData() == "medium"
    assert not selector.mode_buttons[2].isEnabled()
    assert not selector.mode_buttons[3].isEnabled()
    assert selector.install_button.isVisible()
    events = []
    selector.modeChanged.connect(events.append)
    selector.setMode("slow")
    selector.setMode("adaptive")
    qtbot.keyClick(selector.slider, Qt.Key.Key_End)
    qtbot.keyClick(selector.slider, Qt.Key.Key_Right)
    qtbot.mouseClick(selector.slider, Qt.MouseButton.LeftButton,
                     pos=QPoint(selector.slider.width() - 16, selector.slider.height() // 2))
    assert selector.currentData() == "medium"
    assert events == []
    qtbot.keyClick(selector.slider, Qt.Key.Key_Home)
    assert selector.currentData() == "fast"
    assert events == ["fast"]


def test_installation_unlocks_modes_and_removal_resets_to_medium(qtbot):
    state = [False]
    selector = ModeSelector(availability=lambda: state[0])
    qtbot.addWidget(selector)
    selector.show()
    events = []
    selector.modeChanged.connect(events.append)
    state[0] = True
    selector.refresh_availability()
    assert selector.mode_buttons[3].isEnabled()
    assert not selector.install_button.isVisible()
    qtbot.mouseClick(selector.mode_buttons[3], Qt.MouseButton.LeftButton)
    assert selector.currentData() == "adaptive"
    state[0] = False
    selector.refresh_availability()
    assert selector.currentData() == "medium"
    assert selector.flags() == dict(high_accuracy=True, slow_mode=False, adaptive=False)
    assert events == ["adaptive", "medium"]


def test_slider_positions_map_to_each_available_mode(qtbot):
    selector = ModeSelector(slow_available=True)
    qtbot.addWidget(selector)
    selector.resize(320, 220)
    selector.show()
    for index, name in enumerate(("fast", "medium", "slow", "adaptive")):
        x = round(16 + (selector.slider.width() - 32) * index / 3)
        qtbot.mouseClick(selector.slider, Qt.MouseButton.LeftButton,
                         pos=QPoint(x, selector.slider.height() // 2))
        assert selector.currentData() == name


def test_install_button_opens_settings(qtbot):
    selector = ModeSelector(slow_available=False)
    qtbot.addWidget(selector)
    with qtbot.waitSignal(selector.setupRequested):
        selector.install_button.click()


def test_clicking_current_mode_preserves_selection_highlight(qtbot):
    selector = ModeSelector(slow_available=True)
    qtbot.addWidget(selector)
    selector.mode_buttons[1].click()
    assert selector.currentData() == "medium"
    assert selector.mode_buttons[1].isChecked()
    assert sum(button.isChecked() for button in selector.mode_buttons) == 1


@pytest.mark.parametrize("language", ["en", "ru", "cs"])
def test_locked_selector_fits_compact_sidebar_with_native_metrics(qtbot, qapp, language):
    # Keep the platform's native style/font so Windows CI exercises its own
    # button metrics, not a simulated macOS screenshot or a fixed test font.
    from PySide6.QtGui import QFontDatabase
    assert QFontDatabase.families(), "Qt must load installed fonts for meaningful layout measurements"
    apply_theme(qapp, "light")
    set_language(language)
    try:
        selector = ModeSelector(slow_available=False)
        qtbot.addWidget(selector)
        selector.resize(324, 150)
        selector.show()
        qtbot.wait(20)
        assert selector.size().toTuple() == (324, 150)
        assert selector.minimumSizeHint().width() <= 324
        assert selector.minimumSizeHint().height() <= 150
        for widget in [selector.install_button, *selector.mode_buttons, selector.help_button]:
            assert widget.isVisible()
            assert selector.rect().contains(widget.mapTo(selector, widget.rect().topLeft()))
            assert selector.rect().contains(widget.mapTo(selector, widget.rect().bottomRight()))
    finally:
        set_language("en")
