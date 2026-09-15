import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import cv2
import numpy as np
from tablescan_local.domain import TableTemplate, NormalizedRect, CellRuleRegion
from tablescan_local.constraints import ValueConstraints
from tablescan_local.ui import TablePage, create_application, RecognitionWorker, QMessageBox


def template():
    t = TableTemplate("old", "Old", NormalizedRect(.1,.1,.8,.8), [.1,.5,.9], [.1,.5,.9])
    t.ensure_column_rules()
    t.cell_rules = [CellRuleRegion("measure", "Measurements", 0,1,1,1,
        constraints=ValueConstraints(value_format="numeric", minimum=0, maximum=60,
        decimal_places=1, require_decimal=True, expected_minimum=10, expected_maximum=55))]
    return t


def test_visible_edits_apply_without_apply_button_and_preserve_advanced(qtbot):
    page=TablePage();qtbot.addWidget(page);t=template()
    page.set_document(np.full((200,200,3),255,np.uint8),t);page.tabs.setCurrentIndex(2)
    page.quick_maximum.setText("75")
    page.quick_allow_empty.setChecked(False)
    assert t.cell_rules[0].constraints.maximum==75
    assert not t.cell_rules[0].constraints.allow_empty
    assert t.cell_rules[0].constraints.expected_minimum==10
    assert t.cell_rules[0].constraints.expected_maximum==55
    emitted=[];page.continueRequested.connect(emitted.append);page._continue()
    assert emitted[0].cell_rules[0].constraints.maximum==75


def test_invalid_visible_rule_blocks_start_and_cannot_be_overwritten_by_selection(qtbot,monkeypatch):
    page=TablePage();qtbot.addWidget(page);t=template()
    page.set_document(np.full((200,200,3),255,np.uint8),t);page.tabs.setCurrentIndex(2)
    page.quick_minimum.setText("90")
    errors=[];monkeypatch.setattr(QMessageBox,'warning',lambda *args: errors.append(args))
    emitted=[];page.continueRequested.connect(emitted.append);page._continue()
    assert errors and not emitted
    assert t.cell_rules[0].constraints.minimum==0
    page._rule_selected(0)
    assert page.quick_minimum.text()=="90"
    page.quick_maximum.setText("100")
    page._continue()
    assert emitted and t.cell_rules[0].constraints.minimum==90


def window_with_source(qtbot,monkeypatch,tmp_path):
    monkeypatch.setenv('TABLESCAN_DATA_DIR',str(tmp_path/'data'))
    _,w=create_application();qtbot.addWidget(w)
    path=tmp_path/'source.png';image=np.full((200,200,3),255,np.uint8);cv2.imwrite(str(path),image)
    w.job_id,copied=w.store.import_source(path);w.source_path=str(path);w.stored_source_path=str(copied);w.images=[image]
    w.template=template();w.store.save_template(w.template,path);w._refresh_templates();w.table_page.set_document(image,w.template)
    return w


def test_start_uses_current_form_not_stale_argument_and_snapshots_worker(qtbot,monkeypatch,tmp_path):
    w=window_with_source(qtbot,monkeypatch,tmp_path);old=TableTemplate.from_dict(w.template.to_dict())
    w.table_page.quick_maximum.setText('75');captured=[]
    monkeypatch.setattr(RecognitionWorker,'start',lambda self:captured.append(self))
    w.start_recognition(old)
    assert captured[0].template.cell_rules[0].constraints.maximum==75
    assert w.store.load_draft(w.job_id).cell_rules[0].constraints.maximum==75
    w.table_page.quick_maximum.setText('80')
    assert captured[0].template.cell_rules[0].constraints.maximum==75
    assert next(t for t in w.store.load_templates() if t.id == old.id).cell_rules[0].constraints.maximum==60
    captured[0].cancelled.emit();captured[0].finished.emit()


def test_saved_new_version_is_active_after_refresh(qtbot,monkeypatch,tmp_path):
    w=window_with_source(qtbot,monkeypatch,tmp_path);old_id=w.template.id
    w.table_page.quick_maximum.setText('75');w.table_page._save_current_template()
    assert w.template.id!=old_id
    assert w.table_page.saved_template_select.currentData()==w.template.id
    assert w.template_library.list.currentItem().data(__import__('PySide6.QtCore',fromlist=['Qt']).Qt.ItemDataRole.UserRole)==w.template.id
    w._refresh_templates()
    assert w.table_page.saved_template_select.currentData()==w.template.id
    assert w.template.cell_rules[0].constraints.maximum==75


def test_reopen_incomplete_job_restores_exact_draft(qtbot,monkeypatch,tmp_path):
    w=window_with_source(qtbot,monkeypatch,tmp_path)
    t=template();t.cell_rules[0].constraints.maximum=75;w.store.save_draft(w.job_id,t)
    w.open_recent(w.job_id)
    assert w.table_page.template.cell_rules[0].constraints.maximum==75
    assert w.result is None and w.stack.currentIndex()==1


def test_column_role_is_current_when_switching_columns(qtbot):
    page = TablePage(); qtbot.addWidget(page); t = template()
    page.set_document(np.full((200, 200, 3), 255, np.uint8), t)
    page.column_select.setCurrentIndex(1)
    ignored = page.column_role.findData("ignored")
    page.column_role.setCurrentIndex(ignored)
    page.column_role.activated.emit(ignored)
    page.column_select.setCurrentIndex(0)
    assert t.column_rules[1].role == "ignored"
    page.column_select.setCurrentIndex(1)
    assert page.column_role.currentData() == "ignored"
