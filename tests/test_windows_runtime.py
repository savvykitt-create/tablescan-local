"""Cross-platform regression tests; these also run on the native Windows CI runner."""
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from tablescan_local import slow_mode, slow_runtime
from tablescan_local.slow_runner import choose_device
from tablescan_local.imaging import read_image, write_image
from tablescan_local.pipeline import _write_crop


def test_windows_user_profile_and_python_paths(monkeypatch, tmp_path):
    monkeypatch.delenv('TABLESCAN_SLOW_ROOT', raising=False)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'Пользователь'))
    assert slow_runtime.default_backend() == 'transformers'
    assert slow_runtime.runtime_root() == tmp_path / 'Пользователь/TableScan Local/slow-mode'
    assert slow_runtime.venv_python(tmp_path) == tmp_path / 'Scripts/python.exe'


def make_runtime(tmp_path, monkeypatch, backend='transformers'):
    monkeypatch.setenv('TABLESCAN_SLOW_ROOT', str(tmp_path))
    config = {'python': sys.executable, 'backend': backend, 'device': 'cpu'}
    for key, (repo, revision) in slow_runtime.MODEL_SETS[backend].items():
        folder = tmp_path / key
        folder.mkdir()
        (folder / 'audit-revision.json').write_text(json.dumps({'repo': repo, 'revision': revision}), encoding='utf-8')
        (folder / 'model.safetensors').write_bytes(b'test')
        config[key] = str(folder)
    (tmp_path / 'runtime.json').write_text(json.dumps(config), encoding='utf-8')
    return config


def test_runtime_validates_actual_backend_and_revision(tmp_path, monkeypatch):
    config = make_runtime(tmp_path, monkeypatch)
    assert slow_mode.runtime_config()['backend'] == 'transformers'
    (Path(config['qwen']) / 'audit-revision.json').write_text('{}')
    with pytest.raises(RuntimeError):
        slow_mode.runtime_config()


@pytest.mark.parametrize('contents', [{}, {'weight_map': {}}, {'weight_map': {'a': 'missing.safetensors'}}])
def test_partial_weights_never_advertised_ready(tmp_path, monkeypatch, contents):
    config = make_runtime(tmp_path, monkeypatch)
    (Path(config['qwen']) / 'model.safetensors.index.json').write_text(json.dumps(contents))
    with pytest.raises(RuntimeError):
        slow_mode.runtime_config()


def test_old_mac_installation_remains_compatible(tmp_path, monkeypatch):
    config = make_runtime(tmp_path, monkeypatch, 'mlx')
    config.pop('backend'); config.pop('device')
    (tmp_path / 'runtime.json').write_text(json.dumps(config))
    monkeypatch.setattr(slow_mode, 'default_backend', lambda: 'mlx')
    assert slow_mode.runtime_config()['backend'] == 'mlx'


def test_cpu_auto_and_explicit_cuda_selection():
    cuda = Mock()
    cuda.is_available.return_value = False
    torch = SimpleNamespace(cuda=cuda)
    assert choose_device(torch, 'cpu', 'qwen') == 'cpu'
    assert choose_device(torch, 'auto', 'qwen') == 'cpu'
    with pytest.raises(RuntimeError, match='CUDA'):
        choose_device(torch, 'cuda', 'qwen')
    cuda.is_available.return_value = True
    cuda.mem_get_info.return_value = (6 * 1024**3, 8 * 1024**3)
    assert choose_device(torch, 'auto', 'qwen') == 'cpu'
    assert choose_device(torch, 'auto', 'glm') == 'cuda'
    cuda.mem_get_info.return_value = (14 * 1024**3, 16 * 1024**3)
    assert choose_device(torch, 'auto', 'qwen') == 'cuda'


def test_unicode_crops_preserve_pixels(tmp_path):
    crop = np.arange(60, dtype=np.uint8).reshape(4, 5, 3)
    path = _write_crop(crop, tmp_path / 'Замеры пользователя', 'ячейка 1.png')
    assert np.array_equal(read_image(path), crop)
    assert read_image(tmp_path / 'missing.png') is None
    with pytest.raises(OSError):
        write_image(tmp_path / 'missing/fail.png', crop)


def test_real_worker_ipc_utf8_and_environment_isolation(tmp_path, monkeypatch):
    folder = tmp_path / 'Проверка IPC'
    folder.mkdir()
    runner = folder / 'slow_runner.py'
    runner.write_text('''import json, os, sys
from pathlib import Path
request, output = map(Path, sys.argv[1:])
r = json.loads(request.read_text(encoding='utf-8'))
assert r['backend'] == 'transformers' and r['device'] == 'cpu'
assert os.environ['HF_HUB_OFFLINE'] == '1'
assert 'PYTHONPATH' not in os.environ and 'QT_TEST' not in os.environ
output.write_text(json.dumps([{'id': x['id'], 'raw': 'Проверено 12,3'} for x in r['records']], ensure_ascii=False), encoding='utf-8')
''', encoding='utf-8')
    monkeypatch.setattr(slow_mode, '__file__', str(folder / 'slow_mode.py'))
    monkeypatch.setenv('PYTHONPATH', '/bad/path'); monkeypatch.setenv('QT_TEST', 'bad')
    result = slow_mode.run_model('qwen', [{'id': 'таблица'}], folder,
        {'python': sys.executable, 'qwen': 'local', 'backend': 'transformers', 'device': 'cpu'})
    assert result == [{'id': 'таблица', 'raw': 'Проверено 12,3'}]


