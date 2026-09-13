from __future__ import annotations

from .i18n import tr, fmt, join_text
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .components import connected_components

import cv2
import numpy as np
from PIL import Image, ImageOps

from .domain import NormalizedRect, TableTemplate


SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def write_image(path: str | Path, image: np.ndarray) -> None:
    """OpenCV's filename APIs do not reliably support Unicode on Windows."""
    target = Path(path)
    ok, encoded = cv2.imencode(target.suffix, image)
    if not ok:
        raise OSError(f"Could not encode image: {target}")
    target.write_bytes(encoded.tobytes())


def read_image(path: str | Path) -> np.ndarray | None:
    try:
        data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    except OSError:
        return None


@dataclass(slots=True)
class GridDetection:
    table_rect: NormalizedRect
    row_guides: list[float]
    column_guides: list[float]
    warnings: list[str] = field(default_factory=list)


def load_document(path: str | Path, scale: float = 3.0) -> list[np.ndarray]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(tr('Unsupported file type: {p0}', p0=suffix))
    if suffix == ".pdf":
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(source))
        pages: list[np.ndarray] = []
        for index in range(len(document)):
            bitmap = document[index].render(scale=scale)
            rgb = np.asarray(bitmap.to_pil().convert("RGB"))
            pages.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        return pages
    with Image.open(source) as image:
        frames = []
        frame_count = getattr(image, "n_frames", 1)
        for index in range(frame_count):
            image.seek(index)
            rgb = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))
            frames.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        return frames


def _cluster_indices(indices: Iterable[int], gap: int = 3) -> list[int]:
    values = list(indices)
    if not values:
        return []
    groups = [[values[0]]]
    for value in values[1:]:
        if value - groups[-1][-1] <= gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [round(sum(group) / len(group)) for group in groups]


def _projection_lines(mask: np.ndarray, axis: int, threshold_ratio: float) -> list[int]:
    projection = np.sum(mask > 0, axis=axis)
    reference = mask.shape[axis]
    indices = np.where(projection >= reference * threshold_ratio)[0]
    return _cluster_indices(indices.tolist(), gap=max(2, round(reference * 0.003)))


def regular_row_guides(lines: list[int]) -> tuple[list[int], bool]:
    """Recover a strongly supported regular grid under ink, never a fixed row count.

    Non-uniform headers and merged rows are left alone. Any reconstruction must
    be checked in the grid editor before OCR.
    """
    if len(lines) < 9:
        return lines, False
    differences = np.diff(lines)
    step = float(np.median(differences))
    if step < 12 or abs(differences[0] / step - 1) > .15:
        return lines, False
    estimate = round((lines[-1] - lines[0]) / step)
    choices = []
    for count in range(max(8, estimate - 2), estimate + 3):
        expected = np.linspace(lines[0], lines[-1], count + 1)
        distances = np.abs(np.array(lines)[:, None] - expected[None, :])
        matched_lines = np.mean(distances.min(axis=1) < step * .15)
        supported_guides = np.mean(distances.min(axis=0) < step * .15)
        if matched_lines >= .82 and supported_guides >= .72:
            choices.append((matched_lines + supported_guides, expected, distances))
    if not choices:
        return lines, False
    _, expected, distances = max(choices, key=lambda item: item[0])
    corrected = [lines[int(np.argmin(distances[:, i]))] if distances[:, i].min() < step * .15 else round(y) for i, y in enumerate(expected)]
    return corrected, corrected != lines


