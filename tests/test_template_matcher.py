from tablescan_local.domain import NormalizedRect, TableTemplate
from tablescan_local.imaging import GridDetection
from tablescan_local.template_matcher import latest_template_versions, rank_templates


def make_template(identifier: str, rows: int, columns: int, *, family: str | None = None, version: int = 1):
    rect = NormalizedRect(.1, .1, .8, .8)
    row_guides = [.1 + .8 * i / rows for i in range(rows + 1)]
    column_guides = [.1 + .8 * i / columns for i in range(columns + 1)]
    return TableTemplate(
        identifier, identifier, rect, row_guides, column_guides,
        template_version=version, family_id=family or identifier,
        reference_page_aspect=1.5,
    )


def test_only_latest_family_version_is_offered():
    old = make_template("old", 10, 6, family="family", version=1)
    new = make_template("new", 10, 6, family="family", version=2)
    assert latest_template_versions([old, new]) == [new]


def test_matching_prefers_same_grid_and_explains_choice():
    expected = make_template("expected", 16, 12)
    wrong = make_template("wrong", 8, 5)
    detection = GridDetection(expected.table_rect, expected.row_guides, expected.column_guides, [])
    matches = rank_templates((1000, 1500, 3), detection, [wrong, expected])
    assert matches[0].template.id == "expected"
    assert matches[0].score > .95
    assert "число строк совпадает" in matches[0].reasons
