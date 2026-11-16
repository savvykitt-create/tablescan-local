"""The visible mode and Start Analysis action must drive the actual queue."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QPoint

from tablescan_local.domain import NormalizedRect, TableTemplate
from tablescan_local.i18n import set_language, tr
from tablescan_local.ui import RecognitionWorker, create_application


def _window(qtbot, monkeypatch, tmp_path, *, installed=False):
    from tablescan_local import slow_mode, slow_setup
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(slow_setup, "setup_status", lambda: "ready" if installed else "missing")
    monkeypatch.setattr(slow_mode, "runtime_config", lambda: {})
    _, window = create_application()
    qtbot.addWidget(window)
    source = tmp_path / "source.png"
    image = np.full((200, 200, 3), 255, np.uint8)
    cv2.imwrite(str(source), image)
    window.job_id, copied = window.store.import_source(source)
    window.source_path = str(source)
    window.stored_source_path = str(copied)
    window.images = [image]
    template = TableTemplate("mode-test", "Mode test", NormalizedRect(.1, .1, .8, .8),
                             [.1, .5, .9], [.1, .5, .9])
    template.ensure_column_rules()
    window.template = template
    window.table_page.set_document(image, template)
    window.stack.setCurrentWidget(window.table_page)
    return window


@pytest.mark.parametrize("language", ["en", "ru", "cs"])
def test_start_analysis_stays_visible_on_every_preparation_tab(qtbot, monkeypatch, tmp_path, language):
    window = _window(qtbot, monkeypatch, tmp_path)
    set_language(language)
    try:
        window.resize(960, 600)
        window.show()
        page = window.table_page
        for index in range(page.tabs.count()):
            page.tabs.setCurrentIndex(index)
            qtbot.wait(20)
            button = page.continue_button
            assert window.size().toTuple() == (960, 600)
            assert button.isVisible()
            assert button.text() == tr("Start Analysis")
            assert window.rect().contains(button.mapTo(window, QPoint(0, 0)))
            assert window.rect().contains(button.mapTo(window, button.rect().bottomRight()))
            assert page.mode_selector.isVisible()
        assert not page.high_accuracy.isVisible()
        assert not page.slow_mode.isVisible()
        assert not page.adaptive.isVisible()
    finally:
        set_language("en")


@pytest.mark.parametrize("mode,flags", [
    ("fast", (False, False, False)),
    ("medium", (True, False, False)),
    ("slow", (True, True, False)),
    ("adaptive", (True, True, True)),
])
def test_start_analysis_enqueues_the_visible_mode(qtbot, monkeypatch, tmp_path, mode, flags):
    window = _window(qtbot, monkeypatch, tmp_path, installed=True)
    workers = []
    monkeypatch.setattr(RecognitionWorker, "start", lambda self: workers.append(self))
    window.table_page.mode_selector.setMode(mode)
    window.table_page.continue_button.click()
    assert len(workers) == 1
    worker = workers[0]
    try:
        assert (worker.high_accuracy, worker.slow_mode, worker.adaptive) == flags
        entry = window.analysis_queue.entries[0]
        assert (entry["high_accuracy"], entry["slow_mode"], entry["adaptive"]) == flags
    finally:
        worker.cancelled.emit()
        worker.finished.emit()


def test_missing_models_cannot_enqueue_slow_or_adaptive(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    selector = window.table_page.mode_selector
    assert not selector.mode_buttons[2].isEnabled()
    assert not selector.mode_buttons[3].isEnabled()
    for mode in ("slow", "adaptive"):
        selector.setMode(mode)
        assert selector.currentData() == "medium"
        assert not window.table_page.slow_mode.isChecked()
        assert not window.table_page.adaptive.isChecked()


def test_install_modes_action_opens_settings(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.table_page.mode_selector.install_button.click()
    assert window.main_stack.currentIndex() == 2


def test_adaptive_batch_reaches_queue_with_all_required_flags(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path, installed=True)
    workers = []
    monkeypatch.setattr(RecognitionWorker, "start", lambda self: workers.append(self))
    window.enqueue_prepared_files([(window.source_path, window.template)],
                                  high_accuracy=False, slow_mode=False, adaptive=True)
    assert len(workers) == 1
    worker = workers[0]
    try:
        assert (worker.high_accuracy, worker.slow_mode, worker.adaptive) == (True, True, True)
        entry = window.analysis_queue.entries[0]
        assert entry["adaptive"] and entry["slow_mode"] and entry["high_accuracy"]
        window.queue_dialog.refresh()
        assert window.queue_dialog.table.item(0, 1).text() == tr("Adaptive")
    finally:
        worker.cancelled.emit()
        worker.finished.emit()
