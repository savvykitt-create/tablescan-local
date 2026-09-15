import cv2
import numpy as np

from tablescan_local.domain import TableTemplate, NormalizedRect
from tablescan_local.ocr import OcrValue
from tablescan_local.pipeline import process_page


def test_repeated_non_numeric_ocr_marks_exclude_only_the_crossed_row():
    from tablescan_local.domain import CellResult, NormalizedRect, TableTemplate
    from tablescan_local.pipeline import detect_non_numeric_mark_rows

    template = TableTemplate(
        "t", "t", NormalizedRect(0, 0, 1, 1), [0, .5, 1], [0, .1, .28, .46, .64, .82, 1],
        row_label_columns=1,
    )
    template.ensure_column_rules()
    for rule in template.column_rules[1:]:
        rule.value_format = "numeric"
    crossed = [CellResult(0, 0, "15", "15", .99, applied_rule="Labels")]
    crossed += [
        CellResult(0, column, raw, "2.2", .76, flags=["low_confidence"],
                   alternatives="2.0 | epe | eel | $$", applied_rule="Measurements")
        for column, raw in enumerate(("epe", "eece", "tee", "ee", "oecs"), start=1)
    ]
    normal = [CellResult(1, 0, "16", "16", .99, applied_rule="Labels")]
    normal += [
        CellResult(1, column, raw, "37.3", .96, flags=["model_disagreement"] if column == 2 else [],
                   alternatives="37.3 | 31.3 | BSTE", applied_rule="Measurements")
        for column, raw in enumerate(("37.3", "Z3", "32.5", "379", "38.3"), start=1)
    ]

    assert detect_non_numeric_mark_rows([*crossed, *normal], template) == [0]


def test_suspected_crossing_keeps_values_and_requires_review(tmp_path, monkeypatch):
    template = TableTemplate("t", "t", NormalizedRect(0, 0, 1, 1), [0, .5, 1], [0, .5, 1])
    image = np.full((120, 200, 3), 255, np.uint8)
    cv2.putText(image, "34.4", (15, 45), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    class Engine:
        def recognize_cell(self, crop, numeric, constraints=None, retry_crops=None):
            assert len(retry_crops) == 2 if numeric else retry_crops is None
            return OcrValue("34.4", .99, "344", ["numeric_verification_required"], "I34.4")
    monkeypatch.setattr("tablescan_local.pipeline.detect_crossed_rows", lambda *args: [1])
    result = process_page(image, "source.png", 0, template, Engine(), crop_directory=tmp_path)
    assert template.header_rows == 0
    assert result.excluded_rows == []
    assert len(result.cells) == 4
    assert all(c.raw_text == "I34.4" and c.alternatives == "344" for c in result.cells)
    assert result.cell(1, 0).final_text == "34.4"
    crossed = result.cell(1, 1)
    assert crossed.final_text == "34.4"
    assert crossed.status == "automatic"
    assert crossed.needs_review
    assert "suspected_crossed_row" in crossed.flags
    assert "suspected_crossed_row" not in result.cell(1, 0).flags


def test_cell_rule_reaches_ocr_and_header_is_not_validated_as_a_number(tmp_path, monkeypatch):
    from tablescan_local.constraints import ValueConstraints
    from tablescan_local.domain import CellRuleRegion
    from tablescan_local.ocr import constrain_reading
    template = TableTemplate("t", "t", NormalizedRect(0, 0, 1, 1), [0, .5, 1], [0, .5, 1], header_rows=1)
    rule = ValueConstraints("numeric", minimum=0, maximum=100, decimal_places=1)
    template.cell_rules = [CellRuleRegion("r", "Measure", 1, 1, 1, 1, rule)]
    calls = []
    class Engine:
        def recognize_cell(self, crop, numeric, constraints=None, retry_crops=None):
            calls.append((numeric, constraints))
            value = OcrValue("344", .99, "34.4", raw_text="344")
            return constrain_reading(value, constraints) if constraints else OcrValue("Header", 1)
    image = np.full((120, 200, 3), 255, np.uint8)
    monkeypatch.setattr("tablescan_local.pipeline.detect_crossed_rows", lambda *args: [])
    result = process_page(image, "source.png", 0, template, Engine(), crop_directory=tmp_path)
    assert calls[0] == (False, None)
    assert calls[3][1].decimal_places == 1
    assert result.cell(0, 1).final_text == "Header"
    assert not result.cell(0, 1).flags
    assert result.cell(1, 1).final_text == "34.4"
    assert result.cell(1, 1).raw_text == "344"
    assert "Measure" in result.cell(1, 1).applied_rule