def rotate_document(images: list[np.ndarray], degrees: int) -> list[np.ndarray]:
    """Rotate copies counterclockwise; original source files are unchanged."""
    if degrees % 90:
        raise ValueError(tr('Rotation must be a multiple of 90 degrees'))
    return [np.ascontiguousarray(np.rot90(image, (degrees // 90) % 4)) for image in images]


def detect_grid(image: np.ndarray) -> GridDetection:
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 41, 11)
    # Printed grids are neutral gray/black in the supported laboratory forms.
    # Suppressing saturated ink prevents handwritten strike-throughs from being
    # mistaken for additional row boundaries.
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    neutral_dark = cv2.inRange(hsv, np.array([0, 0, 0]), np.array([179, 105, 205]))
    binary = cv2.bitwise_and(binary, neutral_dark)

    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, width // 18), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, height // 18)))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    grid_mask = cv2.bitwise_or(horizontal, vertical)
    grid_mask = cv2.dilate(grid_mask, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(grid_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    for contour in contours:
        x, y, candidate_width, candidate_height = cv2.boundingRect(contour)
        if candidate_width < width * 0.35 or candidate_height < height * 0.2:
            continue
        aspect_bonus = min(candidate_width / width, 1.0)
        candidates.append((candidate_width * candidate_height * aspect_bonus, (x, y, candidate_width, candidate_height)))
    if not candidates:
        raise ValueError(tr('No table grid was detected. Draw the table boundary manually.'))

    _, (x, y, table_width, table_height) = max(candidates, key=lambda item: item[0])
    padding = max(2, round(min(width, height) * 0.002))
    x = max(0, x - padding)
    y = max(0, y - padding)
    table_width = min(width - x, table_width + padding * 2)
    table_height = min(height - y, table_height + padding * 2)

    # Long axis-aligned kernels lost faint or slightly wavy scan lines. Short
    # segments plus a small perpendicular tolerance retain those boundaries.
    horizontal_flexible = cv2.morphologyEx(
        cv2.dilate(binary, np.ones((5, 1), np.uint8)), cv2.MORPH_OPEN,
        np.ones((1, max(25, round(table_width * .025))), np.uint8),
    )
    roi_h = horizontal_flexible[y : y + table_height, x : x + table_width]
    roi_v = vertical[y : y + table_height, x : x + table_width]
    local_rows = _projection_lines(roi_h, axis=1, threshold_ratio=0.45)
    local_rows, reconstructed = regular_row_guides(local_rows)
    local_columns = _projection_lines(roi_v, axis=0, threshold_ratio=0.45)
    recovered_from_margin = False
    if len(local_columns) >= 3 and len(local_rows) >= 9:
        spacing = np.diff(local_rows)
        step = float(np.median(spacing))
        if np.any((spacing < step * .7) | (spacing > step * 1.3)):
            # A wide black strike can merge two boundary peaks over the data
            # columns. The untouched row-label column offers actual line
            # evidence, rather than inventing a fixed number of rows.
            left, right = local_columns[:2]
            margin = binary[y:y + table_height, x + left + 4:x + right - 4]
            if margin.shape[1] >= 20:
                margin = cv2.morphologyEx(cv2.dilate(margin, np.ones((3, 1), np.uint8)), cv2.MORPH_OPEN, np.ones((1, max(12, round(margin.shape[1] * .6))), np.uint8))
                proposed = _projection_lines(margin, axis=1, threshold_ratio=.65)
                if len(proposed) > len(local_rows):
                    proposed_step = float(np.median(np.diff(proposed)))
                    regular = np.mean(np.abs(np.diff(proposed) / proposed_step - 1) < .2) > .9
                    agreement = np.mean(np.min(np.abs(np.array(local_rows)[:, None] - np.array(proposed)[None, :]), axis=1) < proposed_step * .2) > .8
                    if regular and agreement:
                        local_rows = proposed
                        recovered_from_margin = True

    def normalize_guides(lines: list[int], origin: int, extent: int, image_extent: int) -> list[float]:
        if not lines or lines[0] > extent * 0.04:
            lines.insert(0, 0)
        if lines[-1] < extent * 0.96:
            lines.append(extent - 1)
        unique = sorted({max(0, min(extent - 1, value)) for value in lines})
        return [(origin + value) / image_extent for value in unique]

    row_guides = normalize_guides(local_rows, y, table_height, height)
    column_guides = normalize_guides(local_columns, x, table_width, width)
    return GridDetection(
        table_rect=NormalizedRect.from_pixels((x, y, table_width, table_height), image.shape),
        row_guides=row_guides,
        column_guides=column_guides,
        warnings=([tr('Some row boundaries were reconstructed. Check every blue guide.')] if reconstructed else []) + ([tr('Rows under heavy ink were recovered from the label column. Check those guides.')] if recovered_from_margin else []),
    )


def crop_normalized(image: np.ndarray, rect: NormalizedRect, inset: int = 0) -> np.ndarray:
    x, y, width, height = rect.to_pixels(image.shape)
    x += inset
    y += inset
    width = max(1, width - inset * 2)
    height = max(1, height - inset * 2)
    return image[y : y + height, x : x + width].copy()


def cell_rect(template: TableTemplate, image_shape: tuple[int, ...], row: int, column: int, inset: int = 3) -> tuple[int, int, int, int]:
    image_height, image_width = image_shape[:2]
    x1 = round(template.column_guides[column] * image_width) + inset
    x2 = round(template.column_guides[column + 1] * image_width) - inset
    y1 = round(template.row_guides[row] * image_height) + inset
    y2 = round(template.row_guides[row + 1] * image_height) - inset
    return max(0, x1), max(0, y1), max(1, x2 - x1), max(1, y2 - y1)


def crop_cell(image: np.ndarray, template: TableTemplate, row: int, column: int, inset: int = 3) -> np.ndarray:
    x, y, width, height = cell_rect(template, image.shape, row, column, inset)
    return image[y : y + height, x : x + width].copy()


def crop_cell_owned_context(
    image: np.ndarray,
    template: TableTemplate,
    row: int,
    column: int,
) -> np.ndarray | None:
    """Recover handwriting that crosses a cell rule without taking neighbours.

    The crop expands around the cell, but keeps only connected ink components
    that also occupy the target cell.  A value wholly inside the neighbouring
    cell is therefore masked out.  The variant is returned only when actual
    owned ink is found beyond the target boundary, avoiding extra OCR passes on
    ordinary cells.
    """
    image_height, image_width = image.shape[:2]
    x1 = round(template.column_guides[column] * image_width)
    x2 = round(template.column_guides[column + 1] * image_width)
    y1 = round(template.row_guides[row] * image_height)
    y2 = round(template.row_guides[row + 1] * image_height)
    cell_width, cell_height = max(1, x2 - x1), max(1, y2 - y1)
    margin_x = max(5, round(cell_width * .14))
    margin_y = max(4, round(cell_height * .22))
    ex1, ex2 = max(0, x1 - margin_x), min(image_width, x2 + margin_x)
    ey1, ey2 = max(0, y1 - margin_y), min(image_height, y2 + margin_y)
    expanded = image[ey1:ey2, ex1:ex2].copy()
    if expanded.size == 0:
        return None

    gray = cv2.cvtColor(expanded, cv2.COLOR_BGR2GRAY)
    if float(np.percentile(gray, 70)) < 210:
        return None
    hsv = cv2.cvtColor(expanded, cv2.COLOR_BGR2HSV)
    coloured = (
        (hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 150)
        & (hsv[:, :, 1] > 35) & (hsv[:, :, 2] < 248)
    )
    if np.count_nonzero(coloured) >= max(8, round(coloured.size * .0005)):
        ink = coloured
    else:
        dark = np.uint8(gray < max(95, min(205, round(float(np.percentile(gray, 78)) - 30))))
        horizontal = cv2.morphologyEx(
            dark, cv2.MORPH_OPEN, np.ones((1, max(9, round(cell_width * .58))), np.uint8),
        )
        vertical = cv2.morphologyEx(
            dark, cv2.MORPH_OPEN, np.ones((max(9, round(cell_height * .58)), 1), np.uint8),
        )
        rules = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((3, 3), np.uint8))
        ink = (dark > 0) & (rules == 0)

    core_left, core_right = x1 - ex1, x2 - ex1
    core_top, core_bottom = y1 - ey1, y2 - ey1
    count, labels, stats, _ = connected_components(np.uint8(ink))
    owned = np.zeros_like(ink, dtype=bool)
    found_spill = False
    minimum = max(3, round(ink.size * .0002))
    for index in range(1, count):
        if int(stats[index, cv2.CC_STAT_AREA]) < minimum:
            continue
        component = labels == index
        inside = component[core_top:core_bottom, core_left:core_right]
        if np.count_nonzero(inside) < 2:
            continue
        owned |= component
        horizontal_outside = int(np.count_nonzero(component[:, :core_left])) + int(
            np.count_nonzero(component[:, core_right:])
        )
        found_spill = found_spill or horizontal_outside >= max(
            5, round(int(stats[index, cv2.CC_STAT_AREA]) * .08),
        )
    if not found_spill:
        return None
    owned = cv2.dilate(np.uint8(owned), np.ones((3, 3), np.uint8)) > 0
    result = np.full_like(expanded, 255)
    result[owned] = expanded[owned]
    return cv2.copyMakeBorder(result, 4, 4, 6, 6, cv2.BORDER_CONSTANT, value=(255, 255, 255))


def cell_crop_bundle(
    image: np.ndarray,
    template: TableTemplate,
    row: int,
    column: int,
) -> tuple[list[np.ndarray], np.ndarray | None]:
    """Return grid-safe crops for recovering edge strokes and rejecting rules.

    The standard 3 px inset is first so saved previews and existing behavior
    remain stable.  A fourth, ownership-masked context crop is appended only
    when handwriting actually crosses a boundary.
    """
    variants = [crop_cell(image, template, row, column, inset) for inset in (3, 0, 6)]
    context = crop_cell_owned_context(image, template, row, column)
    if context is not None:
        variants.append(context)
    result: list[np.ndarray] = []
    for variant in variants:
        if variant.size == 0:
            continue
        if any(existing.shape == variant.shape and np.array_equal(existing, variant) for existing in result):
            continue
        result.append(variant)
    return result, context


def crop_cell_variants(image: np.ndarray, template: TableTemplate, row: int, column: int) -> list[np.ndarray]:
    return cell_crop_bundle(image, template, row, column)[0]


def detect_crossed_rows(image: np.ndarray, template: TableTemplate) -> list[int]:
    if not template.detect_crossed_rows:
        return []
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, np.array([85, 35, 25]), np.array([150, 255, 255]))
    dark = cv2.inRange(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 0, 110)
    cancellation_ink = cv2.bitwise_or(blue, dark)
    image_height, image_width = image.shape[:2]
    x, _, table_width, _ = template.table_rect.to_pixels(image.shape)
    excluded: list[int] = []
    for row in range(template.header_rows, template.rows):
        y1 = round(template.row_guides[row] * image_height)
        y2 = round(template.row_guides[row + 1] * image_height)
        roi = cancellation_ink[max(0, y1 + 2) : max(y1 + 3, y2 - 2), x : x + table_width]
        if roi.size == 0:
            continue
        # A strike-through joins handwriting from many adjacent cells into one
        # exceptionally wide ink component. Detecting that component is more
        # reliable than Hough lines here: digits in separate cells often share
        # a baseline and can otherwise look like a false horizontal line.
        # A printed border can acquire a blue tint in a scan and connect
        # otherwise separate handwritten digits. It is not a cancellation.
        # Require a connected stroke within the interior of the row itself.
        margin = max(2, round(roi.shape[0] * .18))
        interior = roi[margin:-margin]
        component_count, _, stats, _ = connected_components(interior)
        has_crossing = any(
            int(component[cv2.CC_STAT_WIDTH]) >= table_width * 0.30
            and int(component[cv2.CC_STAT_AREA]) >= int(component[cv2.CC_STAT_WIDTH]) * 1.5
            for component in stats[1:component_count]
        )
        if has_crossing:
            excluded.append(row)
    return excluded


def evenly_spaced_guides(rect: NormalizedRect, rows: int, columns: int) -> tuple[list[float], list[float]]:
    row_guides = [rect.y + rect.height * index / rows for index in range(rows + 1)]
    column_guides = [rect.x + rect.width * index / columns for index in range(columns + 1)]
    return row_guides, column_guides
