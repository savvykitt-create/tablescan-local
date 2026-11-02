"""Do not discard visible numeric content before the recognizer can read it."""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from tablescan_local.constraints import ValueConstraints
from tablescan_local.ocr import LocalOcrEngine, OcrValue, cell_mark_kind


FIXTURES = Path(__file__).parent / "fixtures/uos_cell_marks"
CASES = json.loads((FIXTURES / "provenance.json").read_text())


@pytest.fixture(params=CASES, ids=lambda c: f"{c['document_id']}__{c['cell_id']}")
def visible_number(request):
    case = request.param
    path = FIXTURES / f"{case['document_id']}__{case['cell_id']}.png"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == case["crop_sha256"]
    return cv2.imread(str(path)), case["expected_numeric"]


def test_external_numbers_are_content(visible_number):
    crop, _ = visible_number
    assert cell_mark_kind(crop) == "content"


@pytest.mark.parametrize("high_accuracy", [False, True])
def test_external_numbers_reach_requested_recognizer(visible_number, high_accuracy):
    crop, expected = visible_number
    engine = object.__new__(LocalOcrEngine)
    engine._high_accuracy = high_accuracy
    calls = []

    def recognize(source, *args):
        assert source is crop
        calls.append(source)
        return OcrValue(expected, .95)

    def wrong_path(*args):
        pytest.fail("Unexpected recognition mode")

    engine._read_cell = wrong_path if high_accuracy else recognize
    engine._recognize_numeric_slow = recognize if high_accuracy else wrong_path
    result = engine.recognize_cell(crop, numeric=True, constraints=ValueConstraints("numeric"))
    assert len(calls) == 1
    assert result.text == expected


@pytest.mark.parametrize("width", [160, 1600])
@pytest.mark.parametrize("background", [(255, 255, 255), (220, 180, 160)])
def test_borders_and_speckles_remain_empty(width, background):
    crop = np.full((100, width, 3), background, np.uint8)
    cv2.rectangle(crop, (1, 1), (width - 2, 98), (20, 20, 20), 2)
    crop[30, width // 2] = 0
    assert cell_mark_kind(crop) == "empty"


@pytest.mark.parametrize("background", [(255, 255, 255), (220, 180, 160)])
def test_strike_requires_absence_of_other_content(background):
    crop = np.full((110, 160, 3), background, np.uint8)
    cv2.line(crop, (5, 78), (154, 74), (20, 20, 20), 2)
    assert cell_mark_kind(crop) == "strike"
    cv2.putText(crop, "1", (65, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (20, 20, 20), 3)
    assert cell_mark_kind(crop) == "content"


@pytest.mark.parametrize("high_accuracy", [False, True])
def test_lone_strike_still_bypasses_numeric_ocr(high_accuracy):
    crop = np.full((100, 160, 3), 255, np.uint8)
    cv2.line(crop, (5, 60), (154, 50), (20, 20, 20), 3)
    engine = object.__new__(LocalOcrEngine)
    engine._high_accuracy = high_accuracy
    engine._read_cell = engine._recognize_numeric_slow = lambda *args: pytest.fail("Lone strike must bypass OCR")
    result = engine.recognize_cell(crop, numeric=True, constraints=ValueConstraints("numeric"))
    assert result.text == ""
