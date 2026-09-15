"""Conservative Qwen/GLM agreement, isolated from the primary OCR runtime."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from .domain import PageResult, TableTemplate
from .imaging import cell_rect, write_image
from .slow_runtime import MODEL_SETS, runtime_root, default_backend
from .ocr import OcrValue, constrain_reading
from .slow_parsing import num, parse_table, row_values, unwrap
from .i18n import tr

MODELS = MODEL_SETS['mlx']  # Compatibility for earlier audit consumers.
POLICY = 'disputed-qwen-glm-v2-cell-recovery-exclusion-guard'


def runtime_config() -> dict:
    path = runtime_root() / 'runtime.json'
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(config, dict) or config.get('schema', 1) not in {1, 2}:
            raise ValueError('runtime schema')
        backend = config.setdefault('backend', 'mlx')
        if backend not in MODEL_SETS or config.get('device', 'auto') not in {'auto', 'cpu', 'cuda'}:
            raise ValueError('backend/device')
        if backend == 'mlx' and default_backend() != 'mlx':
            raise ValueError('MLX platform')
        config.setdefault('device', 'auto')
        if config.get('cpu_dtype', 'bfloat16') not in {'bfloat16', 'float32'}:
            raise ValueError('cpu dtype')
        if not Path(config['python']).is_file():
            raise ValueError('python')
        for key, (repo, revision) in MODEL_SETS[backend].items():
            folder = Path(config[key])
            metadata = json.loads((folder / 'audit-revision.json').read_text(encoding='utf-8'))
            if metadata != {'repo': repo, 'revision': revision}:
                raise ValueError('revision')
            index = folder / 'model.safetensors.index.json'
            weights = set(json.loads(index.read_text(encoding="utf-8"))['weight_map'].values()) if index.exists() else {'model.safetensors'}
            if not weights or not all((folder / name).is_file() and (folder / name).stat().st_size > 0 for name in weights):
                raise ValueError('weights')
        return config
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(tr('Модуль slow mode не установлен или его файлы недоступны. Обычное распознавание доступно.')) from exc


def eligible_cells(page: PageResult, template: TableTemplate):
    return [cell for cell in page.cells
            if cell.needs_review and cell.row not in page.excluded_rows
            and cell.row >= template.header_rows and cell.column >= template.row_label_columns
            and template.column_rules[cell.column].role != 'ignored'
            and template.value_constraints(cell.row, cell.column)[0].value_format in {'numeric', 'integer'}]


def reading(value, rule, *, check_rules=True):
    if isinstance(value, (dict, list, bool)):
        return None
    raw = '' if value is None else unwrap(str(value))
    result = constrain_reading(OcrValue(raw, 0.0), rule).text
    return result if num(result) is not None and (not check_rules or not rule.hard_errors(result)) else None


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
    write_image(path, out)
    return {'id': 'table', 'image': str(path), 'rows': template.rows, 'columns': template.columns-first}


def row_record(image, template, row, directory):
    x, y, _, _ = cell_rect(template, image.shape, row, template.row_label_columns, 0)
    xx, yy, w, h = cell_rect(template, image.shape, row, template.columns - 1, 0)
    path = directory / f'row-{row+1}.png'
    write_image(path, image[y:yy+h, x:xx+w])
    return {'id': row, 'image': str(path)}


def recover_cells(kind, image, template, cells, directory, config, progress=None):
    """Read exact cell crops once; never pad, truncate or shift a model's row."""
    if not cells:
        return {}, {}
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    for cell in cells:
        x, y, w, h = cell_rect(template, image.shape, cell.row, cell.column, 0)
        identifier = f'{cell.row}:{cell.column}'
        path = directory / f'cell-{cell.row}-{cell.column}.png'
        write_image(path, image[y:y+h, x:x+w])
        records.append({'id': identifier, 'image': str(path), 'rows': 1, 'columns': 1, 'cell': True})
    results = run_model(kind, records, directory, config, progress)
    return {r['id']: row_values(r['raw'], 1) for r in results}, {r['id']: r.get('execution', {}) for r in results}


