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
    slow_setup.download_python(destination, lambda *a: progress.append(a), expected=hashlib.sha256(b'test').hexdigest())
    assert destination.read_bytes() == b'test' and progress == [(4, 4)]


@pytest.mark.parametrize('fail', [False, True])
def test_setup_requires_fresh_successful_model_test(root, monkeypatch, fail):
    monkeypatch.setattr(slow_setup, 'find_python', lambda *a: ['python3.12'])
    monkeypatch.setattr(slow_mode, 'runtime_config', lambda: {})
    report = root / 'self-test/result.json'
    report.parent.mkdir()
    report.write_text('{"status":"passed"}')
    commands = []
    def run(command, log, **kwargs):
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
    monkeypatch.setattr(slow_setup, 'download_python', lambda path, progress, cancel: path.write_bytes(b'verified fixture'))
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
    def install(progress, cancel):
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


def test_cancel_download_removes_partial_file(root, monkeypatch):
    class Response(io.BytesIO):
        headers = {}
    monkeypatch.setattr(slow_setup.urllib.request, 'urlopen', lambda *a, **kw: Response(b'x' * 200000))
    token = slow_setup.Cancellation()
    destination = root / 'python.exe'
    with pytest.raises(slow_setup.SetupCancelled):
        slow_setup.download_python(destination, lambda *a: token.cancel(), token)
    assert not destination.exists()
    assert not destination.with_suffix('.part').exists()


def test_cancel_stops_real_installer_and_child(tmp_path):
    import sys
    import threading
    import time
    heartbeat = tmp_path / 'heartbeat'
    child = "import time; from pathlib import Path; p=Path(" + repr(str(heartbeat)) + ");\nwhile True:\n p.write_text(str(time.time())); time.sleep(.05)"
    parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(60)'
    token = slow_setup.Cancellation()
    errors = []
    def run():
        try:
            with (tmp_path / 'process.log').open('w') as log:
                slow_setup.run_command([sys.executable, '-c', parent], log, cancel=token)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert heartbeat.exists()
    finally:
        token.cancel()
        thread.join(10)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], slow_setup.SetupCancelled)
    last_write = heartbeat.read_bytes()
    time.sleep(.25)
    assert heartbeat.read_bytes() == last_write


def test_cancelled_installer_releases_own_lock_and_can_retry(root, monkeypatch, qtbot):
    monkeypatch.setattr(slow_setup, 'find_python', lambda *a: ['python3.12'])
    monkeypatch.setattr(slow_mode, 'runtime_config', lambda: {})
    calls = []
    def run(command, log, *, cancel):
        calls.append(command)
        if '--lock-owner' in command:
            (root / 'install.lock').write_text(command[command.index('--lock-owner') + 1])
            while not cancel.event.wait(.01):
                pass
            cancel.check()
    monkeypatch.setattr(slow_setup, 'run_command', run)
    card = SlowSettings()
    qtbot.addWidget(card)
    card.start_install()
    qtbot.waitUntil(lambda: (root / 'install.lock').exists())
    assert card.cancel_button.isEnabled()
    card.cancel_install()
    qtbot.waitUntil(lambda: card.worker is None)
    assert not card.error
    assert not (root / 'install.lock').exists()
    assert not (root / 'setup.lock').exists()
    assert slow_setup.setup_status() == 'cancelled'
    assert card.install_button.isEnabled()
    assert len(calls) == 1
    def completed(command, log, **kwargs):
        if '--slow-mode-self-test' in command:
            report = root / 'self-test/result.json'
            report.parent.mkdir()
            report.write_text('{"status":"passed"}')
    monkeypatch.setattr(slow_setup, 'run_command', completed)
    card.start_install()
    qtbot.waitUntil(lambda: card.worker is None)
    assert slow_setup.setup_status() == 'ready'
    assert not card.install_button.isEnabled()


def test_mac_bootstraps_python_when_none_is_installed(root, monkeypatch):
    monkeypatch.setattr(slow_setup, 'sys', SimpleNamespace(platform='darwin', frozen=True, executable='TableScanLocal'))
    monkeypatch.setattr(slow_setup.platform, 'machine', lambda: 'arm64')
    probes = iter([None, [str(root / 'python/bin/python3.12')]])
    monkeypatch.setattr(slow_setup, 'find_python', lambda *a: next(probes))
    installed = []
    monkeypatch.setattr(slow_setup, 'install_mac_python', lambda *a: installed.append(a[0]))
    monkeypatch.setattr(slow_mode, 'runtime_config', lambda: {})
    def run(command, log, **kwargs):
        if '--slow-mode-self-test' in command:
            report = root / 'self-test/result.json'
            report.parent.mkdir()
            report.write_text('{"status":"passed"}')
    monkeypatch.setattr(slow_setup, 'run_command', run)
    slow_setup.install(lambda *a: None)
    assert installed == [root]
    assert slow_setup.setup_status() == 'ready'


def test_external_environment_ignores_global_python_and_pip_targets(monkeypatch):
    for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER'):
        monkeypatch.setenv(name, '/outside')
    env = slow_setup.external_environment()
    assert all(name not in env for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER'))
    assert env['PYTHONNOUSERSITE'] == '1'
    assert env['PIP_CONFIG_FILE'] == os.devnull


def test_mac_extraction_rejects_archive_paths_outside_destination(root, monkeypatch):
    import tarfile
    def download(destination, *args, **kwargs):
        with tarfile.open(destination, 'w:gz') as archive:
            entry = tarfile.TarInfo('../escape')
            entry.size = 4
            archive.addfile(entry, io.BytesIO(b'test'))
    monkeypatch.setattr(slow_setup, 'download_python', download)
    with pytest.raises(tarfile.FilterError):
        slow_setup.install_mac_python(root, lambda *a: None, slow_setup.Cancellation())
    assert not (root / 'python').exists()
    assert not (root / 'escape').exists()
    assert not (root / 'python-macos.tar.gz').exists()


def test_cancellation_does_not_delete_another_installers_lock(root, monkeypatch):
    monkeypatch.setattr(slow_setup, 'find_python', lambda *a: ['python3.12'])
    token = slow_setup.Cancellation()
    def run(command, log, **kwargs):
        (root / 'install.lock').write_text('another-installer')
        token.cancel()
        token.check()
    monkeypatch.setattr(slow_setup, 'run_command', run)
    with pytest.raises(slow_setup.SetupCancelled):
        slow_setup.install(lambda *a: None, token)
    assert (root / 'install.lock').read_text() == 'another-installer'


def test_bundled_certificates_work_without_system_ca_paths(monkeypatch, tmp_path):
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'missing.pem'))
    monkeypatch.setenv('SSL_CERT_DIR', str(tmp_path / 'missing'))
    assert slow_setup.download_context().get_ca_certs()
