"""Result parity, memory budgets and real child-process lifecycle regressions."""
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tablescan_local import numeric_decoder as decoder
from tablescan_local.constraints import ValueConstraints
from tablescan_local.parallel_ocr import CellWorkers
from tablescan_local.resource_policy import Resources, GIB, cpu_workers, can_keep_model, can_add_model


@pytest.mark.parametrize('available,cores,cells,expected', [
    (2, 10, 200, 1), (4, 10, 200, 1), (7, 10, 200, 5),
    (12, 2, 200, 1), (12, 10, 20, 1), (12, 10, 200, 6),
])
def test_pool_respects_memory_cpu_and_work_size(available, cores, cells, expected):
    assert cpu_workers(Resources(16 * GIB, available * GIB, cores), cells) == expected
    assert cpu_workers(Resources(16 * GIB, available * GIB, cores), cells, accelerated=True) == 1


def test_unknown_hardware_and_memory_pressure_disable_caches_and_parallelism():
    assert cpu_workers(Resources(), 1000) == 1
    assert not can_keep_model(Resources())
    assert not can_keep_model(Resources(16 * GIB, 2 * GIB, 10))
    # 192 GiB host RAM must not mask exhausted CUDA VRAM.
    rich = Resources(192 * GIB, 150 * GIB, 16)
    assert not can_add_model(rich, 8 * GIB, gpu_free=8 * GIB, gpu_required=12 * GIB)
    assert can_add_model(rich, 8 * GIB, gpu_free=18 * GIB, gpu_required=12 * GIB)


@pytest.mark.skipif(decoder._native_search is None, reason='optional compiler unavailable')
@pytest.mark.parametrize('rule', [
    ValueConstraints(value_format='numeric', minimum=0, maximum=100, decimal_places=1),
    ValueConstraints(value_format='integer', minimum=-100, maximum=100),
    ValueConstraints(value_format='numeric'),
    ValueConstraints(value_format='text', allowed_values=['Left', '11', '1.1', '1,1']),
])
def test_native_decoder_matches_reference_including_ties_and_aliases(rule, monkeypatch):
    rng = np.random.default_rng(581)
    chars = ['blank', *'0123456789.,+-−Left', 'X']
    native = decoder._native_search
    for index in range(12):
        probabilities = (np.ones((7, len(chars))) / len(chars) if index == 0 else
                         rng.dirichlet(np.full(len(chars), .2), size=15))
        monkeypatch.setattr(decoder, '_native_search', native)
        actual = decoder.ctc_prefix_beam_search(probabilities, chars, rule, beam_width=64)
        monkeypatch.setattr(decoder, '_native_search', None)
        expected = decoder.ctc_prefix_beam_search(probabilities, chars, rule, beam_width=64)
        assert [asdict(value) for value in actual] == [asdict(value) for value in expected]


def echo_worker(connection, threads):
    connection.send(('ready', None))
    while True:
        number, args = connection.recv()
        time.sleep(args.get('delay', 0))
        if args.get('crash'):
            os._exit(23)
        connection.send(('result', (number, args['value'])))


def wrong_worker(connection, threads):
    connection.send(('ready', None))
    connection.recv()
    connection.send(('result', (999, 'wrong cell')))
    time.sleep(10)


def sleeping_worker(connection, threads):
    time.sleep(10)


def test_out_of_order_completion_keeps_original_cell_addresses(monkeypatch):
    monkeypatch.setattr('tablescan_local.resource_policy.resources', lambda: Resources(16*GIB, 12*GIB, 10))
    pool = CellWorkers(2, target=echo_worker)
    tasks = [(f'cell-{i}', {'value': str(i), 'delay': .15 if i == 0 else 0}) for i in range(6)]
    try:
        assert list(pool.map(tasks, None)) == [(f'cell-{i}', str(i)) for i in range(6)]
    finally:
        pool.close()
    assert not pool.workers


@pytest.mark.parametrize('target', [echo_worker, wrong_worker])
def test_worker_failure_replays_unfinished_cells_only(target, monkeypatch):
    monkeypatch.setattr('tablescan_local.resource_policy.resources', lambda: Resources(16*GIB, 12*GIB, 10))
    pool = CellWorkers(2, target=target)
    replayed = []
    def sequential(**args):
        replayed.append(args['value'])
        return args['value']
    tasks = [(i, {'value': str(i), 'crash': i == 1}) for i in range(4)]
    assert list(pool.map(tasks, SimpleNamespace(recognize_cell=sequential))) == [(i, str(i)) for i in range(4)]
    assert pool.disabled and not pool.workers
    assert replayed == (['1', '2', '3'] if target is echo_worker else ['0', '1', '2', '3'])


@pytest.mark.parametrize('target', [echo_worker, sleeping_worker])
def test_cancellation_reaps_children_even_during_initialization(target):
    pool = CellWorkers(2, target=target)
    def cancel():
        raise InterruptedError
    with pytest.raises(InterruptedError):
        list(pool.map([(0, {'value': 'x', 'delay': 10})], None, cancel))
    assert not pool.workers


