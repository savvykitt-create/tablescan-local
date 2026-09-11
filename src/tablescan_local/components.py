"""Explicit 8-connected labelling without OpenCV's default Spaghetti path.

The bundled macOS OpenCV build can segfault in LabelingBolelli (Spaghetti)
on OCR masks. SAUF computes the same connectivity with deterministic ordering.
A native segmentation fault cannot be handled by a Python try/except.
"""
import cv2
import numpy as np


def connected_components(mask: np.ndarray):
    if mask.ndim != 2:
        raise ValueError("Connected-component input must be a two-dimensional mask")
    binary = np.ascontiguousarray(mask != 0, dtype=np.uint8)
    if binary.size == 0:
        return (1, np.zeros(binary.shape, np.int32), np.zeros((1, 5), np.int32),
                np.full((1, 2), np.nan, np.float64))
    return cv2.connectedComponentsWithStatsWithAlgorithm(binary, 8, cv2.CV_32S, cv2.CCL_SAUF)
