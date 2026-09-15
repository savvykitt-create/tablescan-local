import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from tablescan_local.batch_preflight import prepare_file, assess_geometry
from tablescan_local.batch_dialog import BatchPreparationDialog, FileProtocolDialog
from tablescan_local.domain import TableTemplate, JobResult, PageResult
from tablescan_local.imaging import GridDetection, load_document, rotate_document, detect_grid, write_image
from tablescan_local.ui import TablePage, create_application

ROOT = Path(__file__).resolve().parents[1]


def builtin(name):
    return TableTemplate.from_dict(json.loads((ROOT / f'src/tablescan_local/default_templates/{name}.json').read_text(encoding='utf-8')))


def fixture(rats):
    return str(ROOT / f'examples/lab_forms_excel/fixtures/plantar_{rats}.pdf')


def test_preflight_fits_variable_rows_and_rejects_wrong_protocol():
    templates = [builtin('plantar'), builtin('staircase'), builtin('von_frey')]
    before = [t.to_dict() for t in templates]
    for rats in (5, 40):
        prepared = prepare_file(fixture(rats), templates)
        assert not prepared.error
        match = prepared.assessments[templates[0].id]
        assert match.score > 85 and match.template.rows == rats + 1
        assert prepared.assessments[templates[1].id].template is None
    assert [t.to_dict() for t in templates] == before


def test_preflight_checks_every_page_and_orientation():
    template = builtin('plantar')
    first = GridDetection(template.table_rect, template.row_guides, template.column_guides)
    shorter = GridDetection(template.table_rect, template.row_guides[:-1], template.column_guides)
    result = assess_geometry(template, [((1000, 1500, 3), first), ((1000, 1500, 3), shorter)])
    assert result.template is None and result.error
    rotated = TableTemplate.from_dict(template.to_dict())
    rotated.id = 'rotated'
    rotated.rotation_degrees = 180
    result = prepare_file(fixture(5), [template, rotated])
    assert set(result.geometry) == {0, 90, 180, 270}
    assert result.assessments[template.id].score == result.assessments[rotated.id].score
    assert result.assessments[rotated.id].template.rotation_degrees == 0


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_legacy_protocol_is_fitted_and_oriented_per_file(tmp_path, rotation):
    template = builtin('plantar')
    template.auto_fit_rows = False
    template.rotation_degrees = 270  # This belonged only to its reference scan.
    original = template.to_dict()
    image = load_document(fixture(20))[0]
    # Move the printed form without changing its dimensions or cropping it.
    image = cv2.warpAffine(image, np.float32([[1, 0, 10], [0, 1, 20]]),
                          (image.shape[1], image.shape[0]), borderValue=(255, 255, 255))
    path = tmp_path / 'moved.png'
    write_image(path, rotate_document([image], rotation)[0])
    result = prepare_file(path, [template]).assessments[template.id]
    assert result.template is not None, result.error
    assert result.rotation_degrees == (360 - rotation) % 360
    assert result.template.rotation_degrees == result.rotation_degrees
    assert not result.template.auto_fit_rows  # Freeze the preview for OCR.
    assert result.template.row_guides == detect_grid(image).row_guides
    assert result.template.column_guides == detect_grid(image).column_guides
    assert template.to_dict() == original


def test_incompatible_legacy_protocol_cannot_silently_keep_old_coordinates():
    template = builtin('staircase')
    template.auto_fit_rows = False
    result = prepare_file(fixture(20), [template]).assessments[template.id]
    assert result.template is None
    assert result.preview_template is not None
    assert 'columns' in result.error


def test_default_batch_selects_best_protocol_and_explicit_choice_overrides(qtbot):
    templates = [builtin('staircase'), builtin('plantar')]
    dialog = BatchPreparationDialog([fixture(5)], templates,
                                    lambda: TablePage(mode='template', private_copy=True))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=20000)
    assert dialog.rows[0][1].currentData() == templates[1].id
    assert dialog.start_button.isEnabled()
    dialog.common_template.setCurrentIndex(dialog.common_template.findData(templates[0].id))
    dialog.apply_all()
    assert not dialog.start_button.isEnabled()
    dialog.common_template.setCurrentIndex(0); dialog.apply_all()
    assert dialog.start_button.isEnabled() and dialog.rows[0][1].currentData() == templates[1].id


def test_symmetric_orientation_requires_manual_check(tmp_path):
    from tablescan_local.domain import NormalizedRect
    image = np.full((600, 900, 3), 255, np.uint8)
    for x in range(150, 751, 150):
        cv2.line(image, (x, 150), (x, 450), (0, 0, 0), 2)
    for y in range(150, 451, 30):
        cv2.line(image, (150, y), (750, y), (0, 0, 0), 2)
    grid = detect_grid(image)
    template = TableTemplate('symmetric', 'Symmetric', grid.table_rect, grid.row_guides,
                             grid.column_guides, reference_page_aspect=1.5)
    path = tmp_path / 'symmetric.png'; write_image(path, image)
    result = prepare_file(path, [template]).assessments[template.id]
    assert result.template is None and 'Orientation is uncertain' in result.error


