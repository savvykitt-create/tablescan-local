import pytest

from tablescan_local.constraints import ValueConstraints
from tablescan_local.ocr import OcrValue, canonical_numeric, clean_numeric, constrain_reading, detect_decimal_separator, is_complex_number, is_simple_number


@pytest.mark.parametrize(
    "value",
    ["12.5", "-3", "−3", "<0.1", "8.2±0.4", "1.2e−3", "15%", "2-4"],
)
def test_complex_numeric_grammar(value: str) -> None:
    assert is_complex_number(value)


def test_numeric_cleaning_is_restricted() -> None:
    assert clean_numeric(" O.5 mg ") == "O.5mg"
    assert clean_numeric("I37.5") == "I37.5"
    assert clean_numeric("137.5") == "137.5"
    assert clean_numeric("34,4") == "34,4"
    assert is_simple_number("12,5")
    assert not is_complex_number("patient 12")


def test_comma_is_preserved_in_audit_but_canonicalized_for_the_value():
    reading = constrain_reading(OcrValue("34,4", .99), ValueConstraints("numeric", 0, 60, 1, require_decimal=True))
    assert reading.text == "34.4"
    assert reading.raw_text == "34,4"
    assert canonical_numeric("34,4") == "34.4"


def test_decimal_geometry_does_not_invent_dot_in_integer():
    import cv2
    import numpy as np
    from tablescan_local.ocr import decimal_parts
    crop = np.full((64, 160, 3), 255, np.uint8)
    for x in (20, 60, 110):
        cv2.rectangle(crop, (x, 10), (x + 10, 48), (0, 0, 0), -1)
    assert decimal_parts(crop) is None
    cv2.circle(crop, (90, 46), 3, (0, 0, 0), -1)
    assert decimal_parts(crop) is not None


def test_expected_decimal_position_can_reclassify_a_tall_narrow_mark():
    import cv2
    import numpy as np
    crop = np.full((64, 160, 3), 255, np.uint8)
    cv2.rectangle(crop, (20, 10), (35, 50), (0, 0, 0), -1)
    cv2.rectangle(crop, (72, 10), (74, 48), (0, 0, 0), -1)
    cv2.rectangle(crop, (110, 10), (126, 50), (0, 0, 0), -1)
    assert detect_decimal_separator(crop) is None
    evidence = detect_decimal_separator(crop, expected_fraction_digits=1)
    assert evidence is not None and evidence.kind == "one_like"


def test_numeric_consensus_preserves_primary_raw_and_requires_review():
    import cv2
    import numpy as np
    from tablescan_local.ocr import LocalOcrEngine
    engine = object.__new__(LocalOcrEngine)
    engine._engine = object()
    engine._numeric_check = object()
    readings = iter([("I37.5", .99), ("37.5", .95), ("37.5", .96), ("37.5", .96), ("137.5", .99)])
    engine._recognize_direct = lambda *args: next(readings)
    crop = np.full((64, 160, 3), 255, np.uint8)
    cv2.rectangle(crop, (30, 10), (80, 45), (0, 0, 0), -1)
    result = engine.recognize_cell(crop, numeric=True)
    assert result.text == "37.5"
    assert result.raw_text == "I37.5"
    assert "137.5" in result.alternative
    assert "numeric_verification_required" in result.flags
    assert "possible_border_digit" in result.flags


def test_remove_rules_keeps_interior_one():
    import cv2
    import numpy as np
    from tablescan_local.ocr import remove_edge_rules
    crop = np.full((60, 180, 3), 255, np.uint8)
    cv2.line(crop, (1, 0), (1, 59), (0, 0, 0), 2)
    cv2.line(crop, (70, 10), (70, 50), (0, 0, 0), 2)
    cleaned = remove_edge_rules(crop)
    assert cleaned[:, 1].min() == 255
    assert cleaned[30, 70].max() == 0


def test_empty_fragment_does_not_crash_preprocessing():
    import numpy as np
    from tablescan_local.ocr import tight_ink
    assert tight_ink(np.empty((40, 0, 3), np.uint8)).shape == (32, 32, 3)


def test_cell_mark_classifier_separates_empty_strike_and_number():
    import cv2
    import numpy as np
    from tablescan_local.ocr import cell_mark_kind

    empty = np.full((64, 160, 3), 244, np.uint8)
    cv2.line(empty, (0, 1), (159, 1), (20, 20, 20), 2)
    number = np.full_like(empty, 255)
    cv2.putText(number, "13.5", (18, 48), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (180, 60, 20), 3)
    edge_number = np.full_like(empty, 245)
    cv2.putText(edge_number, "4", (139, 24), cv2.FONT_HERSHEY_SIMPLEX, .65, (30, 30, 30), 2)
    cv2.line(edge_number, (0, 61), (159, 61), (185, 185, 185), 2)
    strike = np.full_like(empty, 255)
    cv2.line(strike, (2, 37), (157, 28), (180, 60, 20), 4)

    assert cell_mark_kind(empty) == "empty"
    assert cell_mark_kind(number) == "content"
    assert cell_mark_kind(edge_number) == "content"
    assert cell_mark_kind(strike) == "strike"


def test_cell_mark_classifier_keeps_black_label_on_blue_tinted_scan():
    import cv2
    import numpy as np
    from tablescan_local.ocr import cell_mark_kind

    crop = np.full((49, 169, 3), (252, 229, 221), np.uint8)
    cv2.putText(crop, "3", (145, 37), cv2.FONT_HERSHEY_SIMPLEX, .9, (35, 35, 35), 2)
    cv2.line(crop, (0, 1), (168, 1), (30, 30, 30), 2)

    assert cell_mark_kind(crop) == "content"


def test_visual_empty_cell_bypasses_ocr_but_required_rule_is_flagged():
    import numpy as np
    from tablescan_local.ocr import LocalOcrEngine

    engine = object.__new__(LocalOcrEngine)
    engine._high_accuracy = False
    engine._read_cell = lambda *_args: pytest.fail("OCR must not run for an empty cell")
    crop = np.full((64, 160, 3), 248, np.uint8)

    optional = engine.recognize_cell(crop, numeric=True, constraints=ValueConstraints("numeric"))
    required = engine.recognize_cell(
        crop,
        numeric=True,
        constraints=ValueConstraints("numeric", allow_empty=False),
    )

    assert optional.text == "" and optional.flags == []
    assert required.text == "" and "required_cell_empty" in required.flags
