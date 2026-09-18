"""One-click optional runtime setup, outside the application's Python environment."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import signal
import ssl
import certifi
import subprocess
import tarfile
import tempfile
import threading
from pathlib import Path
import shutil
import sys
import urllib.request
import uuid

from .slow_runtime import runtime_root

# Published CPython SBOM: python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe.spdx.json
PYTHON_URL = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
PYTHON_SHA256 = '67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb'
MAC_PYTHON_URL = ('https://github.com/astral-sh/python-build-standalone/releases/download/20260901/'
                  'cpython-3.12.14%2B20260901-aarch64-apple-darwin-install_only_stripped.tar.gz')
MAC_PYTHON_SHA256 = '81a359f1cfadd4da11766534c5913791cea55f26e1bb902cacd2a531bb1e4b2b'


class ProcessStopError(Exception):
    pass


class SetupCancelled(Exception):
    pass


class Cancellation:
    def __init__(self):
        self.event = threading.Event()

    def cancel(self):
        self.event.set()

    def check(self):
        if self.event.is_set():
            raise SetupCancelled('Slow mode installation cancelled.')


def stop_process_tree(process, log):
    if sys.platform == 'win32':
        result = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                stdout=log, stderr=log, check=False,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode and process.poll() is None:
            raise ProcessStopError('Could not stop installation processes. See installation details.')
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def setup_status():
    from .slow_mode import runtime_config
    root = runtime_root()
    if (root / 'install.lock').exists():
        return 'installing'
    if (root / 'setup-cancelled').exists():
        return 'cancelled'
    if (root / 'setup-pending').exists():
        return 'failed'
    try:
        runtime_config()
        return 'ready'
    except RuntimeError:
        return 'failed' if (root / 'runtime.json').exists() else 'missing'


def installer_script():
    bundled = Path(__file__).with_name('setup') / 'install_slow_mode.py'
    return bundled if bundled.is_file() else Path(__file__).resolve().parents[2] / 'packaging/install_slow_mode.py'


def external_environment():
    env = os.environ.copy()
    for name in list(env):
        if name.startswith(('_PYI', 'DYLD_', 'QT_')) or name in {'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER', 'PIP_REQUIRE_VIRTUALENV'}:
            env.pop(name, None)
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        env['PATH'] = os.pathsep.join(p for p in env.get('PATH', '').split(os.pathsep)
                                      if not os.path.normcase(p).startswith(os.path.normcase(str(bundle))))
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
               PIP_NO_CACHE_DIR='1', PYTHONNOUSERSITE='1', PIP_CONFIG_FILE=os.devnull, PYINSTALLER_RESET_ENVIRONMENT='1')
    return env


def run_command(command, log, *, accepted=(0,), cancel=None):
    from .slow_mode import start_worker
    cancel = cancel or Cancellation()
    cancel.check()
    process = start_worker([str(part) for part in command], log, external_environment(), process_tree=True)
    try:
        while process.poll() is None:
            if cancel.event.wait(0.1):
                stop_process_tree(process, log)
                cancel.check()
        cancel.check()
        if process.returncode not in accepted:
            raise RuntimeError(f'Process exited with code {process.returncode}. See installation details.')
    finally:
        if process.poll() is None:
            stop_process_tree(process, log)


def probe_python(command, log, cancel=None):
    try:
        run_command([*command, '-c', 'import sys; assert sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32'], log, cancel=cancel)
        return command
    except (OSError, RuntimeError):
        return None


def find_python(root, log, cancel=None):
    candidates = []
    if not getattr(sys, 'frozen', False):
        candidates.append([sys.executable])
    if sys.platform == 'win32':
        candidates += [[str(root / 'python/python.exe')], ['py', '-3.12'],
                       [str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Python/Python312/python.exe')]]
    else:
        candidates += [[str(root / 'python/bin/python3.12')], ['python3.12'], ['/opt/homebrew/bin/python3.12'], ['/usr/local/bin/python3.12']]
    for command in candidates:
        if probe_python(command, log, cancel):
            return command
    return None


def download_context():
    # Frozen macOS builds cannot rely on the build machine's OpenSSL CA path.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def download_python(destination, progress, cancel=None, *, url=PYTHON_URL, expected=PYTHON_SHA256):
    cancel = cancel or Cancellation()
    cancel.check()
    temporary = destination.with_suffix('.part')
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=15, context=download_context()) as response, temporary.open('wb') as output:
            total = int(response.headers.get('Content-Length', 0))
            received = 0
            while True:
                cancel.check()
                chunk = response.read1(64 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                progress(received, total)
        cancel.check()
        if digest.hexdigest() != expected:
            raise RuntimeError('Python download checksum mismatch. Retry installation.')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def install_mac_python(root, progress, cancel):
    package = root / 'python-macos.tar.gz'
    download_python(package, lambda value, maximum: progress('Downloading Python…', value, maximum),
                    cancel, url=MAC_PYTHON_URL, expected=MAC_PYTHON_SHA256)
    progress('Installing Python…', 0, 0)
    try:
        with tempfile.TemporaryDirectory(prefix='python-extract-', dir=root) as temporary:
            with tarfile.open(package) as archive:
                for member in archive:
                    cancel.check()
                    archive.extract(member, temporary, filter='data')
            cancel.check()
            destination = root / 'python'
            if destination.exists():
                shutil.rmtree(destination)
            (Path(temporary) / 'python').replace(destination)
    finally:
        package.unlink(missing_ok=True)


def install(progress, cancel=None):
    """Called on a worker thread. A GUI QLockFile covers bootstrap and validation."""
    cancel = cancel or Cancellation()
    cancel.check()
    root = runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'install.lock').exists():
        raise RuntimeError('Another installation is running (install.lock).')
    script = installer_script()
    if not script.is_file():
        raise RuntimeError('The application is missing its setup files. Reinstall TableScan.')
    if shutil.disk_usage(root).free < 15 * 1024**3:
        raise RuntimeError('At least 15 GB of free disk space is required.')
    pending = root / 'setup-pending'
    pending.write_text('Installation and model verification have not completed.', encoding='utf-8')
    (root / 'setup-cancelled').unlink(missing_ok=True)
    try:
        with (root / 'setup.log').open('w', encoding='utf-8', buffering=1) as log:
            progress('Preparing Slow mode…', 0, 0)
            python = find_python(root, log, cancel)
            if python is None:
                if sys.platform == 'darwin' and platform.machine() == 'arm64':
                    install_mac_python(root, progress, cancel)
                elif sys.platform == 'win32':
                    package = root / 'python-3.12.10-amd64.exe'
                    download_python(package, lambda value, maximum: progress('Downloading Python…', value, maximum), cancel)
                    progress('Installing Python…', 0, 0)
                    run_command([package, '/quiet', 'InstallAllUsers=0', f'TargetDir={root / "python"}',
                                 'Include_launcher=0', 'Include_test=0', 'Include_doc=0', 'Include_tcltk=0',
                                 'Include_pip=1', 'PrependPath=0', 'Shortcuts=0', 'AssociateFiles=0'], log,
                                accepted=(0, 3010), cancel=cancel)
                    # Retain the verified installer for offline removal of private Python.
                else:
                    raise RuntimeError('Install Python 3.12, then retry Slow mode installation.')
                python = find_python(root, log, cancel)
                if python is None:
                    raise RuntimeError('Python installation failed. See installation details.')
            progress('Creating isolated environment and installing models…', 0, 0)
            owner = uuid.uuid4().hex
            try:
                run_command([*python, script, '--root', root, '--device', 'auto', '--lock-owner', owner], log, cancel=cancel)
            except SetupCancelled:
                # The entire installer process tree has exited; its finally block may
                # not run after termination. Retain downloaded files for a retry.
                lock = root / 'install.lock'
                if lock.exists() and lock.read_text(encoding='utf-8') == owner:
                    lock.unlink()
                raise
            progress('Checking both models — this can take a long time on CPU…', 0, 0)
            (root / 'self-test/result.json').unlink(missing_ok=True)
            if getattr(sys, 'frozen', False):
                command = [sys.executable, '--slow-mode-self-test']
            else:
                command = [sys.executable, str(Path(__file__).resolve().parents[2] / 'tablescan_local_app.py'), '--slow-mode-self-test']
            run_command(command, log, cancel=cancel)
            report = json.loads((root / 'self-test/result.json').read_text(encoding='utf-8'))
            if report.get('status') != 'passed':
                raise RuntimeError('Model verification failed. See installation details.')
            from .slow_mode import runtime_config
            runtime_config()
            cancel.check()
            pending.unlink()
    except ProcessStopError:
        raise
    except Exception:
        if cancel.event.is_set():
            (root / 'setup-cancelled').touch()
            cancel.check()
        raise
