from dataclasses import asdict

import pytest

from tablescan_local.constraints import ValueConstraints
from tablescan_local.domain import CellRuleRegion, ColumnRule, NormalizedRect, TableTemplate
from tablescan_local.ocr import OcrValue, constrain_reading


def decimal_rule(**kwargs):
    return ValueConstraints(**(dict(value_format="numeric", minimum=0, maximum=100, decimal_places=1) | kwargs))


@pytest.mark.parametrize("value,valid", [("34.4", True), ("34,4", True), ("344", False), ("34.44", False), ("-1.0", False), ("100.0", True), ("100.1", False), ("3e1", False), ("I3.4", False)])
def test_exact_decimal_and_bounds(value, valid):
    assert (not decimal_rule().hard_errors(value)) == valid


def test_lower_score_real_candidate_wins_over_invalid_number():
    value = OcrValue("344", .99, "34.4 | 3.44", [], "I344", ["344", "34.4", "3.44"], {"344": .99, "34.4": .7})
    result = constrain_reading(value, decimal_rule())
    assert result.text == "34.4"
    assert result.raw_text == "I344"
    assert result.confidence == .7
    assert "344" in result.alternative
    assert "rule_selected_alternative" in result.flags
    assert "separator_inferred_from_rule" not in result.flags


def test_hard_maximum_selects_49_8_instead_of_impossible_99_8():
    value = OcrValue("99.8", .99, "49.8", candidates=["99.8", "49.8"])
    result = constrain_reading(value, decimal_rule(maximum=60, require_decimal=True))
    assert result.text == "49.8"
    assert "rule_selected_alternative" in result.flags


def test_opt_in_decimal_proposal_is_flagged_and_not_auto_confirmed():
    original = OcrValue("429", .99)
    assert constrain_reading(original, decimal_rule()).text == "429"
    result = constrain_reading(original, decimal_rule(suggest_missing_decimal=True))
    assert result.text == "42.9" and result.raw_text == "429"
    assert "separator_inferred_from_rule" in result.flags
    assert "value_rule_review_required" in result.flags
    assert result.confidence == 0


@pytest.mark.parametrize("value", ["I429", "42.99", "142.9", "1"])
def test_rule_does_not_remove_digits_replace_letters_or_move_separator(value):
    result = constrain_reading(OcrValue(value, .99), decimal_rule(suggest_missing_decimal=True))
    assert result.text == value
    assert "rule_conflict" in result.flags


def test_explicit_template_can_reclassify_ocr_one_as_separator():
    rule = decimal_rule(suggest_missing_decimal=True, require_decimal=True, maximum=60)
    result = constrain_reading(OcrValue("3417", .99), rule)
    assert result.text == "34.7"
    assert result.raw_text == "3417"
    assert "separator_reclassified_from_one" in result.flags
    assert "value_rule_review_required" in result.flags


def test_decimal_required_is_independent_of_locale_spelling():
    rule = ValueConstraints("numeric", require_decimal=True)
    assert "decimal_part_required" in rule.hard_errors("34")
    assert not rule.hard_errors("34.0")
    assert not rule.hard_errors("34,0")


def test_conflicting_decimal_proposals_remain_unresolved():
    result = constrain_reading(OcrValue("429", .99, "479"), decimal_rule(suggest_missing_decimal=True))
    assert result.text == "429"
    assert "ambiguous_rule_proposals" in result.flags
    assert "42.9" in result.alternative and "47.9" in result.alternative


def test_expected_range_never_changes_valid_outlier():
    value = OcrValue("90.0", .99, "40.0")
    result = constrain_reading(value, decimal_rule(expected_minimum=20, expected_maximum=60))
    assert result.text == "90.0"
    assert "outside_expected_range" in result.flags
    assert "rule_selected_alternative" not in result.flags


def test_expected_range_can_rerank_real_candidates_only_when_enabled():
    value = OcrValue("97.2", .99, "47.2 | 37.2", candidates=["97.2", "47.2", "37.2"])
    preferred = decimal_rule(expected_minimum=20, expected_maximum=60, prefer_expected_range=True)
    result = constrain_reading(value, preferred)
    assert result.text == "47.2"
    assert "expected_range_selected_alternative" in result.flags
    assert result.raw_text == "97.2"
    with pytest.raises(ValueError):
        decimal_rule(prefer_expected_range=True).validate()