def start_worker(command, log, env):
    """Undo PyInstaller's process-wide DLL search override only while spawning."""
    kernel = None
    if sys.platform == 'win32' and getattr(sys, 'frozen', False):
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.SetDllDirectoryW(None)
    try:
        return subprocess.Popen(command, stdout=log, stderr=log, env=env,
                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    finally:
        if kernel is not None:
            kernel.SetDllDirectoryW(getattr(sys, '_MEIPASS', None))


def run_model(kind, records, directory, config, progress=None):
    request = directory / f'{kind}-request.json'
    output = directory / f'{kind}-response.json'
    output.unlink(missing_ok=True)
    status = directory / f'{kind}-status.json'
    status.unlink(missing_ok=True)
    request.write_text(json.dumps({'kind': kind, 'model': config[kind], 'records': records,
                                   'backend': config.get('backend', 'mlx'),
                                   'device': config.get('device', 'auto'),
                                   'cpu_dtype': config.get('cpu_dtype', 'bfloat16'), 'status': str(status)}), encoding='utf-8')
    env = os.environ.copy()
    for name in list(env):
        if name.startswith(('_PYI', 'DYLD_', 'QT_')) or name in {'PYTHONHOME', 'PYTHONPATH'}:
            env.pop(name, None)
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    # Frozen Windows apps prepend bundled DLL paths. External Python must use its own.
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        env['PATH'] = os.pathsep.join(p for p in env.get('PATH', '').split(os.pathsep)
                                      if not os.path.normcase(p).startswith(os.path.normcase(str(bundle))))
    runner = Path(__file__).with_name('slow_runner.py')
    started = time.monotonic()
    timeout = (max(7200, len(records) * 600) if config.get('backend') == 'transformers'
               else max(600, len(records) * 45))
    with (directory / f'{kind}.log').open('w', encoding='utf-8') as log:
        process = start_worker([config['python'], str(runner), str(request), str(output)], log, env)
        try:
            while process.poll() is None:
                if progress:
                    count = 0
                    try:
                        count = len(json.loads(output.read_text(encoding='utf-8')))
                    except (OSError, ValueError):
                        pass
                    label = 'Qwen' if kind == 'qwen' else 'GLM'
                    tokens = 0
                    try:
                        state = json.loads(status.read_text(encoding='utf-8'))
                        device = state.get('device')
                        if isinstance(state.get('tokens'), int):
                            tokens = state['tokens']
                        if device in {'cpu', 'cuda', 'metal'}:
                            label += f' ({device.upper()})'
                    except (OSError, ValueError, AttributeError):
                        pass
                    message = tr('Slow mode: {p0}, выполнено {p1} из {p2}',
                                 p0=label, p1=count, p2=len(records))
                    if tokens:
                        message += ' · ' + tr('Сгенерировано токенов: {p0}', p0=tokens)
                    progress(count, len(records), message)
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
    result = json.loads(output.read_text(encoding='utf-8'))
    if len(result) != len(records) or [r['id'] for r in result] != [r['id'] for r in records]:
        raise ValueError('Incomplete slow-mode response')
    return result


def refine_page(image, page, template, directory, config, progress=None):
    page.slow_mode = {'policy': POLICY, 'status': 'running', 'models': MODEL_SETS[config.get('backend', 'mlx')],
                      'backend': config.get('backend', 'mlx'), 'requested_device': config.get('device', 'auto')}
    if not eligible_cells(page, template):
        page.slow_mode.update(status='complete', changed=0, rows=0)
        return
    try:
        table = prepare_images(image, template, directory)
        result = run_model('qwen', [table], directory, config, progress)
        page.slow_mode['execution'] = {'qwen': result[0].get('execution', {})}
        qwen = parse_table(result[0]['raw'], template.rows, table['columns'])
        first = template.row_label_columns
        eligible = eligible_cells(page, template)
        missing = [cell for cell in eligible if cell.row + 1 not in qwen or
                   reading(qwen[cell.row + 1][cell.column - first], template.value_constraints(cell.row, cell.column)[0]) is None]
        page.slow_mode['recovery'] = {'qwen_requested_cells': len(missing), 'glm_requested_cells': 0}
        recovery_errors = []
        try:
            recovered, execution = recover_cells('qwen', image, template, missing, directory / 'qwen-cells', config, progress)
            page.slow_mode['execution']['qwen_cells'] = execution
            for cell in missing:
                values = recovered.get(f'{cell.row}:{cell.column}')
                if values is not None:
                    qwen.setdefault(cell.row + 1, [None] * table['columns'])[cell.column - first] = values[0]
        except InterruptedError:
            raise
        except Exception as exc:
            recovery_errors.append(str(exc))
        rows = disputed_rows(page, template, qwen)
        glm = {}
        if rows:
            records = [row_record(image, template, r, directory) for r in rows]
            results = run_model('glm', records, directory, config, progress)
            page.slow_mode['execution']['glm'] = [r.get('execution', {}) for r in results]
            glm = {r['id']: row_values(r['raw'], table['columns']) for r in results}
        # A failed row has no trustworthy positions. Retry only cells whose
        # Qwen reading needs an independent GLM check.
        missing = [cell for cell in eligible if cell.row in rows
                   and (glm.get(cell.row) is None or reading(glm[cell.row][cell.column - first], template.value_constraints(cell.row, cell.column)[0]) is None)
                   and (proposed := reading(qwen[cell.row + 1][cell.column - first], template.value_constraints(cell.row, cell.column)[0])) is not None
                   and num(proposed) != num(cell.final_text)]
        page.slow_mode['recovery']['glm_requested_cells'] = len(missing)
        try:
            recovered, execution = recover_cells('glm', image, template, missing, directory / 'glm-cells', config, progress)
            page.slow_mode['execution']['glm_cells'] = execution
            for cell in missing:
                values = recovered.get(f'{cell.row}:{cell.column}')
                if values is not None:
                    if glm.get(cell.row) is None:
                        glm[cell.row] = [None] * table['columns']
                    glm[cell.row][cell.column - first] = values[0]
        except InterruptedError:
            raise
        except Exception as exc:
            recovery_errors.append(str(exc))
        if progress:
            progress(1, 1, tr('Slow mode: проверка согласия моделей'))
        rule_rejections = []
        missing_qwen = set()
        missing_glm = set()
        for cell in eligible:
            rule, _ = template.value_constraints(cell.row, cell.column)
            values = qwen.get(cell.row + 1)
            q = reading(values[cell.column - first], rule) if values is not None else None
            if q is None:
                missing_qwen.add(cell.row)
                if values is not None and reading(values[cell.column - first], rule, check_rules=False) is not None:
                    rule_rejections.append({'row': cell.row, 'column': cell.column, 'model': 'qwen'})
            elif num(q) != num(cell.final_text):
                values = glm.get(cell.row)
                if values is None or reading(values[cell.column - first], rule) is None:
                    missing_glm.add(cell.row)
                    if values is not None and reading(values[cell.column - first], rule, check_rules=False) is not None:
                        rule_rejections.append({'row': cell.row, 'column': cell.column, 'model': 'glm'})
        partial = bool(missing_qwen or missing_glm)
        page.slow_mode.update(missing_qwen_rows=sorted(missing_qwen), missing_glm_rows=sorted(missing_glm),
                              recovery_errors=recovery_errors, rule_rejections=rule_rejections)
        changed = apply_agreement(page, template, qwen, glm)
        page.slow_mode.update(status='partial' if partial else 'complete', changed=changed, rows=len(rows),
                              parsed_qwen_rows=len(qwen), parsed_glm_rows=len(rows) - len(missing_glm))
    except InterruptedError:
        raise
    except Exception as exc:
        # All inference finishes before applying anything: failure retains OCR.
        page.slow_mode.update(status='failed', error=str(exc), changed=0)


def completion_details(pages):
    """Human-readable diagnostics from saved structured inference evidence."""
    messages = []
    for index, state in enumerate(pages, 1):
        if state.get('status') not in {'partial', 'failed'}:
            continue
        if state.get('status') == 'partial' and 'missing_qwen_rows' in state:
            messages.append(tr('Page {page}: slow verification incomplete after cell retries; Qwen has unreadable or rule-rejected values in {qwen} rows, GLM in {glm} rows. Unverified values retain primary OCR.',
                               page=index, qwen=len(state['missing_qwen_rows']), glm=len(state.get('missing_glm_rows', []))))
        elif state.get('status') == 'partial':
            messages.append(tr('Page {page}: slow verification incomplete; Qwen matched {qwen} rows; GLM matched {glm} of {requested} requested rows. Primary OCR is preserved.',
                               page=index, qwen=state.get('parsed_qwen_rows', 0),
                               glm=state.get('parsed_glm_rows', 0), requested=state.get('rows', 0)))
        else:
            from .i18n import localized_saved_message
            messages.append(tr('Page {page}: slow verification failed: {reason} Primary OCR is preserved.',
                               page=index, reason=localized_saved_message(state.get('error', ''))))
        if state.get('rule_rejections'):
            messages.append(tr('Protocol rules rejected {p0} model readings. Check the value ranges and formats for this file.',
                               p0=len(state['rule_rejections'])))
    from .i18n import join_text
    return join_text('\n', messages)
