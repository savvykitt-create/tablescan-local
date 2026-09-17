import hashlib
import io
import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest

from tablescan_local import slow_setup, slow_mode
from tablescan_local.slow_settings import SlowSettings


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv('TABLESCAN_SLOW_ROOT', str(tmp_path))
    monkeypatch.setattr(slow_setup.shutil, 'disk_usage', lambda path: SimpleNamespace(free=30 * 1024**3))
    return tmp_path


def test_status_rejects_incomplete_setup_even_with_valid_runtime(root, monkeypatch):
    monkeypatch.setattr(slow_mode, 'runtime_config', lambda: {})
    assert slow_setup.setup_status() == 'ready'
    (root / 'setup-pending').touch()
    assert slow_setup.setup_status() == 'failed'
    (root / 'install.lock').touch()
    assert slow_setup.setup_status() == 'installing'


def test_missing_and_broken_runtime_status(root):
    assert slow_setup.setup_status() == 'missing'
    (root / 'runtime.json').write_text('{}')
    assert slow_setup.setup_status() == 'failed'


def test_download_checks_hash_before_execution(root, monkeypatch):
    class Response(io.BytesIO):
        headers = {'Content-Length': '4'}
    monkeypatch.setattr(slow_setup.urllib.request, 'urlopen', lambda *a, **kw: Response(b'test'))
    destination = root / 'python.exe'
    with pytest.raises(RuntimeError, match='checksum'):
        slow_setup.download_python(destination, lambda *a: None)
    assert not destination.exists() and not destination.with_suffix('.part').exists()
    monkeypatch.setattr(slow_setup, 'PYTHON_SHA256', hashlib.sha256(b'test').hexdigest())
    progress = []
    slow_setup.download_python(destination, lambda *a: progress.append(a))
    assert destination.read_bytes() == b'test' and progress == [(4, 4)]


@pytest.mark.parametrize('fail', [False, True])
def test_setup_requires_fresh_successful_model_test(root, monkeypatch, fail):
    monkeypatch.setattr(slow_setup, 'find_python', lambda *a: ['python3.12'])
    monkeypatch.setattr(slow_mode, 'runtime_config', lambda: {})
    report = root / 'self-test/result.json'
    report.parent.mkdir()
    report.write_text('{"status":"passed"}')
    commands = []
    def run(command, log):
        commands.append(command)
        if '--slow-mode-self-test' in command:
            assert not report.exists()
            if fail:
                raise RuntimeError('test model failure')
            report.write_text('{"status":"passed"}')
    monkeypatch.setattr(slow_setup, 'run_command', run)
    if fail:
        with pytest.raises(RuntimeError, match='test model failure'):
            slow_setup.install(lambda *a: None)
        assert slow_setup.setup_status() == 'failed'
    else:
        slow_setup.install(lambda *a: None)
        assert slow_setup.setup_status() == 'ready'
    assert '--device' in commands[0] and 'auto' in commands[0]
    assert str(root) in [str(v) for v in commands[0]]


def test_clean_windows_bootstraps_python_without_launcher(root, monkeypatch):
    # Replace the module's sys object, never the interpreter's global platform.
    monkeypatch.setattr(slow_setup, 'sys', SimpleNamespace(platform='win32', frozen=True, executable='TableScanLocal.exe'))
    probes = iter([None, [str(root / 'python/python.exe')]])
    monkeypatch.setattr(slow_setup, 'find_python', lambda *a: next(probes))
    monkeypatch.setattr(slow_mode, 'runtime_config', lambda: {})
    monkeypatch.setattr(slow_setup, 'download_python', lambda path, progress: path.write_bytes(b'verified fixture'))
    commands = []
    def run(command, log, **kwargs):
        commands.append(command)
        if '--slow-mode-self-test' in command:
            report = root / 'self-test/result.json'
            report.parent.mkdir()
            report.write_text('{"status":"passed"}')
    monkeypatch.setattr(slow_setup, 'run_command', run)
    slow_setup.install(lambda *a: None)
    assert '/quiet' in commands[0] and 'InstallAllUsers=0' in commands[0]
    assert 'Include_launcher=0' in commands[0]
    assert commands[-1] == ['TableScanLocal.exe', '--slow-mode-self-test']
    assert not (root / 'setup-pending').exists()


def test_low_disk_space_does_not_start_or_invalidate_install(root, monkeypatch):
    monkeypatch.setattr(slow_setup.shutil, 'disk_usage', lambda path: SimpleNamespace(free=1))
    with pytest.raises(RuntimeError, match='15 GB'):
        slow_setup.install(lambda *a: None)
    assert not (root / 'setup-pending').exists()


@pytest.mark.parametrize('fail', [False, True])
def test_settings_worker_completion_and_retry(root, monkeypatch, qtbot, fail):
    state = ['missing']
    monkeypatch.setattr(slow_setup, 'setup_status', lambda: state[0])
    def install(progress):
        progress('Downloading Python…', 1, 2)
        if fail:
            raise RuntimeError('Network unavailable')
        state[0] = 'ready'
    monkeypatch.setattr(slow_setup, 'install', install)
    card = SlowSettings()
    qtbot.addWidget(card)
    card.start_install()
    assert not card.install_button.isEnabled()
    qtbot.waitUntil(lambda: card.worker is None)
    assert card.lock is None and not (root / 'setup.lock').exists()
    assert card.install_button.isEnabled() == fail
    assert bool(card.error) == fail
    if fail:
        assert 'Network unavailable' in card.error
    else:
        assert 'ready' in card.status.text()


def test_active_analysis_prevents_setup(root, monkeypatch, qtbot):
    card = SlowSettings(busy=lambda: True)
    qtbot.addWidget(card)
    card.start_install()
    assert card.worker is None and not (root / 'setup.lock').exists()


def test_frozen_setup_files_are_bundled():
    from pathlib import Path
    spec = Path(__file__).parents[1] / 'packaging/tablescan_local.spec'
    text = spec.read_text()
    assert '"packaging/install_slow_mode.py"), "tablescan_local/setup"' in text
    assert '"src/tablescan_local/slow_runtime.py"), "tablescan_local/setup"' in text
    assert slow_setup.installer_script().is_file()