def test_allowed_values_are_constraints_not_a_source_for_invented_answers():
    rule = ValueConstraints("text", allowed_values=["правая", "левая"])
    result = constrain_reading(OcrValue("праваа", .99), rule)
    assert result.text == "праваа" and "value_not_allowed" in result.flags
    result = constrain_reading(OcrValue("праваа", .99, "правая"), rule)
    assert result.text == "правая"


def test_numeric_allowlist_handles_comma_and_trailing_zero():
    rule = decimal_rule(allowed_values=["34.4", "45.0"])
    assert not rule.hard_errors("34,4")
    assert "value_not_allowed" in rule.hard_errors("34.5")


@pytest.mark.parametrize("options", [dict(minimum=20, maximum=10), dict(maximum=float("nan")), dict(decimal_places=9), dict(expected_minimum=10, expected_maximum=5), dict(value_format="integer", decimal_places=1), dict(suggest_missing_decimal=True), dict(value_format="text", minimum=1), dict(value_format="integer", require_decimal=True)])
def test_invalid_rules_rejected(options):
    with pytest.raises(ValueError):
        ValueConstraints(**options).validate()


def template():
    t = TableTemplate("t", "t", NormalizedRect(0, 0, 1, 1), [0, .5, 1], [0, .5, 1])
    t.ensure_column_rules()
    return t


def test_cell_overrides_column_latest_region_wins_and_roundtrips():
    t = template()
    t.column_rules[1] = ColumnRule("Measure", minimum=0, maximum=100)
    t.cell_rules = [CellRuleRegion("a", "All data", 0, 1, 1, 1, decimal_rule()), CellRuleRegion("b", "Exception", 1, 1, 1, 1, ValueConstraints("integer", maximum=9999))]
    t.validate_value_rules()
    restored = TableTemplate.from_dict(t.to_dict())
    assert restored.value_constraints(0, 1)[0].decimal_places == 1
    assert restored.value_constraints(1, 1)[0].value_format == "integer"
    assert restored.value_constraints(1, 0)[0].value_format == "complex_numeric"
    restored.row_guides.pop()
    with pytest.raises(ValueError, match="outside"):
        restored.validate_value_rules()


def test_smaller_region_wins_even_when_broad_rule_was_added_later():
    t = template()
    exact = CellRuleRegion("exact", "Исключение", 1, 1, 1, 1, ValueConstraints("integer", maximum=9))
    broad = CellRuleRegion("broad", "Все измерения", 0, 1, 1, 1, decimal_rule())
    t.cell_rules = [exact, broad]
    rule, source = t.value_constraints(1, 1)
    assert rule.value_format == "integer"
    assert source.startswith("Исключение")


def test_equal_specificity_overlaps_are_reported_as_conflict():
    t = template()
    t.cell_rules = [
        CellRuleRegion("left", "Слева", 0, 0, 0, 1, ValueConstraints("integer")),
        CellRuleRegion("right", "Справа", 0, 1, 1, 1, ValueConstraints("numeric", decimal_places=1)),
    ]
    with pytest.raises(ValueError, match="equally specific"):
        t.validate_value_rules()


def test_old_templates_load_without_value_regions():
    old = template().to_dict(); old.pop("cell_rules")
    for rule in old["column_rules"]:
        for field in ("decimal_places", "expected_minimum", "expected_maximum", "suggest_missing_decimal", "allowed_values"):
            rule.pop(field)
    loaded = TableTemplate.from_dict(old)
    assert loaded.cell_rules == []
    assert loaded.column_rules[1].constraints().decimal_places is None


def test_empty_required_cell_is_not_invented():
    result = constrain_reading(OcrValue("", 1), decimal_rule(allow_empty=False, suggest_missing_decimal=True))
    assert result.text == "" and "required_cell_empty" in result.flags
