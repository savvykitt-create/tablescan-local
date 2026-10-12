from tablescan_local.constraints import ValueConstraints
from tablescan_local.domain import CellResult, ColumnRule, NormalizedRect, PageResult, TableTemplate
from tablescan_local.table_checks import flag_table_outliers


def page(values):
    template = TableTemplate("t", "t", NormalizedRect(0, 0, 1, 1), [0, 1], [i / len(values) for i in range(len(values) + 1)], row_label_columns=0)
    template.column_rules = [ColumnRule(f"c{i}", value_format="numeric") for i in range(len(values))]
    # A shared applied rule indicates that the cells are semantically comparable.
    cells = [CellResult(0, index, value, value, .9, alternatives="47.2 | 77.2", applied_rule="Measurements") for index, value in enumerate(values)]
    return template, PageResult(0, "x", cells, [])


def test_extreme_outlier_is_flagged_but_never_rewritten():
    template, result = page(["11.3", "26.2", "24.2", "18.7", "20.5", "19.8", "51.2", "97.2", "56.7", "46.7"])
    flag_table_outliers(result, template)
    outlier = result.cells[7]
    assert outlier.final_text == "97.2"
    assert "table_outlier" in outlier.flags
    assert "table_outlier_with_plausible_alternative" in outlier.flags


def test_small_or_naturally_variable_groups_are_not_rewritten_or_flagged():
    template, result = page(["10.0", "20.0", "30.0", "40.0"])
    before = [cell.final_text for cell in result.cells]
    flag_table_outliers(result, template)
    assert [cell.final_text for cell in result.cells] == before
    assert all("table_outlier" not in cell.flags for cell in result.cells)


def test_correction_recomputes_outlier_flags_without_erasing_other_evidence():
    template, result = page(['10.0', '10.1', '10.2', '10.3', '10.4', '90.0'])
    last = result.cells[-1]
    last.flags = ['model_disagreement']
    flag_table_outliers(result, template)
    assert 'table_outlier' in last.flags
    last.final_text = '10.5'
    flag_table_outliers(result, template)
    assert last.flags == ['model_disagreement']
    assert last.needs_review


def test_excluded_values_do_not_distort_comparable_group():
    template, result = page(['10', '11', '12', '13', '14', '90', '91', '92', '93', '94'])
    template.set_cell_exclusions({(0, c) for c in range(6, 10)}, True)
    flag_table_outliers(result, template)
    assert 'table_outlier' in result.cells[5].flags
    assert all('table_outlier' not in cell.flags for cell in result.cells[6:])
