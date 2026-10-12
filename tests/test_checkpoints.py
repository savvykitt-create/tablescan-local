import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from tablescan_local import checkpoints, pipeline, slow_mode
from tablescan_local.documents import open_document
from tablescan_local.domain import CellResult, JobResult, NormalizedRect, PageResult, TableTemplate


@pytest.fixture
def analysis(tmp_path, monkeypatch):
    source = tmp_path / 'pages.tiff'
    frames = [Image.new('RGB', (100, 100), (230 + i, 240, 250)) for i in range(3)]
    frames[0].save(source, save_all=True, append_images=frames[1:])
    template = TableTemplate('t', 'Checkpoint test', NormalizedRect(0, 0, 1, 1),
                             [0, 1], [0, 1], header_rows=0, row_label_columns=0)
    template.ensure_column_rules()
    template.column_rules[0].value_format = 'numeric'
    state = dict(primary=[], slow=[], engines=0, fail_primary=None, fail_slow=None,
                 runtime='v1', value='7.0')
    class Engine:
        model_version = 'test-model'
        def __init__(self, **options):
            state['engines'] += 1
    monkeypatch.setattr(pipeline, 'LocalOcrEngine', Engine)
    monkeypatch.setattr(checkpoints, 'recognition_identity', lambda high, slow, cancelled:
                        dict(runtime=state['runtime'], high=high, slow=slow))
    monkeypatch.setattr(slow_mode, 'runtime_config', lambda: {})

    def primary(image, source_path, index, template, engine, progress, directory, **options):
        state['primary'].append(index)
        if state['fail_primary'] == index:
            raise InterruptedError()
        directory.mkdir(parents=True, exist_ok=True)
        crop = directory / 'cell.png'
        Image.fromarray(image).save(crop)
        cell = CellResult(0, 0, state['value'], state['value'], .5,
                          crop_path=str(crop), flags=['low_confidence'])
        return PageResult(index, source_path, [cell], [])

    def refine(image, page, template, directory, config, progress):
        state['slow'].append(page.page_index)
        if state['fail_slow'] == page.page_index:
            page.cells[0].final_text = 'unfinished mutation'
            raise InterruptedError()
        page.cells[0].final_text = '8.0'
        page.slow_mode = dict(status='complete', changed=1)

    monkeypatch.setattr(pipeline, 'process_page', primary)
    monkeypatch.setattr(slow_mode, 'refine_page', refine)

    def run(**options):
        defaults = dict(crop_root=tmp_path / 'crops', checkpoint_directory=tmp_path / 'checkpoints',
                        run_id='run-a', high_accuracy=True, slow_mode=False)
        defaults.update(options)
        return pipeline.process_document(open_document(source, cache_pages=1), str(source), template, **defaults)
    return state, run, source, template, tmp_path


def test_primary_resume_skips_completed_pages_and_matches_uninterrupted_result(analysis):
    state, run, source, template, root = analysis
    state['fail_primary'] = 1
    with pytest.raises(InterruptedError):
        run()
    assert state['primary'] == [0, 1]
    state['fail_primary'] = None
    result = run()
    assert state['primary'] == [0, 1, 1, 2]
    assert result.performance['checkpoint']['primary_pages'] == [1]
    assert result.performance['primary_pages_seconds'][0] == 0
    expected = run(checkpoint_directory=None)
    assert [p.to_dict() for p in result.pages] == [p.to_dict() for p in expected.pages]


def test_resume_mid_slow_does_not_reload_primary_models_or_apply_partial_mutations(analysis):
    state, run, source, template, root = analysis
    state['fail_slow'] = 1
    with pytest.raises(InterruptedError):
        run(slow_mode=True)
    assert state['primary'] == [0, 1, 2] and state['slow'] == [0, 1]
    assert state['engines'] == 1
    state['fail_slow'] = None
    result = run(slow_mode=True)
    assert state['primary'] == [0, 1, 2] and state['slow'] == [0, 1, 1, 2]
    assert state['engines'] == 1
    assert [p.cells[0].final_text for p in result.pages] == ['8.0'] * 3
    assert result.performance['checkpoint']['primary_pages'] == [1, 2, 3]
    assert result.performance['checkpoint']['slow_pages'] == [1]


