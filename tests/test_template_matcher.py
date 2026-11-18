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
    assert "row count matches" in matches[0].reasons


def test_rejected_row_structure_is_explained_and_cannot_be_selected(qtbot):
    from PySide6.QtCore import Qt
    from tablescan_local.ui import TemplateChoiceDialog
    template = make_template('Plantar', 12, 6)
    template.auto_fit_rows = True
    rows = template.row_guides.copy()
    rows[1] += .04
    detection = GridDetection(template.table_rect, rows, template.column_guides, title_text='Plantar')
    match, = rank_templates((1000, 1500), detection, [template])
    assert match.fitted is None and 'row structure' in match.fit_error
    assert match.score < .55
    chooser = TemplateChoiceDialog([match])
    qtbot.addWidget(chooser)
    assert chooser.list.currentRow() == 0
    assert match.fit_error in chooser.list.item(1).text()
    assert not chooser.list.item(1).flags() & Qt.ItemFlag.ItemIsEnabled


def test_choice_checks_the_rest_of_the_document_before_offering_a_fit(monkeypatch):
    import numpy as np
    from tablescan_local import template_matcher
    template = make_template('master', 12, 6)
    template.auto_fit_rows = True
    grid = GridDetection(template.table_rect, template.row_guides, template.column_guides)
    later = GridDetection(template.table_rect, [y + .02 for y in template.row_guides], template.column_guides)
    images = [np.zeros((10, 15))] * 2
    monkeypatch.setattr(template_matcher, 'detect_grid', lambda _: later)
    match, = template_matcher.rank_document_templates(images, grid, [template])
    assert match.fitted is None and 'different grids' in match.fit_error
    monkeypatch.setattr(template_matcher, 'detect_grid', lambda _: grid)
    match, = template_matcher.rank_document_templates(images, grid, [template])
    assert match.fitted is not template and match.fitted.row_guides == grid.row_guides
