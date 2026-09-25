from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tablescan_local import ocr_runtime as runtime


def native(providers, error=None):
    return SimpleNamespace(get_providers=lambda: providers,
                           run=Mock(side_effect=error, return_value=['result']))


@pytest.mark.parametrize('platform,available,expected', [
    ('darwin', ['CoreMLExecutionProvider', runtime.CPU], None),
    ('win32', ['DmlExecutionProvider', runtime.CPU], 'DmlExecutionProvider'),
    ('linux', ['CUDAExecutionProvider', runtime.CPU], 'CUDAExecutionProvider'),
    ('linux', ['AzureExecutionProvider', runtime.CPU], None),
    ('linux', ['DmlExecutionProvider', runtime.CPU], None),
])
def test_provider_selection(monkeypatch, platform, available, expected):
    monkeypatch.delenv('TABLESCAN_OCR_DEVICE', raising=False)
    monkeypatch.setattr(runtime.sys, 'platform', platform)
    monkeypatch.setattr(runtime.ort, 'get_available_providers', lambda: available)
    provider = runtime.acceleration_provider()
    assert (provider[0] if provider else None) == expected


def test_cpu_override(monkeypatch):
    monkeypatch.setenv('TABLESCAN_OCR_DEVICE', 'cpu')
    monkeypatch.setattr(runtime.ort, 'get_available_providers', lambda: ['CUDAExecutionProvider'])
    assert runtime.acceleration_provider() is None


def test_initialization_failure_keeps_cpu_and_does_not_retry_gpu(monkeypatch):
    monkeypatch.setattr(runtime, 'acceleration_provider', lambda: ('CUDAExecutionProvider', {}))
    cpu = native([runtime.CPU])
    factory = Mock(side_effect=RuntimeError('driver missing'))
    monkeypatch.setattr(runtime.ort, 'InferenceSession', factory)
    session = runtime.AutoSession('model.onnx', cpu_session=cpu)
    factory.assert_not_called()
    assert session.run(None, {}) == ['result']
    assert session.run(None, {}) == ['result']
    assert factory.call_count == 1
    assert cpu.run.call_count == 2


def test_inference_failure_replays_same_input_on_cpu_permanently(monkeypatch):
    monkeypatch.setattr(runtime, 'acceleration_provider', lambda: ('CUDAExecutionProvider', {}))
    gpu = native(['CUDAExecutionProvider', runtime.CPU], RuntimeError('out of memory'))
    cpu = native([runtime.CPU])
    factory = Mock(side_effect=[gpu, cpu])
    monkeypatch.setattr(runtime.ort, 'InferenceSession', factory)
    session = runtime.AutoSession('model.onnx', cpu_session=native([runtime.CPU]))
    inputs = {'pixels': object()}
    assert session.run(['output'], inputs) == ['result']
    assert session.run(['output'], inputs) == ['result']
    gpu.run.assert_called_once_with(['output'], inputs)
    assert cpu.run.call_count == 2
    assert cpu.run.call_args.args == (['output'], inputs)
    assert factory.call_args.kwargs['providers'] == [runtime.CPU]


def test_cpu_failure_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(runtime, 'acceleration_provider', lambda: None)
    session = runtime.AutoSession('model.onnx', cpu_session=native([runtime.CPU], ValueError('bad input')))
    with pytest.raises(ValueError, match='bad input'):
        session.run(None, {})


def test_directml_uses_required_options(monkeypatch):
    monkeypatch.setattr(runtime, 'acceleration_provider', lambda: ('DmlExecutionProvider', {}))
    factory = Mock(return_value=native(['DmlExecutionProvider', runtime.CPU]))
    monkeypatch.setattr(runtime.ort, 'InferenceSession', factory)
    session = runtime.AutoSession('model.onnx', cpu_session=native([runtime.CPU]))
    session.run(None, {})
    options = factory.call_args.kwargs['sess_options']
    assert options.enable_mem_pattern is False
    assert options.execution_mode == runtime.ort.ExecutionMode.ORT_SEQUENTIAL


def test_real_rapidocr_adapter_and_digit_cpu_inference(monkeypatch):
    import numpy as np
    from tablescan_local.ocr import LocalOcrEngine
    monkeypatch.setenv('TABLESCAN_OCR_DEVICE', 'cpu')
    engine = LocalOcrEngine(high_accuracy=True)
    for ocr in (engine._engine, engine._numeric_check, engine._precision_engine):
        for adapter in (ocr.text_det.infer, ocr.text_cls.infer, ocr.text_rec.session):
            assert isinstance(adapter.session, runtime.AutoSession)
        image = np.full((48, 96, 3), 255, dtype=np.uint8)
        engine._recognize_direct(image, ocr)
    probabilities = engine._digit_verifier._probabilities([np.zeros((28, 28), dtype=np.uint8)])
    assert probabilities.shape == (1, 10)
    assert np.isfinite(probabilities).all()