def test_legacy_multiple_pages_with_different_grids_are_blocked():
    template = builtin('plantar'); template.auto_fit_rows = False
    first = GridDetection(template.table_rect, template.row_guides, template.column_guides)
    shifted = GridDetection(template.table_rect, [y + .02 for y in template.row_guides], template.column_guides)
    result = assess_geometry(template, [((1000, 1500, 3), first), ((1000, 1500, 3), shifted)])
    assert result.template is None and 'different grids' in result.error


@pytest.mark.parametrize('compatible', [True, False])
def test_retry_of_old_queue_runs_preflight_before_ocr(qtbot, tmp_path, monkeypatch, compatible):
    from tablescan_local import ui
    t = builtin('plantar' if compatible else 'staircase')
    t.auto_fit_rows = False; t.rotation_degrees = 270
    calls, failures, completed = [], [], []
    def process(images, source, template, *args, **kwargs):
        calls.append((images[0].shape, template))
        return JobResult(source, template, [PageResult(0, source, [], [])])
    monkeypatch.setattr(ui, 'process_document', process)
    worker = ui.RecognitionWorker(None, fixture(20), t, tmp_path, auto_prepare=True)
    worker.failed.connect(failures.append); worker.completed.connect(completed.append)
    worker.run()
    if compatible:
        assert len(calls) == len(completed) == 1 and not failures
        shape, template = calls[0]
        assert shape[1] > shape[0] and template.rotation_degrees == 0
        assert worker.preparation['status'] == 'fitted'
    else:
        assert not calls and not completed and 'Alignment required' in failures[0]


@pytest.mark.parametrize('mode, options', [(0, (False, False)), (1, (True, False)), (2, (True, True))])
def test_batch_preview_selects_per_file_protocol_and_excludes_bad_input(qtbot, tmp_path, monkeypatch, mode, options):
    from tablescan_local import slow_mode
    monkeypatch.setattr(slow_mode, 'runtime_config', lambda: {})
    bad = tmp_path / 'bad.pdf'; bad.write_bytes(b'broken')
    templates = [builtin('plantar'), builtin('staircase')]
    dialog = BatchPreparationDialog([fixture(5), fixture(10), str(bad)], templates,
                                    lambda: TablePage(mode='template', private_copy=True))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=15000)
    assert dialog.start_button.isEnabled() and not dialog.rows[2][0].isChecked()
    for _, select in dialog.rows[:2]:
        assert '%' in select.itemText(0) and '%' in select.itemText(1)
    dialog.rows[1][1].setCurrentIndex(dialog.rows[1][1].findData(templates[1].id))
    assert not dialog.start_button.isEnabled()  # Auto-fit of the wrong protocol needs inspection.
    dialog.rows[1][0].setChecked(False)
    dialog.mode.setCurrentIndex(mode)
    dialog.submit()
    assert dialog.analysis_options == options
    assert len(dialog.jobs) == 1 and dialog.jobs[0][1].rows == 6


def test_file_editor_preserves_private_rules_and_freezes_manual_alignment(qtbot):
    template = builtin('plantar')
    before = template.to_dict()
    prepared = prepare_file(fixture(5), [template])
    dialog = FileProtocolDialog(prepared, prepared.assessments[template.id].template,
                                lambda: TablePage(mode='template', private_copy=True))
    qtbot.addWidget(dialog)
    dialog.editor.header_rows_spin.setValue(0)
    dialog.save()
    assert dialog.result_template.header_rows == 0
    assert not dialog.result_template.auto_fit_rows
    assert template.to_dict() == before


def test_many_files_keep_individual_templates_and_slow_options(qtbot, tmp_path, monkeypatch):
    from tablescan_local import ui, slow_mode
    monkeypatch.setenv('TABLESCAN_DATA_DIR', str(tmp_path / 'store'))
    monkeypatch.setattr(slow_mode, 'runtime_config', lambda: {})
    _, window = create_application(); qtbot.addWidget(window)
    calls = []
    def process(images, path, template, progress, crops, high_accuracy, *, slow_mode=False):
        calls.append((template.name, high_accuracy, slow_mode))
        page = PageResult(0, path, [], [])
        if len(calls) == 1:
            page.slow_mode = {'status': 'partial'}
        return JobResult(path, template, [page])
    monkeypatch.setattr(ui, 'process_document', process)
    jobs = []
    for index in range(25):
        path = tmp_path / f'{index}.png'
        cv2.imwrite(str(path), np.full((100, 100, 3), 255, np.uint8))
        template = builtin('plantar' if index % 2 == 0 else 'staircase')
        template.auto_fit_rows = False
        jobs.append((str(path), template))
    window.enqueue_prepared_files(jobs, high_accuracy=False, slow_mode=True)
    qtbot.waitUntil(lambda: len(calls) == 25 and window.analysis_queue.worker is None, timeout=15000)
    assert calls == [(template.name, True, True) for _, template in jobs]
    assert all(entry['status'] == 'ready' for entry in window.analysis_queue.entries)
    assert window.analysis_queue.entries[0]['message']  # Incomplete model checks remain visible.
    assert not window.analysis_queue.entries[1]['message']


