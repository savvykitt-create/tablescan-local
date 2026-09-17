"""Install the optional local runtime on macOS, Windows or Linux (Python 3.12)."""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Works both in the source checkout and in the Windows installer's tools folder.
contract = Path(__file__).with_name('slow_runtime.py')
if not contract.exists():
    contract = Path(__file__).resolve().parents[1] / 'src/tablescan_local/slow_runtime.py'
spec = importlib.util.spec_from_file_location('tablescan_slow_contract', contract)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
MODEL_SETS = runtime.MODEL_SETS
COMMON = ['transformers==5.17.0', 'huggingface_hub==1.31.0', 'pillow==12.3.0']


def torch_index(device):
    cuda = device == 'cuda' or (device == 'auto' and shutil.which('nvidia-smi') is not None)
    return 'https://download.pytorch.org/whl/' + ('cu128' if cuda else 'cpu')


def install_dependencies(python, backend, device):
    # pip must never escape the application's dedicated virtual environment,
    # including when the user has configured global pip target/user options.
    subprocess.run([str(python), '-c',
                    'import sys; assert sys.prefix != sys.base_prefix, "Expected a virtual environment"'], check=True)
    for name in ('PIP_TARGET', 'PIP_PREFIX', 'PIP_USER', 'PYTHONHOME', 'PYTHONPATH'):
        os.environ.pop(name, None)
    os.environ.update(PIP_CONFIG_FILE=os.devnull, PIP_REQUIRE_VIRTUALENV='1', PYTHONNOUSERSITE='1')
    subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', 'pip'], check=True)
    if backend == 'mlx':
        packages = ['mlx-vlm==0.7.0', 'mlx==0.32.2', 'mlx-metal==0.32.2']
    else:
        # Force the selected build when switching an existing CPU/CUDA installation.
        command = [str(python), '-m', 'pip', 'install', '--upgrade', '--force-reinstall',
                   'torch==2.10.0', 'torchvision==0.25.0']
        if sys.platform != 'darwin':
            command += ['--index-url', torch_index(device)]
        subprocess.run(command, check=True)
        packages = []
    subprocess.run([str(python), '-m', 'pip', 'install', *packages, *COMMON], check=True)


def download(root, backend, device, cpu_dtype):
    os.environ['HF_HOME'] = str(root / 'cache')
    from huggingface_hub import snapshot_download
    if backend == 'transformers':
        import torch
        from transformers import AutoProcessor, AutoModelForImageTextToText
        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable. Update the NVIDIA driver or install with --device cpu.')
    config = {'schema': 2, 'backend': backend, 'device': device,
              'python': str(runtime.venv_python(root / 'runtime')), 'cpu_dtype': cpu_dtype}
    for key, (repo, revision) in MODEL_SETS[backend].items():
        destination = root / 'models' / backend / key
        print('Downloading', repo, flush=True)
        snapshot_download(repo, revision=revision, local_dir=destination,
                          allow_patterns=['*.json', '*.safetensors', '*.txt', '*.model', '*.jinja', '*.tiktoken', '*LICENSE*', '*NOTICE*', 'README.md'], max_workers=4)
        index = destination / 'model.safetensors.index.json'
        weights = set(json.loads(index.read_text(encoding='utf-8'))['weight_map'].values()) if index.exists() else {'model.safetensors'}
        if not weights or not all((destination / name).is_file() and (destination / name).stat().st_size > 0 for name in weights):
            raise RuntimeError('Incomplete model download: ' + repo)
        (destination / 'audit-revision.json').write_text(json.dumps({'repo': repo, 'revision': revision}), encoding='utf-8')
        config[key] = str(destination)
    temporary = root / 'runtime.tmp'
    temporary.write_text(json.dumps(config, indent=2), encoding='utf-8')
    temporary.replace(root / 'runtime.json')
    print('Slow mode ready:', root, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['mlx', 'transformers'], default=runtime.default_backend())
    parser.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='auto',
                        help='Windows/Linux: auto selects NVIDIA when possible, otherwise CPU')
    parser.add_argument('--cpu-dtype', choices=['bfloat16', 'float32'], default='bfloat16',
                        help='BF16 uses less RAM; float32 is a compatibility option needing about 32 GB RAM')
    parser.add_argument('--copy-runtime', type=Path, help='Maintainer option: reuse an MLX environment')
    parser.add_argument('--lock-owner', default='', help=argparse.SUPPRESS)
    parser.add_argument('--download', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--root', type=Path, default=runtime.runtime_root())
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error('Python 3.12 is required; on Windows run: py -3.12 packaging/install_slow_mode.py')
    if args.backend == 'mlx' and runtime.default_backend() != 'mlx':
        parser.error('MLX requires Apple Silicon macOS; use --backend transformers')
    if args.backend == 'mlx' and args.device != 'auto':
        parser.error('MLX uses Metal; --device applies only to the transformers backend')
    if args.copy_runtime and args.backend != 'mlx':
        parser.error('--copy-runtime is only supported for MLX')
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.download:
        download(root, args.backend, args.device, args.cpu_dtype)
        return
    lock = root / 'install.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        parser.error(f'Installation already in progress. If it was interrupted, remove {lock} and retry.')
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(args.lock_owner)
    try:
        # A partially repaired environment must never be advertised as ready.
        (root / 'runtime.json').unlink(missing_ok=True)
        venv = root / 'runtime'
        python = runtime.venv_python(venv)
        healthy = python.exists() and (venv / 'pyvenv.cfg').is_file()
        if healthy:
            healthy = subprocess.run([str(python), '-c', 'import pip, sys; assert sys.prefix != sys.base_prefix']).returncode == 0
        if not healthy:
            if args.copy_runtime:
                subprocess.run(['cp', '-cR', str(args.copy_runtime.resolve()), str(venv)], check=True)
            else:
                subprocess.run([sys.executable, '-m', 'venv', '--clear', str(venv)], check=True)
        if not args.copy_runtime:
            install_dependencies(python, args.backend, args.device)
        env = os.environ.copy()
        env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        subprocess.run([str(python), str(Path(__file__).resolve()), '--download', '--root', str(root),
                        '--backend', args.backend, '--device', args.device, '--cpu-dtype', args.cpu_dtype], check=True, env=env)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
