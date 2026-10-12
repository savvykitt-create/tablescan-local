"""HEIC regressions use a generated fixture, never a user's photograph.

The fixture has a green 96x64 first frame and a primary second frame with
red/blue halves and orientation 6. Decoding must give a 64x96 portrait with
red at the top, then the green frame. See assets/heic-smoke.txt for provenance.
"""
from pathlib import Path
import shutil

import numpy as np
from PIL import UnidentifiedImageError
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
import pytest

from tablescan_local.documents import open_document
from tablescan_local.imaging import load_document, rotate_document

FIXTURE = Path(__file__).parents[1] / 'src/tablescan_local/assets/heic-smoke.heic'


@pytest.mark.parametrize('extension', ['heic', 'HEIC', 'heif', 'HEIF'])
def test_heic_decodes_primary_first_and_applies_orientation_once(tmp_path, extension):
    source = tmp_path / ('фото с пробелами.' + extension)
    shutil.copyfile(FIXTURE, source)
    original = source.read_bytes()
    pages = open_document(source)
    assert len(pages) == 2
    assert pages[0].shape == (96, 64, 3)
    assert pages[1].shape == (64, 96, 3)
    assert pages[0].dtype == np.uint8
    np.testing.assert_allclose(pages[0][10, 10], [30, 20, 230], atol=4)
    np.testing.assert_allclose(pages[0][-10, -10], [230, 30, 20], atol=4)
    np.testing.assert_allclose(pages[1][10, 10], [50, 180, 30], atol=4)
    assert np.array_equal(rotate_document(pages, 90)[0], np.rot90(pages[0]))
    assert np.array_equal(load_document(source)[0], pages[0])
    assert source.read_bytes() == original


def test_heic_preview_batch_and_source_copy(tmp_path):
    from tablescan_local.batch_preflight import prepare_file
    from tablescan_local.storage import LocalStore
    source = tmp_path / 'photo.HEIC'
    shutil.copyfile(FIXTURE, source)
    store = LocalStore(tmp_path / 'data')
    try:
        _, copied = store.import_source(str(source))
        assert copied.read_bytes() == source.read_bytes()
        item = prepare_file(copied, [])
        assert not item.error and item.pages == 2
        assert len(item.geometry[0]) == 2
        assert open_document(copied, max_dimension=32)[0].shape == (32, 21, 3)
    finally:
        store.connection.close()
    source.rename(tmp_path / 'renamed.HEIC')  # Native handles must be closed.


def test_corrupt_heic_does_not_leave_imported_job(tmp_path, monkeypatch, qtbot):
    from tablescan_local.ui import create_application, QMessageBox
    monkeypatch.setenv('TABLESCAN_DATA_DIR', str(tmp_path / 'data'))
    _, window = create_application()
    qtbot.addWidget(window)
    source = tmp_path / 'broken.HEIC'
    source.write_bytes(FIXTURE.read_bytes()[:32])
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args))
    with pytest.raises((OSError, ValueError, UnidentifiedImageError)):
        open_document(source)
    before = window.store.job_count()
    window.open_source(str(source))
    assert len(errors) == 1
    assert window.store.job_count() == before
    assert not window.job_id


def test_picker_drop_and_reopen_heic_in_alignment(tmp_path, monkeypatch, qtbot):
    from tablescan_local import ui
    from tablescan_local.i18n import set_language
    monkeypatch.setenv('TABLESCAN_DATA_DIR', str(tmp_path / 'data'))
    _, window = ui.create_application()
    qtbot.addWidget(window)
    monkeypatch.setattr(ui, 'rank_templates', lambda *args: [])
    source = tmp_path / 'photo.HEIC'
    shutil.copyfile(FIXTURE, source)
    filters = []
    def choose(*args):
        filters.append(str(args[3]))
        return [str(source)], ''
    monkeypatch.setattr(ui.QFileDialog, 'getOpenFileNames', choose)
    for language in ('en', 'cs', 'ru'):
        set_language(language)
        window.choose_files()
        assert '*.heic' in filters[-1] and '*.HEIC' in filters[-1]
        assert '*.heif' in filters[-1] and '*.HEIF' in filters[-1]
        assert window.images[0].shape == (96, 64, 3)
        assert window.stack.currentIndex() == 1
    job_id = window.job_id
    window.open_recent(job_id)
    assert window.job_id == job_id and window.images[0].shape == (96, 64, 3)
    drop_area = window.files_page.findChild(ui.DropArea)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    drop_area.dropEvent(event)
    assert event.isAccepted() and window.job_id != job_id
    assert window.images[0].shape == (96, 64, 3)
    set_language('en')


def test_packaged_heic_self_test():
    from tablescan_local.main import document_self_test
    assert document_self_test() == 0