@pytest.mark.parametrize('change', ['run', 'source', 'rules', 'grid', 'exclusions', 'mode', 'runtime', 'rotation', 'adaptive'])
def test_changed_inputs_cannot_reuse_old_page_results(analysis, change):
    state, run, source, template, root = analysis
    run()
    options = {}
    if change == 'adaptive':
        options['adaptive'] = True
    elif change == 'run':
        options['run_id'] = 'run-b'
    elif change == 'source':
        frames = [Image.new('RGB', (100, 100), 'white') for _ in range(3)]
        frames[0].save(source, save_all=True, append_images=frames[1:])
    elif change == 'rules':
        template.column_rules[0].maximum = 10
    elif change == 'grid':
        template.row_guides[0] = .01
    elif change == 'exclusions':
        template.excluded_rows = [0]
    elif change == 'mode':
        options['high_accuracy'] = False
    elif change == 'runtime':
        state['runtime'] = 'new model'
    elif change == 'rotation':
        template.rotation_degrees = 180
    result = run(**options)
    assert state['primary'] == [0, 1, 2, 0, 1, 2]
    assert result.performance['checkpoint']['primary_pages'] == []


def test_corrupt_checkpoint_recomputes_only_affected_page(analysis):
    state, run, source, template, root = analysis
    run()
    (root / 'checkpoints/primary-2.json').write_text('{broken')
    result = run()
    assert state['primary'] == [0, 1, 2, 1]
    assert result.performance['checkpoint']['primary_pages'] == [1, 3]


def test_changed_primary_baseline_invalidates_its_slow_result(analysis):
    state, run, source, template, root = analysis
    run(slow_mode=True)
    (root / 'checkpoints/primary-2.json').unlink()
    state['value'] = '9.0'
    result = run(slow_mode=True)
    assert state['primary'] == [0, 1, 2, 1]
    assert state['slow'] == [0, 1, 2, 1]
    assert result.performance['checkpoint']['slow_pages'] == [1, 3]


def test_missing_crops_and_tampered_page_are_not_reused(analysis):
    state, run, source, template, root = analysis
    run()
    (root / 'crops/page-1/cell.png').unlink()
    path = root / 'checkpoints/primary-3.json'
    data = json.loads(path.read_text())
    data['page']['cells'][0]['final_text'] = 'tampered'
    path.write_text(json.dumps(data))
    result = run()
    assert state['primary'] == [0, 1, 2, 0, 2]
    assert result.performance['checkpoint']['primary_pages'] == [2]


def test_cancelled_resume_does_no_new_work(analysis):
    state, run, *_ = analysis
    run()
    with pytest.raises(InterruptedError):
        run(cancelled=lambda: True)
    assert state['primary'] == [0, 1, 2]


def test_progress_spans_document_and_resume_finishes_at_total(analysis):
    _, run, *_ = analysis
    run(slow_mode=True)
    progress = []
    run(slow_mode=True, progress=lambda value, maximum, label: progress.append((value, maximum)))
    assert progress[-1] == (6000, 6000)
    assert [v for v, m in progress] == sorted(v for v, m in progress)


def test_atomic_write_failure_preserves_last_good_checkpoint(tmp_path, monkeypatch):
    path = tmp_path / 'checkpoint.json'
    checkpoints.atomic_json(path, {'old': True})
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(checkpoints.os, 'replace', fail)
    with pytest.raises(OSError, match='disk full'):
        checkpoints.atomic_json(path, {'new': True})
    assert json.loads(path.read_text()) == {'old': True}
    assert list(tmp_path.iterdir()) == [path]


