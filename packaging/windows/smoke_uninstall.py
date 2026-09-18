"""Exercise the actual installer/uninstaller in a disposable Windows directory."""
import os
from pathlib import Path
import subprocess
import tempfile

from PySide6.QtCore import QCoreApplication, QLockFile


def run(args, env):
    return subprocess.run([str(arg) for arg in args], env=env, timeout=180).returncode


app = QCoreApplication([])
installer = next(Path('release').glob('*Setup-x64.exe')).resolve()
with tempfile.TemporaryDirectory(prefix='tablescan-uninstall-test-') as directory:
    root = Path(directory)
    destination = root / 'application'
    data = root / 'data'
    slow = root / 'slow-mode'
    env = dict(os.environ, TABLESCAN_DATA_DIR=str(data), TABLESCAN_SLOW_ROOT=str(slow))
    flags = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART']
    assert run([installer, *flags, '/SP-', f'/DIR={destination}'], env) == 0
    executable = destination / 'TableScanLocal.exe'
    uninstaller = destination / 'unins000.exe'
    assert executable.exists() and uninstaller.exists()
    for path in [data / 'jobs/job/source.pdf', data / 'templates/template.json',
                 data / 'preferences.ini', data / 'tablescan.db', data / 'analysis-queue.json',
                 slow / 'runtime/python.exe', slow / 'models/model.safetensors',
                 slow / 'cache/partial', slow / 'setup-cancelled']:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
    # Force the private bootstrap path, including its Windows registration and
    # cached installer. The CI Python used to run this script is not removed.
    from tablescan_local.slow_setup import download_python
    from tablescan_local.cleanup import registered_private_python
    package = slow / 'python-3.12.10-amd64.exe'
    download_python(package, lambda *args: None)
    assert run([package, '/quiet', 'InstallAllUsers=0', f'TargetDir={slow / "python"}',
                'Include_launcher=0', 'Include_test=0', 'Include_doc=0', 'Include_tcltk=0',
                'Include_pip=1', 'PrependPath=0', 'Shortcuts=0', 'AssociateFiles=0'], env) == 0
    assert registered_private_python(slow)
    import hashlib
    import winreg
    from tablescan_local.cleanup import PYTHON_CACHED_SHA256
    cache_hashes = []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                       r'Software\Microsoft\Windows\CurrentVersion\Uninstall') as parent:
        for index in range(winreg.QueryInfoKey(parent)[0]):
            with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as key:
                try:
                    candidate = Path(winreg.QueryValueEx(key, 'BundleCachePath')[0])
                    if candidate.is_file():
                        cache_hashes.append(hashlib.sha256(candidate.read_bytes()).hexdigest())
                except FileNotFoundError:
                    pass
    assert PYTHON_CACHED_SHA256 in cache_hashes, cache_hashes
    # Exercise migration from v1.0.2, which did not retain the downloaded installer.
    package.unlink()
    original = root / 'original.pdf' 
    original.write_text('keep')
    export = root / 'export.xlsx'
    export.write_text('keep')
    lock = QLockFile(str(data / 'instance.lock'))
    assert lock.tryLock(0)
    try:
        assert run([uninstaller, *flags], env) != 0
        assert executable.exists(), 'Uninstall continued after cleanup was refused'
        assert (data / 'tablescan.db').exists()
        assert (slow / 'models/model.safetensors').exists()
    finally:
        lock.unlock()
    assert run([uninstaller, *flags], env) == 0
    assert not data.exists() and not slow.exists()
    assert not executable.exists()
    assert not registered_private_python(slow)
    assert original.read_text() == export.read_text() == 'keep'
    # Also verify the actual default Qt/Slow locations used by a clean install.
    from tablescan_local.cleanup import application_data_root
    from tablescan_local.slow_runtime import runtime_root
    app.setApplicationName('TableScan Local')
    app.setOrganizationName('TableScan Local')
    default_data, default_slow = application_data_root(), runtime_root()
    assert not default_data.exists() and not default_slow.exists(), 'CI profile must be clean'
    default_data.mkdir(parents=True)
    default_slow.mkdir(parents=True)
    (default_data / 'preferences.ini').write_text('fixture')
    (default_slow / 'setup-cancelled').touch()
    assert run([installer, *flags, '/SP-', f'/DIR={destination}'], dict(os.environ)) == 0
    assert run([uninstaller, *flags], dict(os.environ)) == 0
    assert not default_data.exists() and not default_slow.exists()
print('Windows installer and complete uninstall passed, including active-app refusal.')
