"""Regression coverage for laptop windows and overflowing preparation panels."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QGroupBox, QScrollArea

from tablescan_local.domain import NormalizedRect, TableTemplate
from tablescan_local.i18n import set_language
from tablescan_local.ui import MainWindow, create_application


def layout_sizes(window, page):
    widgets = [window, window.centralWidget(), window.main_stack, page,
               page.document_split, page.document_split.widget(0), page.canvas,
               page.tabs, page.tabs.tabBar()]
    return [(type(widget).__name__, widget.minimumSizeHint().toTuple(),
             widget.minimumSize().toTuple(), widget.size().toTuple()) for widget in widgets]


@pytest.mark.parametrize("mode", ["document", "template"])
@pytest.mark.parametrize("language", ["en", "ru", "cs"])
def test_preparation_tabs_scroll_without_growing_window(qtbot, monkeypatch, tmp_path, mode, language):
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path / "data"))
    _, window = create_application()
    qtbot.addWidget(window)
    set_language(language)
    try:
        page = window.table_page if mode == "document" else window.template_editor
        template = TableTemplate("layout", "Layout", NormalizedRect(.1, .1, .8, .8),
                                 [.1, .5, .9], [.1, .5, .9])
        template.ensure_column_rules()
        page.set_document(np.full((200, 200, 3), 255, np.uint8), template)
        if mode == "document":
            window.stack.setCurrentWidget(page)
        else:
            window.main_stack.setCurrentIndex(1)
            window.templates_stack.setCurrentWidget(page)
        window.file_title.setText("a very long imported document name " * 20)
        window.resize(960, 600)
        window.show()
        qtbot.wait(30)

        for index in range(page.tabs.count()):
            page.tabs.setCurrentIndex(index)
            qtbot.wait(30)
            assert window.width() == 960, layout_sizes(window, page)
            assert window.height() == 600
            scroll = page.tabs.currentWidget()
            assert isinstance(scroll, QScrollArea)
            bar = scroll.verticalScrollBar()
            assert bar.maximum() > 0
            bar.setValue(0)
            position = scroll.viewport().rect().center()
            event = QWheelEvent(QPointF(position), QPointF(scroll.viewport().mapToGlobal(position)),
                                QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(scroll.viewport(), event)
            assert bar.value() > 0
            bar.setValue(bar.maximum())
            content = scroll.widget()
            assert content.mapTo(scroll.viewport(), content.rect().bottomLeft()).y() < scroll.viewport().height()

        # Resizing after visiting every tab must still work in both directions.
        window.resize(1200, 750)
        qtbot.wait(30)
        window.resize(960, 600)
        qtbot.wait(30)
        assert window.size().toTuple() == (960, 600)
        page.exclusions_button.click()
        assert page.tabs.currentWidget().widget() is page.exclusions_panel
        assert page.canvas.selection_enabled
    finally:
        set_language("en")


def test_expanding_rule_options_updates_scroll_range(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path / "data"))
    _, window = create_application()
    qtbot.addWidget(window)
    page = window.table_page
    window.stack.setCurrentWidget(page)
    page.tabs.setCurrentIndex(2)
    window.resize(960, 600)
    window.show()
    qtbot.wait(30)
    scroll = page.tabs.currentWidget()
    group = next(group for group in scroll.findChildren(QGroupBox) if group.isCheckable())
    before = scroll.verticalScrollBar().maximum()
    group.setChecked(True)
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() > before)
    assert window.size().toTuple() == (960, 600)
    scroll.ensureWidgetVisible(page.column_role)
    assert scroll.viewport().rect().contains(page.column_role.mapTo(scroll.viewport(), QPoint(0, 0)))


def test_initial_window_uses_available_screen(qtbot, monkeypatch, tmp_path):
    class LaptopScreen:
        def availableGeometry(self):
            return QRect(0, 0, 1366, 740)

    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(MainWindow, "screen", lambda self: LaptopScreen())
    _, window = create_application()
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(30)
    assert window.width() <= 1366
    assert window.height() < 740
