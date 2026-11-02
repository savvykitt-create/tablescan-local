"""Missing-value dashes must not become digits in Fast or High numeric OCR."""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from tablescan_local.constraints import ValueConstraints
from tablescan_local.ocr import LocalOcrEngine, OcrValue, cell_mark_kind


FIXTURES = Path(__file__).parent / "fixtures/uos_dashes"
CASES = json.loads((FIXTURES / "provenance.json").read_text())


@pytest.fixture(params=CASES, ids=lambda c: c["filename"])
def source_mark(request):
    case = request.param
    path = FIXTURES / case["filename"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == case["crop_sha256"]
    return cv2.imread(str(path)), case["expected_mark"]


def test_source_mark_geometry(source_mark):
    crop, expected = source_mark
    assert cell_mark_kind(crop) == expected


@pytest.mark.parametrize("high_accuracy", [False, True])
def test_real_dashes_bypass_both_numeric_recognizers(source_mark, high_accuracy):
    crop, expected = source_mark
    engine = object.__new__(LocalOcrEngine)
    engine._high_accuracy = high_accuracy
    calls = []

    def recognize(*args):
        calls.append(True)
        return OcrValue("10", .9)

    engine._read_cell = engine._recognize_numeric_slow = recognize
    result = engine.recognize_cell(crop, numeric=True, constraints=ValueConstraints("numeric"))
    assert result.text == ("" if expected == "strike" else "10")
    assert len(calls) == (0 if expected == "strike" else 1)


@pytest.mark.parametrize("text", ["0", "1", "7", "-1", "-0.5", "-12.4", "1-"])
@pytest.mark.parametrize("background", [(255, 255, 255), (220, 180, 160)])
@pytest.mark.parametrize("high_accuracy", [False, True])
def test_signed_and_unsigned_numbers_are_never_suppressed(text, background, high_accuracy):
    crop = np.full((80, 240, 3), background, np.uint8)
    cv2.putText(crop, text, (25, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 2)
    assert cell_mark_kind(crop) == "content"
    engine = object.__new__(LocalOcrEngine)
    engine._high_accuracy = high_accuracy
    engine._read_cell = engine._recognize_numeric_slow = lambda *args: OcrValue("-1", .9)
    assert engine.recognize_cell(crop, numeric=True, constraints=ValueConstraints("numeric")).text == "-1"


@pytest.mark.parametrize("high_accuracy", [False, True])
def test_retry_crop_with_visible_number_vetoes_dash_suppression(high_accuracy):
    dash = np.full((80, 200, 3), 255, np.uint8)
    cv2.line(dash, (70, 55), (95, 55), (0, 0, 0), 2)
    number = dash.copy()
    cv2.putText(number, "1", (110, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    assert cell_mark_kind(dash) == "strike"
    assert cell_mark_kind(number) == "content"
    engine = object.__new__(LocalOcrEngine)
    engine._high_accuracy = high_accuracy
    engine._read_cell = engine._recognize_numeric_slow = lambda *args: OcrValue("-1", .9)
    assert engine.recognize_cell(dash, numeric=True, constraints=ValueConstraints("numeric"), retry_crops=[number]).text == "-1"


def test_text_fields_preserve_a_literal_dash():
    crop = np.full((80, 200, 3), 255, np.uint8)
    cv2.line(crop, (70, 55), (95, 55), (0, 0, 0), 2)
    engine = object.__new__(LocalOcrEngine)
    engine._read_cell = lambda *args: OcrValue("-", .9)
    assert engine.recognize_cell(crop, numeric=False).text == "-"


def test_adaptive_high_also_short_circuits_before_recognition():
    crop = np.full((80, 200, 3), 255, np.uint8)
    cv2.line(crop, (70, 55), (95, 55), (0, 0, 0), 2)
    engine = object.__new__(LocalOcrEngine)
    engine._high_accuracy = True
    assert engine.recognize_cell(crop, numeric=True, constraints=ValueConstraints("numeric"), adaptive=True).text == ""


@pytest.mark.parametrize("width", [80, 240])
@pytest.mark.parametrize("y", [35, 70])
def test_short_dash_inside_a_complete_table_frame(width, y):
    crop = np.full((80, width, 3), 255, np.uint8)
    cv2.rectangle(crop, (1, 1), (width - 2, 78), (0, 0, 0), 1)
    cv2.line(crop, (width // 2 - 10, y), (width // 2 + 10, y), (0, 0, 0), 2)
    assert cell_mark_kind(crop) == "strike"


@pytest.mark.parametrize("high_accuracy", [False, True])
def test_fragmented_horizontal_rule_cannot_become_a_number(high_accuracy):
    crop = np.full((43, 137, 3), 255, np.uint8)
    for left, right in [(0, 40), (50, 54), (112, 114)]:
        crop[-1, left:right+1] = 0
    engine = object.__new__(LocalOcrEngine)
    engine._high_accuracy = high_accuracy
    engine._read_cell = engine._recognize_numeric_slow = lambda *args: pytest.fail("Rule fragments must bypass OCR")
    assert engine.recognize_cell(crop, numeric=True, constraints=ValueConstraints("numeric")).text == ""
