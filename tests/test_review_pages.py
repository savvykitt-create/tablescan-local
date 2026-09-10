import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from tablescan_local.domain import CellResult, JobResult, NormalizedRect, PageResult, TableTemplate
from tablescan_local.ui import ReviewPage, create_application


def test_review_page_switches_to_next_page_with_uncertain_value(qtbot, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path / "app-data"))
    app, window = create_application()
    qtbot.addWidget(window)
    template = TableTemplate(
        id="template",
        name="Two pages",
        table_rect=NormalizedRect(0.1, 0.1, 0.8, 0.8),
        row_guides=[0.1, 0.5, 0.9],
        column_guides=[0.1, 0.5, 0.9],
    )
    template.ensure_column_rules()
    pages = [
        PageResult(0, "sample.pdf", [CellResult(0, 0, "A", "A", 1.0, status="confirmed")], []),
        PageResult(1, "sample.pdf", [CellResult(0, 0, "7", "7", 0.4, flags=["low_confidence"])], []),
    ]
    images = [np.full((100, 100, 3), 255, np.uint8) for _ in pages]
    review = ReviewPage()
    qtbot.addWidget(review)

    review.set_result(images, JobResult("sample.pdf", template, pages))

    assert review.page_select.count() == 2
    assert review.current_page == 1
    assert review.current_index == 0


def test_review_edit_requires_explicit_confirmation(qtbot):
    from PySide6.QtWidgets import QTableWidget
    review = ReviewPage()
    qtbot.addWidget(review)
    assert review.table.editTriggers() == QTableWidget.EditTrigger.NoEditTriggers
    assert review.fields_table.editTriggers() == QTableWidget.EditTrigger.NoEditTriggers


def test_clicking_result_cell_highlights_same_source_cell(qtbot):
    template = TableTemplate(
        id="linked-selection", name="Linked selection",
        table_rect=NormalizedRect(0, 0, 1, 1),
        row_guides=[0, .5, 1], column_guides=[0, .5, 1],
    )
    template.ensure_column_rules()
    cells = [
        CellResult(row, column, str(row * 2 + column), str(row * 2 + column), .99, status="confirmed")
        for row in range(2) for column in range(2)
    ]
    review = ReviewPage()
    qtbot.addWidget(review)
    review.set_result(
        [np.full((200, 200, 3), 255, np.uint8)],
        JobResult("sample.pdf", template, [PageResult(0, "sample.pdf", cells, [])]),
    )

    review._cell_clicked(1, 0)

    assert review.canvas.active_cell == (1, 0)
    orange_overlays = [
        item for item in review.canvas.scene().items()
        if hasattr(item, "pen") and item.pen().color().name().upper() == "#F97316"
    ]
    assert len(orange_overlays) == 1


def test_crop_preview_refits_after_layout_shrinks(qtbot):
    from PySide6.QtGui import QPixmap
    from tablescan_local.ui import CropPreview
    preview = CropPreview()
    qtbot.addWidget(preview)
    preview.resize(1200, 300)
    preview.show()
    image = QPixmap(600, 120)
    preview.set_source_pixmap(image)
    preview.resize(250, 105)
    qtbot.waitUntil(lambda: preview.pixmap().width() <= 250)
    assert preview.pixmap().height() <= 105
    assert abs(preview.pixmap().width() / preview.pixmap().height() - 5) < .1
    preview.setText("Done")
    preview.resize(500, 200)
    assert preview.text() == "Done"


def test_result_table_keeps_useful_height_and_hides_empty_fields_panel(qtbot):
    from PySide6.QtWidgets import QHeaderView

    rows, columns = 16, 12
    template = TableTemplate(
        id="large",
        name="Large result",
        table_rect=NormalizedRect(0.05, 0.05, 0.9, 0.9),
        row_guides=[0.05 + 0.9 * index / rows for index in range(rows + 1)],
        column_guides=[0.05 + 0.9 * index / columns for index in range(columns + 1)],
    )
    template.ensure_column_rules()
    cells = [
        CellResult(row, column, f"{row}.{column}", f"{row}.{column}", .95, status="confirmed")
        for row in range(rows)
        for column in range(columns)
    ]
    review = ReviewPage()
    qtbot.addWidget(review)
    review.resize(1400, 900)
    review.show()
    review.set_result(
        [np.full((900, 1400, 3), 255, np.uint8)],
        JobResult("sample.pdf", template, [PageResult(0, "sample.pdf", cells, [])]),
    )
    qtbot.waitUntil(lambda: review.table.viewport().height() >= 220)

    assert not review.fields_table.isVisible()
    assert not review.fields_label.isVisible()
    assert review.table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert review.table.viewport().height() >= 220
    assert review.table.viewport().height() // review.table.rowHeight(0) >= 6


