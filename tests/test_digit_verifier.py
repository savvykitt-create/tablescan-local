import cv2
import numpy as np
import pytest

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


@pytest.mark.parametrize("reverse", [False, True])
def test_separated_unequal_width_digits_keep_their_complete_strokes(reverse):
    # A narrow vertical glyph and a much wider closed glyph, with a clear
    # gap far away from the equal-width midpoint. Identity is irrelevant:
    # segmentation must preserve exactly the pixels of each isolated glyph.
    narrow = np.full((50, 7, 3), 255, np.uint8)
    wide = np.full((50, 44, 3), 255, np.uint8)
    cv2.line(narrow, (3, 8), (3, 42), (0, 0, 0), 2)
    cv2.rectangle(wide, (2, 8), (41, 42), (0, 0, 0), 2)
    originals = [wide, narrow] if reverse else [narrow, wide]
    crop = np.concatenate([originals[0], np.full((50, 5, 3), 255, np.uint8), originals[1]], axis=1)
    segmented = segment_digits(crop, "12")
    assert segmented is not None and len(segmented) == 2
    for actual, original in zip(segmented, originals, strict=True):
        np.testing.assert_array_equal(actual, segment_digits(original, "1")[0])


def test_detached_small_mark_is_not_used_as_a_complete_digit_group():
    from tablescan_local.digit_verifier import _split_part
    mask = np.zeros((50, 80), np.uint8)
    cv2.rectangle(mask, (10, 8), (50, 42), 1, 2)
    cv2.circle(mask, (65, 41), 1, 1, -1)
    bounds = _split_part(mask, 0, 80, 2)
    assert bounds is not None
    # The one-pixel mark must not enable the whole-glyph whitespace path.
    assert bounds != [(9, 52), (64, 67)]
