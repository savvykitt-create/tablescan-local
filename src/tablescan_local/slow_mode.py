"""Conservative Qwen/GLM agreement, isolated from the primary OCR runtime."""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from .domain import PageResult, TableTemplate
from .imaging import cell_rect
from .ocr import OcrValue, constrain_reading
from .slow_parsing import num, parse_table, row_values, unwrap
from .i18n import tr

MODELS = {
    'qwen': ('mlx-community/Qwen3.5-4B-MLX-4bit', '32f3e8ecf65426fc3306969496342d504bfa13f3'),
    'glm': ('mlx-community/GLM-OCR-bf16', '24f15402e83baa0a80eeeaecf5480e172abc6f2e'),
}
POLICY = 'disputed-qwen-glm-original-v1-exclusion-guard'


def runtime_config() -> dict:
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise RuntimeError(tr('Slow mode доступен на Mac с Apple Silicon.'))
    path = Path.home() / 'Library/Application Support/TableScan Local/slow-mode/runtime.json'
    try:
        config = json.loads(path.read_text())
        if not Path(config['python']).is_file():
            raise ValueError('python')
        for key, (repo, revision) in MODELS.items():
            folder = Path(config[key])
            metadata = json.loads((folder / 'audit-revision.json').read_text())
            if metadata != {'repo': repo, 'revision': revision}:
                raise ValueError('revision')
            index = folder / 'model.safetensors.index.json'
            weights = set(json.loads(index.read_text())['weight_map'].values()) if index.exists() else {'model.safetensors'}
            if not all((folder / name).is_file() and (folder / name).stat().st_size > 0 for name in weights):
                raise ValueError('weights')
        return config
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(tr('Модуль slow mode не установлен или его файлы недоступны. Обычное распознавание доступно.')) from exc


def eligible_cells(page: PageResult, template: TableTemplate):
    return [cell for cell in page.cells
            if cell.needs_review and cell.row not in page.excluded_rows
            and cell.row >= template.header_rows and cell.column >= template.row_label_columns
            and template.column_rules[cell.column].role != 'ignored'
            and template.value_constraints(cell.row, cell.column)[0].value_format in {'numeric', 'integer'}]


def reading(value, rule):
    if isinstance(value, (dict, list, bool)):
        return None
    raw = '' if value is None else unwrap(str(value))
    result = constrain_reading(OcrValue(raw, 0.0), rule).text
    return result if num(result) is not None and not rule.hard_errors(result) else None


def disputed_rows(page, template, qwen):
    rows = set()
    first = template.row_label_columns
    for cell in eligible_cells(page, template):
        values = qwen.get(cell.row + 1)
        if values is None:
            continue
        rule, _ = template.value_constraints(cell.row, cell.column)
        proposed = reading(values[cell.column - first], rule)
        if proposed is not None and num(proposed) != num(cell.final_text):
            rows.add(cell.row)
    return sorted(rows)


def apply_agreement(page, template, qwen, glm):
    """Never change exclusion, confirmation, confidence, or review flags."""
    changed = 0
    first = template.row_label_columns
    for cell in eligible_cells(page, template):
        qvalues, gvalues = qwen.get(cell.row + 1), glm.get(cell.row)
        if qvalues is None or gvalues is None:
            continue
        rule, _ = template.value_constraints(cell.row, cell.column)
        q = reading(qvalues[cell.column - first], rule)
        g = reading(gvalues[cell.column - first], rule)
        cell.slow_mode_evidence = {'baseline': cell.final_text, 'qwen': q, 'glm': g, 'policy': POLICY}
        if q is None or num(q) != num(g) or num(q) == num(cell.final_text):
            continue
        original = cell.final_text
        cell.final_text = q
        cell.alternatives = ' | '.join(dict.fromkeys(v for v in [original, *cell.alternatives.split(' | '), q] if v))
        cell.flags = sorted(set([*cell.flags, 'slow_mode_selected']))
        changed += 1
    return changed