def test_auto_excluded_crossed_row_displays_blank_and_can_be_restored(qtbot):
    template = TableTemplate(
        id="crossed",
        name="Crossed row",
        table_rect=NormalizedRect(0, 0, 1, 1),
        row_guides=[0, .5, 1],
        column_guides=[0, .5, 1],
    )
    template.ensure_column_rules()
    cells = [
        CellResult(0, 0, "1", "1", .99, status="confirmed"),
        CellResult(0, 1, "12.5", "12.5", .99, status="confirmed"),
        CellResult(1, 0, "2", "2", .99, status="confirmed"),
        CellResult(
            1, 1, "−", "", .70,
            flags=["crossed_out_row", "auto_excluded_crossed_row"],
            status="excluded",
            suggested_text="2.2",
        ),
    ]
    page = PageResult(0, "sample.pdf", cells, [], [1])
    review = ReviewPage()
    qtbot.addWidget(review)
    review.set_result(
        [np.full((100, 100, 3), 255, np.uint8)],
        JobResult("sample.pdf", template, [page]),
    )

    assert review.table.item(1, 1).text() == "—"
    review._cell_clicked(1, 1)
    assert review.exclude_row.isChecked()
    assert review.value_label.text() == "Empty — excluded"
    review.exclude_row.setChecked(False)
    assert page.excluded_rows == []
    assert page.cell(1, 1).final_text == "2.2"
    assert page.cell(1, 1).status == "automatic"


def test_only_real_conflicts_need_review_and_all_can_be_confirmed(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    template = TableTemplate(
        id="review-policy", name="Review policy",
        table_rect=NormalizedRect(0, 0, 1, 1), row_guides=[0, .5, 1], column_guides=[0, .5, 1],
    )
    template.ensure_column_rules()
    audit_only = CellResult(0, 0, "1", "1", .99, flags=["high_accuracy_consensus", "crop_retry_contributed"])
    disputed = CellResult(0, 1, "13.0", "13.0", .82, flags=["low_confidence", "model_disagreement"])
    another = CellResult(1, 1, "14.0", "14.0", .91, flags=["table_outlier"])
    result = JobResult("sample.pdf", template, [PageResult(0, "sample.pdf", [audit_only, disputed, another], [])])
    review = ReviewPage()
    qtbot.addWidget(review)
    review.set_result([np.full((100, 100, 3), 255, np.uint8)], result)

    assert not audit_only.needs_review
    assert disputed.needs_review and another.needs_review
    assert result.unresolved_count == 2
    assert review.confirm_all_button.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    review.confirm_all_uncertain()

    assert disputed.status == another.status == "confirmed"
    assert result.unresolved_count == 0
    assert not review.confirm_all_button.isEnabled()


def test_writer_style_suggestion_requires_explicit_confirmation(qtbot):
    template = TableTemplate(
        id="writer-suggestion", name="Writer suggestion",
        table_rect=NormalizedRect(0, 0, 1, 1), row_guides=[0, 1], column_guides=[0, 1],
    )
    template.ensure_column_rules()
    cell = CellResult(
        0, 0, "99.1", "99.1", .98,
        flags=["writer_style_ambiguous"], alternatives="49.1",
        writer_suggestion="49.1",
    )
    review = ReviewPage()
    qtbot.addWidget(review)
    review.show()
    review.set_result(
        [np.full((100, 100, 3), 255, np.uint8)],
        JobResult("sample.pdf", template, [PageResult(0, "sample.pdf", [cell], [])]),
    )

    assert review.writer_suggestion_button.isVisible()
    review.writer_suggestion_button.click()

    assert review.correct_value.text() == "49.1"
    assert cell.final_text == "99.1"