def test_private_protocol_can_be_reselected_and_bulk_reset(qtbot, monkeypatch):
    templates = [builtin('plantar'), builtin('staircase')]
    dialog = BatchPreparationDialog([fixture(5), fixture(10)], templates,
                                    lambda: TablePage(mode='template', private_copy=True))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=15000)
    def edit_and_save(editor_dialog):
        editor_dialog.editor.header_rows_spin.setValue(0)
        editor_dialog.save()
        return editor_dialog.DialogCode.Accepted
    monkeypatch.setattr(FileProtocolDialog, 'exec', edit_and_save)
    dialog.edit_file(0)
    select = dialog.rows[0][1]
    assert dialog.assessment(0).template.header_rows == 0
    assert dialog.assessment(1).template.header_rows == 1
    select.setCurrentIndex(select.findData(templates[1].id))
    assert dialog.assessment(0).template is None
    select.setCurrentIndex(select.findData('__custom__'))
    assert dialog.assessment(0).template.header_rows == 0
    dialog.apply_all()
    assert select.findData('__custom__') == -1 and dialog.assessment(0).template.header_rows == 1
    assert templates[0].header_rows == 1


def test_missing_slow_runtime_does_not_queue_or_downgrade(qtbot, tmp_path, monkeypatch):
    from tablescan_local import slow_mode, ui
    monkeypatch.setenv('TABLESCAN_DATA_DIR', str(tmp_path / 'store'))
    def unavailable():
        raise RuntimeError('missing models')
    monkeypatch.setattr(slow_mode, 'runtime_config', unavailable)
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, 'warning', lambda *args: warnings.append(args[-1]))
    _, window = create_application(); qtbot.addWidget(window)
    window.enqueue_files([fixture(5), fixture(10)], builtin('plantar'), slow_mode=True)
    assert not window.analysis_queue.entries and warnings == ['missing models']


def test_batch_can_start_from_scratch_and_save_template_for_other_files(qtbot, tmp_path, monkeypatch):
    from tablescan_local.storage import LocalStore
    store = LocalStore(tmp_path / 'store')
    dialog = BatchPreparationDialog([fixture(5), fixture(5)], [],
                                    lambda: TablePage(mode='template', private_copy=True),
                                    save_template=store.save_template_version)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=15000)
    assert dialog.rows[0][1].currentData() == '__blank__'
    assert not dialog.start_button.isEnabled()
    def edit_and_save(editor_dialog):
        editor_dialog.editor.header_rows_spin.setValue(0)
        editor_dialog.save_to_library()
        assert editor_dialog.result_template is None  # Saving does not close the editor.
        editor_dialog.save()
        return editor_dialog.DialogCode.Accepted
    monkeypatch.setattr(FileProtocolDialog, 'exec', edit_and_save)
    dialog.edit_file(0)
    saved = store.load_templates()
    assert len(saved) == 1 and saved[0].template_version == 1
    assert saved[0].header_rows == 0
    assert dialog.assessment(0).template.header_rows == 0
    assert dialog.rows[1][1].findData(saved[0].id) >= 0
    assert dialog.rows[1][1].currentData() == '__blank__'
    dialog.common_template.setCurrentIndex(0)
    dialog.apply_all()
    assert all(select.currentData() == saved[0].id for _, select in dialog.rows)
    assert dialog.start_button.isEnabled()
    dialog.submit()
    assert len(dialog.jobs) == 2


def test_saving_individual_protocol_creates_new_family(qtbot, tmp_path):
    from tablescan_local.storage import LocalStore
    store = LocalStore(tmp_path / 'store')
    original = builtin('plantar')
    store.save_template(original)
    prepared = prepare_file(fixture(5), [original])
    dialog = FileProtocolDialog(prepared, prepared.assessments[original.id].template,
                                lambda: TablePage(mode='template', private_copy=True),
                                save_template=store.save_template_version)
    qtbot.addWidget(dialog)
    dialog.editor.header_rows_spin.setValue(0)
    dialog.save_to_library()
    saved = store.load_templates()
    assert len(saved) == 2
    new = next(t for t in saved if t.id != original.id)
    assert new.family_id != original.family_id and new.header_rows == 0
    assert next(t for t in saved if t.id == original.id).header_rows == 1
    assert Path(new.reference_source_path).is_file()
