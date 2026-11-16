"""Batch mode availability and compatibility with the queue handoff."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from tablescan_local import slow_setup
from tablescan_local.batch_dialog import BatchPreparationDialog


def _dialog(qtbot, monkeypatch, installed):
    monkeypatch.setattr(slow_setup, "setup_status", lambda: "ready" if installed else "missing")
    monkeypatch.setattr(BatchPreparationDialog, "_start_preflight", lambda self: None)
    dialog = BatchPreparationDialog([], [], lambda: None)
    qtbot.addWidget(dialog)
    dialog.preflight_finished()
    return dialog


@pytest.mark.parametrize("mode,options,adaptive", [
    ("fast", (False, False), False),
    ("medium", (True, False), False),
    ("slow", (True, True), False),
    ("adaptive", (True, True), True),
])
def test_batch_slider_preserves_legacy_options_and_exposes_adaptive(qtbot, monkeypatch, mode, options, adaptive):
    dialog = _dialog(qtbot, monkeypatch, installed=True)
    dialog.mode.setMode(mode)
    assert dialog.analysis_options == options
    assert dialog.adaptive is adaptive


def test_batch_cannot_choose_uninstalled_modes(qtbot, monkeypatch):
    dialog = _dialog(qtbot, monkeypatch, installed=False)
    for mode in ("slow", "adaptive"):
        dialog.mode.setMode(mode)
        assert dialog.mode.currentData() == "medium"
        assert dialog.analysis_options == (True, False)
        assert not dialog.adaptive


def test_batch_install_shortcut_closes_modal_before_requesting_settings(qtbot, monkeypatch):
    dialog = _dialog(qtbot, monkeypatch, installed=False)
    dialog.setModal(True)
    dialog.show()
    observed = []
    dialog.setupRequested.connect(lambda: observed.append(dialog.isVisible()))
    dialog.mode.install_button.click()
    assert observed == [False]
    assert dialog.result() == BatchPreparationDialog.DialogCode.Rejected
