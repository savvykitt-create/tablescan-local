import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tablescan_local.ui import create_application


def test_main_window_has_complete_workflow(monkeypatch, tmp_path, qtbot) -> None:
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path / "app-data"))
    _app, window = create_application()
    qtbot.addWidget(window)

    assert window.windowTitle() == "TableScan Local 0.7.14"
    assert window.table_page.high_accuracy.isChecked()
    assert window.stack.count() == 4
    assert [button.text().split()[-1] for button in window.nav_buttons] == ["Files", "Alignment", "Review", "Export"]
    assert [button.text().split()[-1] for button in window.section_buttons] == ["Documents", "Templates", "Settings"]
