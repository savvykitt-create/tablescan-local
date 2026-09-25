"""Local ONNX acceleration with a permanent CPU fallback per model session."""
from __future__ import annotations

import logging
import os
import sys

import onnxruntime as ort

logger = logging.getLogger(__name__)
CPU = "CPUExecutionProvider"


def acceleration_provider():
    """Availability means the runtime includes the EP; loading still may fail."""
    if os.environ.get("TABLESCAN_OCR_DEVICE", "auto").lower() == "cpu":
        return None
    available = ort.get_available_providers()
    # CoreML is deliberately excluded for the shipped dynamic-shape models:
    # NeuralNetwork changed recognized digits in local parity checks; MLProgram
    # failed to compile PP-OCRv5 Server and aborted natively during teardown.
    # A native abort cannot be recovered by the Python CPU fallback below.
    if "CUDAExecutionProvider" in available:
        return ("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"})
    if sys.platform == "win32" and "DmlExecutionProvider" in available:
        return ("DmlExecutionProvider", {"device_id": "0"})
    return None


class AutoSession:
    """Preserve ORT's session API and retry a failed accelerated run once on CPU.

    Acceleration is initialized on first inference so unused RapidOCR detection
    and orientation models do not incur GPU compilation or allocation costs.
    """

    def __init__(self, model_path, options=None, *, cpu_session=None):
        self.model_path = str(model_path)
        self.options = options if options is not None else ort.SessionOptions()
        self.provider = acceleration_provider()
        self._pending = self.provider is not None
        self._accelerated = False
        self._session = cpu_session if cpu_session is not None else self._cpu()

    def _cpu(self):
        return ort.InferenceSession(self.model_path, sess_options=self.options, providers=[CPU])

    def __getattr__(self, name):
        return getattr(self._session, name)

    def run(self, output_names, input_feed, *args, **kwargs):
        if self._pending:
            self._pending = False
            if self.provider[0] == "DmlExecutionProvider":
                self.options.enable_mem_pattern = False
                self.options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            try:
                self._session = ort.InferenceSession(
                    self.model_path, sess_options=self.options, providers=[self.provider, CPU])
                self._accelerated = self.provider[0] in self._session.get_providers()
                logger.info("OCR %s: %s", self.model_path, self._session.get_providers())
            except Exception:
                # The original CPU session remains usable if GPU setup fails.
                logger.warning("OCR accelerator initialization failed; using CPU: %s", self.model_path, exc_info=True)
        try:
            return self._session.run(output_names, input_feed, *args, **kwargs)
        except Exception:
            if not self._accelerated:
                raise
            logger.warning("OCR accelerator inference failed; retrying on CPU: %s", self.model_path, exc_info=True)
        # Leave the exception scope so its traceback cannot retain GPU resources.
        self._accelerated = False
        self._session = None
        self._session = self._cpu()
        return self._session.run(output_names, input_feed, *args, **kwargs)


def accelerate_rapidocr(engine):
    """Adapt RapidOCR 1.x sessions without patching library globals.

    The adapter adds consistent lazy initialization and inference-time fallback
    to all three components. _model_path is the one private ORT field used here.
    """
    for adapter in (engine.text_det.infer, engine.text_cls.infer, engine.text_rec.session):
        native = adapter.session
        adapter.session = AutoSession(
            native._model_path, native.get_session_options(), cpu_session=native)
    return engine
