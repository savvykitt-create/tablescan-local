import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsPixmapItem

from tablescan_local.domain import CellResult, FieldRegion, FieldResult, JobResult, NormalizedRect, PageResult, TableTemplate
from tablescan_local.ui import ReviewPage, create_application


def make_review(qtbot, pages=1):
    template = TableTemplate("review", "Review", NormalizedRect(0, 0, 1, 1), [0, .5, 1], [0, .5, 1])
    template.ensure_column_rules()
    template.fields = [FieldRegion("f", "Title", NormalizedRect(.5, 0, .5, .5))]
    result = JobResult("scan.png", template, [
        PageResult(i, "scan.png", [CellResult(r, c, f"{r}{c}", f"{r}{c}", .5, flags=["low_confidence"])
                                    for r in range(2) for c in range(2)],
                   [FieldResult("f", "Title", "Title", "Title", 1, status="confirmed")])
        for i in range(pages)
    ])
    images = [np.zeros((200, 200, 3), np.uint8) for _ in range(pages)]
    images[0][:100, :100] = (12, 75, 190)
    images[0][:100, 100:] = (88, 120, 10)
    review = ReviewPage()
    qtbot.addWidget(review)
    review.resize(1200, 800)
    review.show()
    review.set_result(images, result)
    return review, result


def test_preview_is_exact_original_even_when_saved_ocr_crop_is_wrong(qtbot, tmp_path):
    review, result = make_review(qtbot)
    bad_crop = tmp_path / "mask.png"
    cv2.imwrite(str(bad_crop), np.full((25, 25, 3), 255, np.uint8))
    result.pages[0].cells[0].preview_crop_path = str(bad_crop)
    result.pages[0].cells[0].crop_path = str(bad_crop)
    review._cell_clicked(0, 0)
    preview = review.crop_label._source_pixmap.toImage()
    assert (preview.width(), preview.height()) == (100, 100)
    assert preview.pixelColor(0, 0).getRgb()[:3] == (190, 75, 12)
    assert preview.pixelColor(99, 99).getRgb()[:3] == (190, 75, 12)
    review._cell_clicked(0, 1)
    assert review.crop_label._source_pixmap.toImage().pixelColor(0, 0).getRgb()[:3] == (10, 120, 88)


def test_next_skips_current_without_confirming_and_wraps_pages(qtbot):
    review, result = make_review(qtbot, pages=2)
    seen = []
    for _ in range(8):
        seen.append((review.current_page, review.current_kind, review.current_index))
        review.next_button.click()
    assert len(set(seen)) == 8
    assert (review.current_page, review.current_kind, review.current_index) == seen[0]
    assert result.unresolved_count == 8


def test_explicit_page_choice_stays_on_completed_page(qtbot):
    review, result = make_review(qtbot, pages=2)
    for item in result.pages[1].cells:
        item.status = "confirmed"
    review.page_select.setCurrentIndex(1)
    assert review.current_page == 1
    assert review.current_index == -1
    assert not review.confirm_button.isEnabled()
    assert review.writer_suggestion_button.isHidden()
    review.next_button.click()
    assert review.current_page == 0


def test_field_tab_keyboard_selection_highlights_region_and_source(qtbot):
    review, result = make_review(qtbot)
    review.results_tabs.setCurrentIndex(1)
    review.fields_table.setCurrentCell(0, 0)
    assert review.current_kind == "field"
    assert review.canvas.active_field == 0
    assert review.canvas.active_cell is None
    assert not review.exclude_row.isEnabled()
    preview = review.crop_label._source_pixmap.toImage()
    assert preview.pixelColor(0, 0).getRgb()[:3] == (10, 120, 88)
    review.results_tabs.setCurrentIndex(0)
    review.table.setCurrentCell(1, 1)
    assert review.canvas.active_field == -1
    assert review.canvas.active_cell == (1, 1)