def prepare_images(image, template, directory):
    first = template.row_label_columns
    if first >= template.columns:
        raise ValueError(tr('В таблице нет столбцов измерений для slow mode.'))
    directory.mkdir(parents=True, exist_ok=True)
    x, y, _, _ = cell_rect(template, image.shape, 0, first, 0)
    xx, yy, w, h = cell_rect(template, image.shape, template.rows - 1, template.columns - 1, 0)
    crop = image[y:yy+h, x:xx+w].copy()
    if not crop.size:
        raise ValueError(tr('Область таблицы пуста.'))
    out = np.full((crop.shape[0], crop.shape[1] + 85, 3), 255, np.uint8)
    out[:, 85:] = crop
    for row in range(template.rows):
        _, ry, _, rh = cell_rect(template, image.shape, row, first, 0)
        cy = max(10, min(out.shape[0] - 2, ry-y+rh//2+7))
        cv2.putText(out, str(row+1), (8, cy), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 0), 2, cv2.LINE_AA)
    path = directory / 'table.png'
    if not cv2.imwrite(str(path), out):
        raise OSError('Could not save slow-mode table crop')
    return {'id': 'table', 'image': str(path), 'rows': template.rows, 'columns': template.columns-first}


def row_record(image, template, row, directory):
    x, y, _, _ = cell_rect(template, image.shape, row, template.row_label_columns, 0)
    xx, yy, w, h = cell_rect(template, image.shape, row, template.columns - 1, 0)
    path = directory / f'row-{row+1}.png'
    if not cv2.imwrite(str(path), image[y:yy+h, x:xx+w]):
        raise OSError('Could not save slow-mode row crop')
    return {'id': row, 'image': str(path)}


def run_model(kind, records, directory, config, progress=None):
    request = directory / f'{kind}-request.json'
    output = directory / f'{kind}-response.json'
    output.unlink(missing_ok=True)
    request.write_text(json.dumps({'kind': kind, 'model': config[kind], 'records': records}))
    env = os.environ.copy()
    for name in list(env):
        if name.startswith(('_PYI', 'DYLD_', 'QT_')) or name in {'PYTHONHOME', 'PYTHONPATH'}:
            env.pop(name, None)
    env.update(HF_HUB_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    runner = Path(__file__).with_name('slow_runner.py')
    started = time.monotonic()
    timeout = max(600, len(records) * 45)
    with (directory / f'{kind}.log').open('w') as log:
        process = subprocess.Popen([config['python'], str(runner), str(request), str(output)],
                                   stdout=log, stderr=log, env=env)
        try:
            while process.poll() is None:
                if progress:
                    count = 0
                    try:
                        count = len(json.loads(output.read_text()))
                    except (OSError, ValueError):
                        pass
                    progress(count, len(records), tr('Slow mode: {p0}, выполнено {p1} из {p2}',
                             p0='Qwen' if kind == 'qwen' else 'GLM', p1=count, p2=len(records)))
                if time.monotonic() - started > timeout:
                    raise TimeoutError(tr('Slow mode: превышено время ожидания модели.'))
                time.sleep(.2)
            if process.returncode:
                raise RuntimeError(tr('Slow mode: модель {p0} завершилась с ошибкой.', p0=kind))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    result = json.loads(output.read_text())
    if len(result) != len(records) or [r['id'] for r in result] != [r['id'] for r in records]:
        raise ValueError('Incomplete slow-mode response')
    return result


def refine_page(image, page, template, directory, config, progress=None):
    page.slow_mode = {'policy': POLICY, 'status': 'running', 'models': MODELS}
    if not eligible_cells(page, template):
        page.slow_mode.update(status='complete', changed=0, rows=0)
        return
    try:
        table = prepare_images(image, template, directory)
        result = run_model('qwen', [table], directory, config, progress)
        qwen = parse_table(result[0]['raw'], template.rows, table['columns'])
        if not qwen:
            raise ValueError(tr('Slow mode: не удалось сопоставить ответ модели со строками таблицы.'))
        rows = disputed_rows(page, template, qwen)
        glm = {}
        if rows:
            records = [row_record(image, template, r, directory) for r in rows]
            results = run_model('glm', records, directory, config, progress)
            glm = {r['id']: row_values(r['raw'], table['columns']) for r in results}
        if progress:
            progress(1, 1, tr('Slow mode: проверка согласия моделей'))
        changed = apply_agreement(page, template, qwen, glm)
        partial = any(cell.row + 1 not in qwen for cell in eligible_cells(page, template)) or any(v is None for v in glm.values())
        page.slow_mode.update(status='partial' if partial else 'complete', changed=changed, rows=len(rows),
                              parsed_qwen_rows=len(qwen), parsed_glm_rows=sum(v is not None for v in glm.values()))
    except InterruptedError:
        raise
    except Exception as exc:
        # All inference finishes before applying anything: failure retains OCR.
        page.slow_mode.update(status='failed', error=str(exc), changed=0)
