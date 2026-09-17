"""One-click optional runtime setup, outside the application's Python environment."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import urllib.request

from .slow_runtime import runtime_root, venv_python

# Published CPython SBOM: python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe.spdx.json
PYTHON_URL = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
PYTHON_SHA256 = '67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb'


def setup_status():
    from .slow_mode import runtime_config
    root = runtime_root()
    if (root / 'install.lock').exists():
        return 'installing'
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
        if name.startswith(('_PYI', 'DYLD_', 'QT_')) or name in {'PYTHONHOME', 'PYTHONPATH'}:
            env.pop(name, None)
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        env['PATH'] = os.pathsep.join(p for p in env.get('PATH', '').split(os.pathsep)
                                      if not os.path.normcase(p).startswith(os.path.normcase(str(bundle))))
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
               PIP_NO_CACHE_DIR='1', PYINSTALLER_RESET_ENVIRONMENT='1')
    return env


def run_command(command, log, *, accepted=(0,)):
    from .slow_mode import start_worker
    process = start_worker([str(part) for part in command], log, external_environment())
    code = process.wait()
    if code not in accepted:
        raise RuntimeError(f'Process exited with code {code}. See installation details.')


def probe_python(command, log):
    try:
        run_command([*command, '-c', 'import sys; assert sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32'], log)
        return command
    except (OSError, RuntimeError):
        return None


def find_python(root, log):
    candidates = [[str(venv_python(root / 'runtime'))]]
    if not getattr(sys, 'frozen', False):
        candidates.append([sys.executable])
    if sys.platform == 'win32':
        candidates += [[str(root / 'python/python.exe')], ['py', '-3.12'],
                       [str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Python/Python312/python.exe')]]
    else:
        candidates += [['python3.12'], ['/opt/homebrew/bin/python3.12'], ['/usr/local/bin/python3.12']]
    for command in candidates:
        if probe_python(command, log):
            return command
    return None


def download_python(destination, progress):
    temporary = destination.with_suffix('.part')
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(PYTHON_URL, timeout=60) as response, temporary.open('wb') as output:
            total = int(response.headers.get('Content-Length', 0))
            received = 0
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                progress(received, total)
        if digest.hexdigest() != PYTHON_SHA256:
            raise RuntimeError('Python download checksum mismatch. Retry installation.')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def install(progress):
    """Called on a worker thread. A GUI QLockFile covers bootstrap and validation."""
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
    with (root / 'setup.log').open('w', encoding='utf-8', buffering=1) as log:
        progress('Preparing Slow mode…', 0, 0)
        python = find_python(root, log)
        if python is None:
            if sys.platform != 'win32':
                raise RuntimeError('Install Python 3.12, then retry Slow mode installation.')
            package = root / 'python-3.12.10-amd64.exe'
            download_python(package, lambda value, maximum: progress('Downloading Python…', value, maximum))
            progress('Installing Python…', 0, 0)
            run_command([package, '/quiet', 'InstallAllUsers=0', f'TargetDir={root / "python"}',
                         'Include_launcher=0', 'Include_test=0', 'Include_doc=0', 'Include_tcltk=0',
                         'Include_pip=1', 'PrependPath=0', 'Shortcuts=0', 'AssociateFiles=0'], log,
                        accepted=(0, 3010))
            python = find_python(root, log)
            if python is None:
                raise RuntimeError('Python installation failed. See installation details.')
            package.unlink(missing_ok=True)
        progress('Downloading and installing models…', 0, 0)
        run_command([*python, script, '--root', root, '--device', 'auto'], log)
        progress('Checking both models — this can take a long time on CPU…', 0, 0)
        (root / 'self-test/result.json').unlink(missing_ok=True)
        if getattr(sys, 'frozen', False):
            command = [sys.executable, '--slow-mode-self-test']
        else:
            command = [sys.executable, str(Path(__file__).resolve().parents[2] / 'tablescan_local_app.py'), '--slow-mode-self-test']
        run_command(command, log)
        report = json.loads((root / 'self-test/result.json').read_text(encoding='utf-8'))
        if report.get('status') != 'passed':
            raise RuntimeError('Model verification failed. See installation details.')
        from .slow_mode import runtime_config
        runtime_config()
        pending.unlink()
