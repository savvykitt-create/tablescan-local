import cv2
import numpy as np

from tablescan_local.domain import TableTemplate
from tablescan_local.imaging import detect_crossed_rows, detect_grid


def synthetic_table() -> np.ndarray:
    image = np.full((800, 1200, 3), 255, np.uint8)
    for x in range(100, 1101, 100):
        cv2.line(image, (x, 150), (x, 650), (30, 30, 30), 3)
    for y in range(150, 651, 50):
        cv2.line(image, (100, y), (1100, y), (30, 30, 30), 3)
    cv2.line(image, (110, 427), (1090, 420), (180, 60, 20), 4)
    return image


def test_detect_grid_ignores_colored_strike_through() -> None:
    detection = detect_grid(synthetic_table())

    assert len(detection.column_guides) - 1 == 10
    assert len(detection.row_guides) - 1 == 10
    x, y, width, height = detection.table_rect.to_pixels((800, 1200, 3))
    assert abs(x - 100) < 12
    assert abs(y - 150) < 12
    assert abs(width - 1000) < 20
    assert abs(height - 500) < 20


def test_detect_crossed_rows_finds_colored_strike_through() -> None:
    image = synthetic_table()
    detection = detect_grid(image)
    template = TableTemplate(
        id="test",
        name="Synthetic",
        table_rect=detection.table_rect,
        row_guides=detection.row_guides,
        column_guides=detection.column_guides,
    )

    assert detect_crossed_rows(image, template) == [5]


def test_regular_grid_recovers_obscured_lines_without_fixed_row_count():
    from tablescan_local.imaging import regular_row_guides
    expected = list(range(10, 711, 50))
    observed = [v for v in expected if v not in {260, 310}] + [278]
    corrected, changed = regular_row_guides(sorted(observed))
    assert corrected == expected
    assert changed


def test_nonuniform_header_not_regularized():
    from tablescan_local.imaging import regular_row_guides
    lines = [0, 80, 130, 180, 230, 280, 330, 380, 430, 480]
    assert regular_row_guides(lines) == (lines, False)


def test_rotation_roundtrip_does_not_mutate_source():
    from tablescan_local.imaging import rotate_document
    image = synthetic_table()
    rotated = rotate_document([image], 90)
    assert rotated[0].shape == (1200, 800, 3)
    assert np.array_equal(rotate_document(rotated, -90)[0], image)


def test_cell_crop_variants_stay_inside_cell_and_preserve_standard_first():
    from tablescan_local.domain import NormalizedRect
    from tablescan_local.imaging import crop_cell, crop_cell_variants
    image = np.arange(100 * 100 * 3, dtype=np.uint8).reshape((100, 100, 3))
    template = TableTemplate("t", "t", NormalizedRect(0, 0, 1, 1), [0, .5, 1], [0, .5, 1])
    variants = crop_cell_variants(image, template, 0, 0)
    assert len(variants) == 3
    assert np.array_equal(variants[0], crop_cell(image, template, 0, 0, 3))
    assert variants[1].shape[:2] == (50, 50)
    assert variants[2].shape[0] < variants[0].shape[0]
    assert variants[2].shape[1] < variants[0].shape[1]


def test_context_crop_keeps_crossing_ink_but_masks_neighbour_value():
    from tablescan_local.domain import NormalizedRect
    from tablescan_local.imaging import crop_cell_owned_context

    image = np.full((100, 200, 3), 255, np.uint8)
    template = TableTemplate("t", "t", NormalizedRect(0, 0, 1, 1), [0, 1], [0, .5, 1])
    # Blue target stroke crosses the x=100 cell rule; a disconnected neighbour
    # glyph lies inside the expanded margin and must not be copied.
    cv2.line(image, (88, 25), (106, 75), (180, 45, 25), 4)
    cv2.circle(image, (112, 50), 5, (180, 45, 25), -1)
    context = crop_cell_owned_context(image, template, 0, 0)
    assert context is not None
    gray = cv2.cvtColor(context, cv2.COLOR_BGR2GRAY)
    assert np.count_nonzero(gray < 230) > 20
    # The neighbour circle would contribute a dense patch at the far right.
    assert np.count_nonzero(gray[:, -12:] < 230) <= 6


def test_heavy_black_ink_uses_unobscured_label_column():
    image = synthetic_table()
    for y in range(200, 250, 5):
        cv2.line(image, (205, y), (1100, y), (20, 20, 20), 2)
    detection = detect_grid(image)
    assert len(detection.row_guides) - 1 == 10
    assert any("label column" in warning for warning in detection.warnings)