def test_frozen_windows_dll_path_restored_on_spawn_failure(monkeypatch, tmp_path):
    import ctypes
    kernel = Mock()
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', 'C:\\bundle', raising=False)
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(kernel32=kernel), raising=False)
    monkeypatch.setattr(slow_mode.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    popen = Mock(side_effect=OSError('cannot start'))
    monkeypatch.setattr(slow_mode.subprocess, 'Popen', popen)
    with pytest.raises(OSError):
        slow_mode.start_worker(['python'], None, {})
    assert [call.args for call in kernel.SetDllDirectoryW.call_args_list] == [(None,), ('C:\\bundle',)]
    assert popen.call_args.kwargs['creationflags'] == 0x08000000


def installer_module():
    path = Path(__file__).parents[1] / 'packaging/install_slow_mode.py'
    spec = importlib.util.spec_from_file_location('install_slow_mode', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installer_repairs_existing_environment_and_invalidates_failed_setup(tmp_path, monkeypatch):
    installer = installer_module()
    python = installer.runtime.venv_python(tmp_path / 'runtime')
    python.parent.mkdir(parents=True); python.write_bytes(b'old environment')
    (tmp_path / 'runtime.json').write_text('{}')
    monkeypatch.setattr(sys, 'argv', ['install', '--root', str(tmp_path), '--backend', 'transformers', '--device', 'cpu'])
    dependencies = Mock(side_effect=RuntimeError('interrupted download'))
    monkeypatch.setattr(installer, 'install_dependencies', dependencies)
    with pytest.raises(RuntimeError):
        installer.main()
    dependencies.assert_called_once_with(python, 'transformers', 'cpu')
    assert not (tmp_path / 'runtime.json').exists()
    assert not (tmp_path / 'install.lock').exists()


def test_parallel_install_is_blocked_without_changing_active_config(tmp_path, monkeypatch):
    installer = installer_module()
    (tmp_path / 'install.lock').write_text('')
    (tmp_path / 'runtime.json').write_text('existing')
    monkeypatch.setattr(sys, 'argv', ['install', '--root', str(tmp_path)])
    with pytest.raises(SystemExit):
        installer.main()
    assert (tmp_path / 'runtime.json').read_text() == 'existing'


@pytest.mark.parametrize('device,should_retry', [('auto', True), ('cuda', False)])
def test_cuda_oom_retries_only_in_auto(tmp_path, monkeypatch, device, should_retry):
    from tablescan_local import slow_runner
    class OOM(Exception):
        pass
    cuda = SimpleNamespace(OutOfMemoryError=OOM, empty_cache=Mock())
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=cuda))
    request = tmp_path / 'request.json'; output = tmp_path / 'response.json'
    request.write_text(json.dumps({'backend': 'transformers', 'device': device}))
    output.write_text('partial response must be removed')
    monkeypatch.setattr(sys, 'argv', ['runner', str(request), str(output)])
    execute = Mock(side_effect=[OOM('out of memory'), None])
    monkeypatch.setattr(slow_runner, 'execute', execute)
    if should_retry:
        slow_runner.main()
        assert execute.call_count == 2
        assert execute.call_args.kwargs == {'device': 'cpu'}
        assert not output.exists()
    else:
        with pytest.raises(OOM):
            slow_runner.main()
        assert execute.call_count == 1


@pytest.mark.parametrize('contents', [[], None, {'schema': 99}, {'backend': ['invalid']}])
def test_corrupt_config_is_reported_as_runtime_error(tmp_path, monkeypatch, contents):
    monkeypatch.setenv('TABLESCAN_SLOW_ROOT', str(tmp_path))
    (tmp_path / 'runtime.json').write_text(json.dumps(contents), encoding='utf-8')
    with pytest.raises(RuntimeError):
        slow_mode.runtime_config()


@pytest.mark.parametrize('platform', ['win32', 'darwin', 'linux'])
def test_cpu_dispatch_limit_precedes_library_loading(monkeypatch, platform):
    from tablescan_local.slow_runner import configure_cpu_environment
    monkeypatch.setattr(sys, 'platform', platform)
    for name in ('ONEDNN_MAX_CPU_ISA', 'DNNL_MAX_CPU_ISA'):
        monkeypatch.delenv(name, raising=False)
    configure_cpu_environment()
    for name in ('ONEDNN_MAX_CPU_ISA', 'DNNL_MAX_CPU_ISA'):
        assert os.environ.get(name) == ('AVX512_CORE_BF16' if platform == 'win32' else None)


def test_cpu_dispatch_preserves_explicit_compatibility_setting(monkeypatch):
    from tablescan_local.slow_runner import configure_cpu_environment
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('ONEDNN_MAX_CPU_ISA', 'AVX2')
    monkeypatch.setenv('DNNL_MAX_CPU_ISA', 'AVX2')
    configure_cpu_environment()
    assert os.environ['ONEDNN_MAX_CPU_ISA'] == 'AVX2'
    assert os.environ['DNNL_MAX_CPU_ISA'] == 'AVX2'
