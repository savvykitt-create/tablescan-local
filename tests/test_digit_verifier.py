import cv2
import numpy as np

from tablescan_local.digit_verifier import DigitVerifier, hog_features, infer_numeric_geometry, segment_digits
from tablescan_local.ocr import detect_decimal_separator


def numeric_crop() -> np.ndarray:
    crop = np.full((60, 180, 3), 255, np.uint8)
    cv2.putText(crop, "47", (10, 46), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 2)
    cv2.circle(crop, (100, 47), 2, (0, 0, 0), -1)
    cv2.putText(crop, "2", (120, 46), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 2)
    return crop


def test_hog_shape_and_blank_stability():
    values = hog_features(np.zeros((2, 28, 28), np.uint8))
    assert values.shape == (2, 324)
    assert np.all(np.isfinite(values))


def test_candidate_driven_segmentation_needs_visible_separator():
    crop = numeric_crop()
    separator = detect_decimal_separator(crop)
    assert separator is not None
    assert segment_digits(crop, "47.2", separator.x, separator.width) is not None
    assert len(segment_digits(crop, "47.2", separator.x, separator.width)) == 3
    assert segment_digits(crop, "47.2", None) is None


def test_numeric_geometry_counts_visible_glyphs_when_comma_touches_integer():
    crop = np.full((60, 180, 3), 255, np.uint8)
    cv2.putText(crop, "24", (12, 46), cv2.FONT_HERSHEY_SIMPLEX, 1.35, (0, 0, 0), 2)
    cv2.line(crop, (91, 39), (88, 54), (0, 0, 0), 3)
    cv2.putText(crop, "8", (116, 46), cv2.FONT_HERSHEY_SIMPLEX, 1.35, (0, 0, 0), 2)
    geometry = infer_numeric_geometry(crop, 1)
    assert geometry is not None
    assert geometry.digit_count == 3
    assert geometry.separator_x is not None


def test_packaged_digit_model_loads_and_scores_candidates():
    verifier = DigitVerifier()
    crop = numeric_crop()
    separator = detect_decimal_separator(crop)
    assert separator is not None
    results = verifier.verify(crop, ["47.2", "97.2"], separator.x, separator.width)
    assert verifier.validation_accuracy > .99
    assert {result.text for result in results} == {"47.2", "97.2"}
    assert all(0 <= result.support <= 1 for result in results)
