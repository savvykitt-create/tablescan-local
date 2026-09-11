"""Install the optional, pinned Apple Silicon runtime; no documents uploaded.

Run with Python 3.12. --copy-runtime can reuse an already validated MLX venv.
The app discovers only the completed installation via runtime.json.
"""
import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

MODELS = {
    'qwen': ('mlx-community/Qwen3.5-4B-MLX-4bit', '32f3e8ecf65426fc3306969496342d504bfa13f3'),
    'glm': ('mlx-community/GLM-OCR-bf16', '24f15402e83baa0a80eeeaecf5480e172abc6f2e'),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--copy-runtime', type=Path)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--root', type=Path, default=Path.home() / 'Library/Application Support/TableScan Local/slow-mode')
    args = parser.parse_args()
    root = args.root.resolve()
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise SystemExit('Requires Apple Silicon macOS')
    root.mkdir(parents=True, exist_ok=True)
    if not args.download:
        venv = root / 'runtime'
        if not venv.exists():
            if args.copy_runtime:
                # APFS clone avoids duplicating several GB of validated packages.
                subprocess.run(['cp', '-cR', str(args.copy_runtime.resolve()), str(venv)], check=True)
            else:
                subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
                subprocess.run([str(venv/'bin/python'), '-m', 'pip', 'install',
                                'mlx-vlm==0.7.0', 'mlx==0.32.2', 'mlx-metal==0.32.2',
                                'transformers==5.17.0', 'huggingface_hub==1.31.0', 'pillow==12.3.0'], check=True)
        subprocess.run([str(venv/'bin/python'), __file__, '--download', '--root', str(root)], check=True)
        return
    import os
    os.environ['HF_HOME'] = str(root / 'cache')
    from huggingface_hub import snapshot_download
    config = {'python': str(root/'runtime/bin/python')}
    for key, (repo, revision) in MODELS.items():
        destination = root / 'models' / key
        print('Downloading', repo, flush=True)
        snapshot_download(repo, revision=revision, local_dir=destination,
                          allow_patterns=['*.json', '*.safetensors', '*.txt', '*.model', '*.jinja', '*.tiktoken', '*LICENSE*', '*NOTICE*', 'README.md'], max_workers=4)
        (destination/'audit-revision.json').write_text(json.dumps({'repo': repo, 'revision': revision}))
        config[key] = str(destination)
    temporary = root/'runtime.tmp'
    temporary.write_text(json.dumps(config, indent=2))
    temporary.replace(root/'runtime.json')
    print('Slow mode ready:', root, flush=True)


if __name__ == '__main__':
    main()
