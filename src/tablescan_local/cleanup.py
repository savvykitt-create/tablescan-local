"""Remove application-owned data without following links to user documents."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PySide6.QtCore import QLockFile, QStandardPaths
from .slow_runtime import runtime_root

SLOW_FILES = {'runtime', 'python', 'models', 'cache', 'self-test', 'runtime.json', 'runtime.tmp',
              'setup.log', 'setup-pending', 'setup-cancelled',
              'python-3.12.10-amd64.exe', 'python-3.12.10-amd64.part',
              'python-macos.tar.gz', 'python-macos.tar.part'}
# WiX Burn's cached engine from the SHA-pinned CPython 3.12.10 x64 bundle.
# Its attached MSI container is removed and original PE signature fields restored.
PYTHON_CACHED_SHA256 = '8515944637be89aab89d2dc5d247bc21331e9c4179dc25e2fc24df51a9eda934'

STORE_FILES = {'jobs', 'templates', 'template-samples', 'tablescan.db',
               'tablescan.db-wal', 'tablescan.db-shm', 'tablescan.db-journal',
               'preferences.ini', '.default-templates-installed'}


def remove_entry(path):
    # shutil.rmtree does not traverse directory junctions on Windows (Python 3.8+).
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def prune_empty(path):
    try:
        path.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        if path.exists() and any(path.iterdir()):
            return
        raise


@contextmanager
def locked(root, name):
    root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(root / name))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise RuntimeError('Close TableScan and wait for any Slow mode operation to finish, then retry.')
    try:
        yield
    finally:
        lock.unlock()
        prune_empty(root)


def registered_private_python(root):
    """Only the Python registration pointing into this Slow directory belongs to us."""
    private = root / 'python'
    if private.is_symlink() or getattr(private, 'is_junction', lambda: False)():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r'Software\Python\PythonCore\3.12\InstallPath') as key:
            installed = Path(winreg.QueryValueEx(key, '')[0]).resolve()
        return installed == (root / 'python').resolve()
    except FileNotFoundError:
        return False


def uninstall_private_python(root):
    if sys.platform != 'win32' or not registered_private_python(root):
        return
    import winreg
    from .slow_setup import PYTHON_SHA256
    candidates = [root / 'python-3.12.10-amd64.exe']
    # Earlier versions removed the downloaded installer. Burn retains its bundle
    # in Package Cache; use only an exact hash match, never an arbitrary command.
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                       r'Software\Microsoft\Windows\CurrentVersion\Uninstall') as parent:
        for index in range(winreg.QueryInfoKey(parent)[0]):
            with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as key:
                try:
                    candidates.append(Path(winreg.QueryValueEx(key, 'BundleCachePath')[0]))
                except FileNotFoundError:
                    pass
    for candidate in candidates:
        if candidate.is_file() and hashlib.sha256(candidate.read_bytes()).hexdigest() in {PYTHON_SHA256, PYTHON_CACHED_SHA256}:
            result = subprocess.run([str(candidate), '/uninstall', '/quiet', '/norestart'],
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode != 0 or registered_private_python(root):
                raise RuntimeError('Private Python could not be removed. Restart Windows and retry removal.')
            return
    raise RuntimeError('The private Python uninstaller is missing. Repair Slow mode before removing it.')


def remove_slow(root=None, *, setup_locked=False):
    root = root or runtime_root()
    if not root.exists():
        return
    if not setup_locked:
        with locked(root, 'setup.lock'):
            return remove_slow(root, setup_locked=True)
    # The command-line installer uses an exclusive file too. Hold it while deleting.
    marker = root / 'install.lock'
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError('Slow mode installation is running. Cancel it before removing Slow mode.') from None
    os.close(fd)
    try:
        uninstall_private_python(root)
        # Explicit ownership list also protects unrelated files in custom roots.
        for name in sorted(SLOW_FILES):
            remove_entry(root / name)
    finally:
        marker.unlink(missing_ok=True)


def application_data_root():
    return Path(os.environ.get('TABLESCAN_DATA_DIR') or
                QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))


def uninstall_data():
    root = application_data_root()
    with locked(root, 'instance.lock'):
        remove_slow()
        for name in sorted(STORE_FILES):
            remove_entry(root / name)
    # Remove empty organization directories, without recursively deleting them.
    prune_empty(root.parent)
    prune_empty(runtime_root().parent)


def uninstall_main():
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    app.setApplicationName('TableScan Local')
    app.setOrganizationName('TableScan Local')
    try:
        uninstall_data()
    except Exception as exc:
        if '--silent' not in sys.argv:
            QMessageBox.critical(None, 'TableScan Local', str(exc))
        return 1
    return 0
