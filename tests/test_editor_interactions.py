import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtTest import QTest
from tablescan_local.domain import NormalizedRect, TableTemplate
from tablescan_local.ui import TablePage, OverlayRectItem, create_application


def sample():
    t = TableTemplate("t", "Sample", NormalizedRect(.1, .1, .8, .8), [.1,.5,.9], [.1,.5,.9])
    t.ensure_column_rules()
    return t


def test_unavailable_steps_do_not_change_selection(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path))
    _, window = create_application(); qtbot.addWidget(window)
    for index in (1, 2, 3):
        assert not window.nav_buttons[index].isEnabled()
        window.nav_buttons[index].click()
        assert window.stack.currentIndex() == 0
    assert [b.isChecked() for b in window.nav_buttons] == [True, False, False, False]
    window.template = sample(); window._navigate(1)
    assert window.nav_buttons[1].isEnabled()
    assert window.nav_buttons[1].isChecked()


def test_template_rotate_buttons_change_image_and_save_orientation(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path))
    _, window = create_application(); qtbot.addWidget(window)
    t = sample(); window.template_editor_working = t
    image = np.full((200, 400, 3), 255, np.uint8)
    window.template_editor.set_document(image, t)
    from tablescan_local.ui import QToolButton
    buttons = [b for b in window.template_editor.findChildren(QToolButton) if "Rotate" in b.text()]
    buttons[0].click()
    assert window.template_editor.image.shape == (400, 200, 3)
    assert t.rotation_degrees == 90
    buttons[1].click()
    assert window.template_editor.image.shape == image.shape
    assert t.rotation_degrees == 0
    assert window._toast.isVisibleTo(window)


def test_table_resize_keeps_nonuniform_guides_and_emits_change(qtbot):
    page = TablePage(); qtbot.addWidget(page)
    t = sample(); t.column_guides = [.1,.3,.9]
    page.set_document(np.full((1000,1000,3),255,np.uint8), t)
    with qtbot.waitSignal(page.templateChanged):
        page.canvas._table_moved(QRectF(200,200,600,600))
    assert t.column_guides == pytest.approx([.2,.35,.8])
    assert t.row_guides == pytest.approx([.2,.5,.8])
    restored = TableTemplate.from_dict(t.to_dict())
    assert restored.table_rect.width == .6


def test_field_corner_drag_updates_persisted_geometry(qtbot):
    page = TablePage(); qtbot.addWidget(page); page.resize(1200,800); page.show()
    t = sample(); page.set_document(np.full((1000,1000,3),255,np.uint8),t)
    page._region_created("field", QRectF(200,200,200,200)); page.tabs.setCurrentIndex(1)
    qtbot.wait(20)
    item = next(i for i in page.canvas.scene().items() if isinstance(i, OverlayRectItem) and i.label)
    start = page.canvas.mapFromScene(item.mapToScene(item.rect().bottomRight()))
    end = page.canvas.mapFromScene(QPointF(500,450))
    QTest.mousePress(page.canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(page.canvas.viewport(), end, delay=20)
    QTest.mouseRelease(page.canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    qtbot.wait(20)
    assert t.fields[0].rect.width == pytest.approx(.3, abs=.01)
    assert t.fields[0].rect.height == pytest.approx(.25, abs=.01)
    assert t.fields[0].name == "Field 1"
    assert TableTemplate.from_dict(t.to_dict()).fields[0].rect == t.fields[0].rect


@pytest.mark.parametrize("locale", ["en", "ru", "cs"])
def test_generated_names_are_english_in_every_locale(qtbot, tmp_path, locale):
    from tablescan_local.i18n import set_language
    from tablescan_local.storage import LocalStore
    set_language(locale)
    try:
        page = TablePage(); qtbot.addWidget(page)
        assert page.template_name.text() == "New template"
        assert page.quick_rule_name.text() == "Measurements"
        t = sample(); page.set_document(np.full((1000,1000,3),255,np.uint8),t)
        page.template_name.clear(); page._save_common()
        assert t.name == "New template"
        page._region_created("field", QRectF(200,200,200,200))
        assert t.fields[0].name == "Field 1"
        assert [c.name for c in t.column_rules] == ["Column 1", "Column 2"]
        page.quick_rule_name.clear(); page._rule_all_data(); page._apply_quick_rule()
        assert t.cell_rules[0].name == "Values"
        store = LocalStore(tmp_path / locale)
        assert store.duplicate_template(t).name == "New template — copy"
        t.name = "Мой образец"
        assert store.duplicate_template(t).name == "Мой образец — copy"
        assert TableTemplate.from_dict(t.to_dict()).name == "Мой образец"
    finally:
        set_language("en")
