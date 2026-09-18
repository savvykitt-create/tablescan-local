import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path

import pytest
from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QMessageBox
from tablescan_local import cleanup
from tablescan_local.slow_settings import SlowSettings


@pytest.fixture
def roots(tmp_path, monkeypatch):
    slow = tmp_path / 'slow-mode'
    data = tmp_path / 'data'
    slow.mkdir()
    data.mkdir()
    monkeypatch.setenv('TABLESCAN_SLOW_ROOT', str(slow))
    monkeypatch.setenv('TABLESCAN_DATA_DIR', str(data))
    return slow, data


def populate(root, names):
    for name in names:
        (root / name).write_text('fixture')


def test_remove_slow_preserves_history_and_external_files(roots):
    slow, data = roots
    populate(slow, cleanup.SLOW_FILES)
    populate(data, cleanup.STORE_FILES)
    outside = slow / 'my-document.xlsx'
    outside.write_text('original')
    cleanup.remove_slow()
    assert list(slow.iterdir()) == [outside]
    assert outside.read_text() == 'original'
    assert {p.name for p in data.iterdir()} == cleanup.STORE_FILES


def test_uninstall_removes_owned_data_and_preserves_originals(roots):
    slow, data = roots
    populate(slow, cleanup.SLOW_FILES)
    populate(data, cleanup.STORE_FILES)
    original = data.parent / 'original.pdf'
    original.write_text('original')
    cleanup.uninstall_data()
    assert not data.exists() and not slow.exists()
    assert original.read_text() == 'original'


@pytest.mark.parametrize('name', ['setup.lock', 'install.lock'])
def test_active_setup_prevents_removal(roots, name):
    slow, _ = roots
    (slow / 'runtime.json').write_text('keep')
    if name == 'setup.lock':
        lock = QLockFile(str(slow / name))
        assert lock.tryLock(0)
    else:
        (slow / name).touch()
    try:
        with pytest.raises(RuntimeError):
            cleanup.remove_slow()
        assert (slow / 'runtime.json').read_text() == 'keep'
    finally:
        if name == 'setup.lock':
            lock.unlock()


def test_running_application_blocks_full_uninstall(roots):
    slow, data = roots
    (slow / 'runtime.json').write_text('keep')
    lock = QLockFile(str(data / 'instance.lock'))
    assert lock.tryLock(0)
    try:
        with pytest.raises(RuntimeError):
            cleanup.uninstall_data()
        assert (slow / 'runtime.json').exists()
    finally:
        lock.unlock()


def test_directory_link_does_not_delete_target(roots):
    slow, data = roots
    original = data / 'original.pdf'
    original.write_text('keep')
    try:
        (slow / 'models').symlink_to(data, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation is unavailable')
    cleanup.remove_slow()
    assert original.read_text() == 'keep'
    assert not slow.exists()


def test_removal_failure_releases_locks_and_allows_retry(roots, monkeypatch):
    slow, _ = roots
    (slow / 'runtime.json').write_text('keep')
    original = cleanup.remove_entry
    def fail(path):
        raise PermissionError('File is in use')
    monkeypatch.setattr(cleanup, 'remove_entry', fail)
    with pytest.raises(PermissionError):
        cleanup.remove_slow()
    assert (slow / 'runtime.json').exists()
    assert not (slow / 'install.lock').exists()
    assert not (slow / 'setup.lock').exists()
    monkeypatch.setattr(cleanup, 'remove_entry', original)
    cleanup.remove_slow()
    assert not slow.exists()


def test_settings_removal_then_install_is_available(roots, monkeypatch, qtbot):
    slow, _ = roots
    (slow / 'setup-cancelled').touch()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.Yes)
    card = SlowSettings()
    qtbot.addWidget(card)
    assert card.remove_button.isEnabled()
    card.start_remove()
    qtbot.waitUntil(lambda: card.worker is None)
    assert not card.error
    assert not slow.exists()
    assert card.install_button.isEnabled()
    assert not card.remove_button.isEnabled()
    assert 'not installed' in card.status.text()


def test_settings_removal_refused_during_analysis(roots, qtbot):
    slow, _ = roots
    (slow / 'runtime.json').write_text('keep')
    card = SlowSettings(busy=lambda: True)
    qtbot.addWidget(card)
    card.start_remove()
    assert card.worker is None
    assert (slow / 'runtime.json').exists()


def test_external_python_is_never_uninstalled(roots, monkeypatch):
    from types import SimpleNamespace
    slow, _ = roots
    monkeypatch.setattr(cleanup, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(cleanup, 'registered_private_python', lambda root: False)
    monkeypatch.setattr(cleanup.subprocess, 'run', lambda *a, **kw: pytest.fail('External Python must be preserved'))
    cleanup.uninstall_private_python(slow)


def test_user_declines_removal(roots, monkeypatch, qtbot):
    slow, _ = roots
    (slow / 'setup-cancelled').touch()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.No)
    card = SlowSettings()
    qtbot.addWidget(card)
    card.start_remove()
    assert card.worker is None
    assert (slow / 'setup-cancelled').exists()