def test_selection_does_not_rebuild_canvas_or_change_template(qtbot):
    review, result = make_review(qtbot)
    before = result.template.to_dict()
    source = next(item for item in review.canvas.scene().items() if isinstance(item, QGraphicsPixmapItem))
    for _ in range(15):
        review._cell_clicked(0, 0)
        review._cell_clicked(1, 1)
    after = next(item for item in review.canvas.scene().items() if isinstance(item, QGraphicsPixmapItem))
    assert source is after
    assert result.template.to_dict() == before
    assert len([i for i in review.canvas.scene().items() if i.zValue() == 30]) == 1
    assert review.canvas.read_only


def test_details_are_collapsed_and_escape_recognized_text(qtbot):
    review, result = make_review(qtbot)
    result.pages[0].cells[0].raw_text = '<img src="unexpected.png">'
    review._cell_clicked(0, 0)
    assert review.detail_scroll.isHidden()
    assert "&lt;img" in review.confidence_label.text()
    assert "<img" not in review.confidence_label.text()
    review.details_toggle.click()
    assert not review.detail_scroll.isHidden()
    assert "What to check" in review.confidence_label.text()
    assert "Reading candidates" in review.confidence_label.text()


def test_confirm_enter_advances_and_completion_clears_controls(qtbot):
    review, result = make_review(qtbot)
    for item in result.pages[0].cells[1:]:
        item.status = "confirmed"
    review.correct_value.setText("25")
    qtbot.keyClick(review.correct_value, Qt.Key.Key_Return)
    assert result.pages[0].cells[0].final_text == "25"
    assert result.unresolved_count == 0
    assert review.current_index == -1
    assert not review.confirm_button.isEnabled()
    assert not review.correct_value.isEnabled()
    assert review.canvas.active_cell is None


def test_theme_preference_persists_and_keeps_selected_text_readable(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path / "preferences"))
    app, window = create_application()
    qtbot.addWidget(window)
    window.theme_select.setCurrentIndex(1)
    assert app.property("tablescanTheme") == "dark"
    _, reopened = create_application()
    qtbot.addWidget(reopened)
    assert reopened.theme_select.currentData() == "dark"
    review, _ = make_review(qtbot)
    review.refresh_theme()
    item = review.table.item(0, 0)
    assert item.foreground().color().lightness() > item.background().color().lightness()
    reopened.theme_select.setCurrentIndex(0)
    assert app.property("tablescanTheme") == "light"


def test_scroll_thumb_drag_and_repaint_keep_position(qtbot):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QStyle, QStyleOptionSlider
    review, result = make_review(qtbot)
    # Enlarge the table without invoking OCR to exercise both scrollbar ranges.
    template = result.template
    template.row_guides = [i / 30 for i in range(31)]
    template.column_guides = [i / 20 for i in range(21)]
    template.ensure_column_rules()
    result.pages[0].cells = [CellResult(r, c, "5", "5", .99, status="confirmed")
                            for r in range(30) for c in range(20)]
    review._populate()
    qtbot.wait(30)
    for bar in (review.table.horizontalScrollBar(), review.table.verticalScrollBar()):
        assert bar.maximum() > 0
        option = QStyleOptionSlider()
        bar.initStyleOption(option)
        thumb = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, option,
                                           QStyle.SubControl.SC_ScrollBarSlider, bar)
        start = thumb.center()
        end = start + (QPoint(100, 0) if bar.orientation() == Qt.Orientation.Horizontal else QPoint(0, 100))
        qtbot.mousePress(bar, Qt.MouseButton.LeftButton, pos=start)
        qtbot.mouseMove(bar, pos=end)
        qtbot.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=end)
        assert bar.value() > 0
        value = bar.value()
        review._populate()
        assert bar.value() == value
        assert not bar.isSliderDown()


def test_hidden_editor_does_not_force_review_beyond_laptop_screen(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("TABLESCAN_DATA_DIR", str(tmp_path / "laptop"))
    _, window = create_application()
    qtbot.addWidget(window)
    window.stack.setCurrentIndex(2)
    window.resize(1180, 760)
    window.show()
    qtbot.wait(30)
    assert window.height() <= 760
    assert window.review_page.review_split.widget(1).height() >= 260