def test_model_cache_reuses_weights_but_rebinds_request_and_evicts_under_pressure(tmp_path, monkeypatch):
    from tablescan_local import slow_runner as runner
    state = [Resources(32*GIB, 20*GIB, 8)]
    monkeypatch.setattr(runner, 'resources', lambda: state[0])
    loaded = []
    class Engine:
        def __init__(self, request):
            loaded.append(request['kind'])
            self.execution = {'device': 'metal'}
    monkeypatch.setattr(runner, 'MlxEngine', Engine)
    cache = runner.EngineCache()
    request = {'kind': 'qwen', 'model': str(tmp_path), 'status': 'first'}
    first = cache.acquire(request)
    second = cache.acquire({**request, 'status': 'second'})
    assert first is second and second.request['status'] == 'second'
    assert second.execution['model_reused']
    cache.acquire({**request, 'kind': 'glm'})
    assert len(cache.engines) == 2 and loaded == ['qwen', 'glm']
    state[0] = Resources(16*GIB, 2*GIB, 8)
    cache.trim()
    assert not cache.engines


def test_reusable_subprocess_uses_separate_requests_and_closes_on_cancel(tmp_path, monkeypatch):
    import sys
    from tablescan_local import slow_mode as slow
    # Exercise actual stdin IPC and response polling without ML libraries.
    (tmp_path/'slow_runner.py').write_text('''import json,sys,os,time
from pathlib import Path
for line in sys.stdin:
    command=json.loads(line); request=json.loads(Path(command['request']).read_text())
    out=Path(command['output'])
    if request['records'][0]['id']=='cancel': time.sleep(30)
    out.write_text(json.dumps([{'id': r['id'], 'raw': str(os.getpid())} for r in request['records']]))
    out.with_suffix('.done.json').write_text(json.dumps({'ok': True}))
''')
    monkeypatch.setattr(slow, '__file__', str(tmp_path/'slow_mode.py'))
    config = {'python': sys.executable, 'qwen': 'local'}
    with slow.model_session() as session:
        a = slow.run_model('qwen', [{'id':'first'}], tmp_path, config)
        b = slow.run_model('qwen', [{'id':'second'}], tmp_path, config)
        assert a[0]['raw'] == b[0]['raw'] and b[0]['id'] == 'second'
        process = session.process
        def cancel(*args): raise InterruptedError
        with pytest.raises(InterruptedError):
            slow.run_model('qwen', [{'id':'cancel'}], tmp_path, config, cancel)
    assert process.poll() is not None


def test_performance_metadata_survives_storage_and_legacy_results():
    from tablescan_local.domain import JobResult, TableTemplate, NormalizedRect
    template = TableTemplate('t', 't', NormalizedRect(0,0,1,1), [0,1], [0,1])
    result = JobResult('source', template, [], performance={'cpu_workers':2})
    data = result.to_dict()
    assert JobResult.from_dict(data).performance == {'cpu_workers':2}
    data.pop('performance')
    assert JobResult.from_dict(data).performance == {}


def test_grammar_cache_tracks_rule_edits_and_never_reuses_model_probabilities():
    rule = ValueConstraints(value_format='numeric', minimum=0, maximum=100, decimal_places=1)
    a = decoder.NumericPrefixGrammar(rule)
    a.prefix_cache['12.3'] = a.prefix_allowed('12.3')
    rule.decimal_places = 0
    b = decoder.NumericPrefixGrammar(rule)
    assert a.prefix_cache is not b.prefix_cache
    assert not b.prefix_allowed('12.3')
    chars = ['blank', '1', '2']
    rule = ValueConstraints(value_format='integer')
    first = decoder.ctc_prefix_beam_search(np.array([[.01,.98,.01]]), chars, rule)
    second = decoder.ctc_prefix_beam_search(np.array([[.01,.01,.98]]), chars, rule)
    assert first[0].text == '1' and second[0].text == '2'


def test_existing_weights_are_evicted_before_loading_another_model_without_room(tmp_path, monkeypatch):
    from tablescan_local import slow_runner as runner
    import weakref
    monkeypatch.setattr(runner, 'resources', lambda: Resources(16*GIB, 2*GIB, 10))
    first = []
    class Engine:
        def __init__(self, request):
            if first:
                assert first[0]() is None
            self.execution = {'device': 'metal'}
            first.append(weakref.ref(self))
    monkeypatch.setattr(runner, 'MlxEngine', Engine)
    cache = runner.EngineCache()
    cache.acquire({'kind':'qwen', 'model':str(tmp_path)})
    cache.acquire({'kind':'glm', 'model':str(tmp_path)})
    assert len(cache.engines) == 1


def test_standalone_worker_does_not_write_into_signed_bundle(tmp_path):
    import shutil
    import subprocess
    import sys
    root = Path(__file__).parents[1] / 'src/tablescan_local'
    for name in ('slow_runner.py', 'resource_policy.py'):
        shutil.copyfile(root / name, tmp_path / name)
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'backend': 'invalid'}))
    env = os.environ.copy()
    env.pop('PYTHONDONTWRITEBYTECODE', None)
    result = subprocess.run([sys.executable, str(tmp_path/'slow_runner.py'), str(request), str(tmp_path/'output.json')],
                            env=env, capture_output=True, text=True, timeout=20)
    assert 'Unknown backend' in result.stderr
    assert not (tmp_path/'__pycache__').exists()
