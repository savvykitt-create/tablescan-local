"""Optional full-weight CPU recognition check on a synthetic two-cell image.

Runs on adequately sized hosts only. A skip is explicit and is NOT an accuracy
result. No user documents or annotations are read.
"""
import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if sys.platform == 'win32':
        class Memory(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ['total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended']]
        memory = Memory(); memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise OSError('Cannot query physical memory')
        print(f'Physical RAM: {memory.total / 1024**3:.1f} GiB', flush=True)
        if memory.total < 12 * 1024**3:
            print('FULL_CPU_NOT_RUN: host has less than 12 GiB physical RAM.', flush=True)
            return
    if shutil.disk_usage(tempfile.gettempdir()).free < 14 * 1024**3:
        print('FULL_CPU_NOT_RUN: host has less than 14 GiB free temporary disk space.', flush=True)
        return
    installer = load_module('installer', ROOT / 'packaging/install_slow_mode.py')
    parser = load_module('parser', ROOT / 'src/tablescan_local/slow_parsing.py')
    with tempfile.TemporaryDirectory(prefix='tablescan-full-cpu-') as directory:
        root = Path(directory)
        installer.download(root, 'transformers', 'cpu', 'bfloat16')
        config = json.loads((root / 'runtime.json').read_text(encoding='utf-8'))
        font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/arial.ttf'
        font = ImageFont.truetype(str(font_path), 48) if font_path.exists() else ImageFont.load_default(size=48)
        row = Image.new('RGB', (600, 100), 'white')
        draw = ImageDraw.Draw(row)
        draw.text((20, 20), '12.3', font=font, fill='black')
        draw.text((320, 20), '45.6', font=font, fill='black')
        row.save(root / 'row.png')
        table = Image.new('RGB', (685, 100), 'white'); table.paste(row, (85, 0))
        ImageDraw.Draw(table).text((8, 25), '1', font=ImageFont.load_default(size=24), fill='black')
        table.save(root / 'table.png')
        for kind in ['qwen', 'glm']:
            record = {'id': 'test', 'image': str(root / ('table.png' if kind == 'qwen' else 'row.png')), 'rows': 1, 'columns': 2}
            request = {'kind': kind, 'backend': 'transformers', 'device': 'cpu', 'cpu_dtype': 'bfloat16', 'model': config[kind], 'records': [record]}
            request_path = root / f'{kind}-request.json'; output = root / f'{kind}-response.json'
            request_path.write_text(json.dumps(request), encoding='utf-8')
            env = os.environ.copy(); env['PYTHONUTF8'] = '1'
            subprocess.run([sys.executable, str(ROOT / 'src/tablescan_local/slow_runner.py'), str(request_path), str(output)],
                           check=True, timeout=1200, env=env)
            response = json.loads(output.read_text(encoding='utf-8'))[0]
            values = parser.parse_table(response['raw'], 1, 2).get(1) if kind == 'qwen' else parser.row_values(response['raw'], 2)
            if values is None or [parser.num(v) for v in values] != ['12.3', '45.6']:
                raise AssertionError(f'{kind}: {response}')
            print(json.dumps({'full_cpu_model': kind, 'status': 'passed', 'seconds': response['seconds'], 'values': values}), flush=True)


if __name__ == '__main__':
    main()