def test_changed_file_invalidates_cached_content_digest(tmp_path):
    path = tmp_path / 'weights.bin'
    path.write_bytes(b'123')
    before = checkpoints.file_digest(path)
    path.write_bytes(b'456')
    assert checkpoints.file_digest(path) != before
    with pytest.raises(InterruptedError):
        checkpoints.file_digest(path, lambda: True)


def test_interrupted_analysis_keeps_existing_reviewed_result_unchanged(analysis):
    from tablescan_local.storage import LocalStore
    state, run, source, template, root = analysis
    store = LocalStore(root / 'store')
    job_id, copied = store.import_source(source)
    original = JobResult(str(copied), template, [PageResult(0, str(copied),
                         [CellResult(0, 0, '1', '12.345', .9, status='corrected')], [])])
    store.save_result(job_id, original)
    state['fail_primary'] = 1
    with pytest.raises(InterruptedError):
        run(checkpoint_directory=store.jobs_dir / job_id / 'analysis-checkpoint')
    assert store.load_job(job_id)[1].to_dict() == original.to_dict()


def test_hard_process_exit_resumes_from_durable_page_boundary(tmp_path):
    import subprocess
    import sys
    script = tmp_path / 'crash.py'
    script.write_text('''
import os, sys, json
from pathlib import Path
import numpy as np
from tablescan_local import pipeline, checkpoints
from tablescan_local.domain import TableTemplate, NormalizedRect, CellResult, PageResult
root = Path(sys.argv[1])
template = TableTemplate('t', 't', NormalizedRect(0,0,1,1), [0,1], [0,1], row_label_columns=0)
template.ensure_column_rules()
class Engine:
    model_version = 'crash-test'
    def __init__(self, **kwargs): pass
pipeline.LocalOcrEngine = Engine
checkpoints.recognition_identity = lambda *args: {'model': 'test'}
calls = []
def recognize(image, source, index, *args):
    if index == 1 and sys.argv[2] == 'crash': os._exit(23)
    calls.append(index)
    return PageResult(index, source, [CellResult(0,0,'7','7',.9)], [])
pipeline.process_page = recognize
result = pipeline.process_document([np.zeros((2,2,3),np.uint8)]*3, 'test', template,
                                  checkpoint_directory=root/'checkpoint',run_id='fixed')
print(json.dumps({'calls': calls, 'restored': result.performance['checkpoint']['primary_pages']}))
''')
    first = subprocess.run([sys.executable, str(script), str(tmp_path), 'crash'],
                           capture_output=True, text=True, timeout=30)
    assert first.returncode == 23, first.stderr
    second = subprocess.run([sys.executable, str(script), str(tmp_path), 'resume'],
                            capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(second.stdout) == {'calls': [1, 2], 'restored': [1]}



def test_recent_file_digest_detects_same_timestamp_rewrite(tmp_path, monkeypatch):
    path = tmp_path / 'same-tick.bin'
    path.write_bytes(b'123')
    frozen = path.stat()
    original_stat = Path.stat
    monkeypatch.setattr(Path, 'stat', lambda self, *a, **kw:
                        frozen if self == path else original_stat(self, *a, **kw))
    monkeypatch.setattr(checkpoints.time, 'time_ns', lambda: frozen.st_mtime_ns)
    before = checkpoints.file_digest(path)
    path.write_bytes(b'456')
    assert checkpoints.file_digest(path) != before


def test_unchanged_older_weights_reuse_cached_digest(tmp_path, monkeypatch):
    path = tmp_path / 'stable-weights.bin'
    path.write_bytes(b'123')
    monkeypatch.setattr(checkpoints.time, 'time_ns', lambda: path.stat().st_mtime_ns + 10_000_000_000)
    before = checkpoints.file_digest(path)
    def unexpected_hash():
        raise AssertionError('Unchanged older weights should not be rehashed')
    monkeypatch.setattr(checkpoints.hashlib, 'sha256', unexpected_hash)
    assert checkpoints.file_digest(path) == before
