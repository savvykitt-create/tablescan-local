import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tablescan_local.ui import create_application


def test_main_window_has_complete_workflow(monkeypatch, tmp_path, qtbot) -> None:
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path / "app-data"))
    _app, window = create_application()
    qtbot.addWidget(window)

    from tablescan_local import __version__
    assert window.windowTitle() == f"TableScan Local {__version__}"
    assert window.table_page.high_accuracy.isChecked()
    assert window.stack.count() == 4
    assert [button.text().split()[-1] for button in window.nav_buttons] == ["Files", "Alignment", "Review", "Export"]
    assert [button.text().split()[-1] for button in window.section_buttons] == ["Documents", "Templates", "Settings"]
    labels = [window.template_library.list.item(i).text().splitlines()[0]
              for i in range(window.template_library.list.count())]
    assert labels == ["Plantar", "Staircase", "Staircase portrait (30 animals)", "Von Frey"]


def test_library_selection_applies_name_and_rules_and_restores_failed_selection(monkeypatch, tmp_path, qtbot):
    from tablescan_local.imaging import load_document
    from tablescan_local.template_fit import fit_document_template
    from tablescan_local.ui import QMessageBox

    monkeypatch.setenv('TABLESCAN_DATA_DIR', str(tmp_path / 'app-data'))
    _app, window = create_application()
    qtbot.addWidget(window)
    templates = {t.name: t for t in window.store.load_templates()}
    window.images = load_document(templates['Plantar'].reference_source_path)
    window.template = fit_document_template(templates['Plantar'], window.images)
    page = window.table_page
    page.set_saved_templates(list(templates.values()))
    page.set_document(window.images[0], window.template)
    for name, maximum in [('Von Frey', 200), ('Plantar', 35)]:
        index = page.saved_template_select.findData(templates[name].id)
        page.saved_template_select.setCurrentIndex(index)
        page.saved_template_select.activated.emit(index)
        assert page.template_name.text() == window.template.name == name
        assert window.template.cell_constraints(1, 1)[0].maximum == maximum
        assert window.template.fixed_value(0, 1) == 'L1'
    current = window.template.to_dict()
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    index = page.saved_template_select.findData(templates['Staircase'].id)
    page.saved_template_select.setCurrentIndex(index)
    page.saved_template_select.activated.emit(index)
    assert warnings
    assert window.template.to_dict() == current
    assert page.saved_template_select.currentData() == templates['Plantar'].id
    assert page.template_name.text() == 'Plantar'
