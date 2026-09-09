"""Independent EMNIST-based verifier for candidate numeric strings."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import cv2
import numpy as np
import onnxruntime as ort


def hog_features(images: np.ndarray, batch_size: int = 4096) -> np.ndarray:
    """Small NumPy HOG used identically during training and local inference."""
    if images.ndim == 2:
        images = images[np.newaxis, :]
    batches = []
    for start in range(0, len(images), batch_size):
        batch = images[start:start + batch_size].astype(np.float32) / 255
        gx = np.zeros_like(batch)
        gy = np.zeros_like(batch)
        gx[:, :, 1:-1] = batch[:, :, 2:] - batch[:, :, :-2]
        gy[:, 1:-1, :] = batch[:, 2:, :] - batch[:, :-2, :]
        magnitude = np.hypot(gx, gy)
        angle = np.mod(np.arctan2(gy, gx), np.pi)
        orientation = np.minimum(8, (angle * (9 / np.pi)).astype(np.int8))
        cell_magnitude = magnitude.reshape((-1, 4, 7, 4, 7)).transpose(0, 1, 3, 2, 4)
        cell_orientation = orientation.reshape((-1, 4, 7, 4, 7)).transpose(0, 1, 3, 2, 4)
        histogram = np.stack([
            np.sum(cell_magnitude * (cell_orientation == index), axis=(3, 4))
            for index in range(9)
        ], axis=-1)
        blocks = []
        for row in range(3):
            for column in range(3):
                block = histogram[:, row:row + 2, column:column + 2].reshape((-1, 36))
                block /= np.sqrt(np.sum(block * block, axis=1, keepdims=True) + 1e-6)
                blocks.append(block)
        batches.append(np.concatenate(blocks, axis=1).astype(np.float32))
    return np.concatenate(batches)


@dataclass(frozen=True, slots=True)
class DigitVerification:
    text: str
    support: float
    predicted: str
    confidence: float


@dataclass(frozen=True, slots=True)
class NumericGeometry:
    """Conservative glyph-count and decimal-boundary evidence from pixels."""

    digit_count: int
    separator_x: int | None
    separator_width: int = 0
    confidence: float = 0.0
    separator_visible: bool = False


def infer_numeric_geometry(crop: np.ndarray, fraction_digits: int | None = None) -> NumericGeometry | None:
    """Estimate how many digit-sized ink groups are physically present.

    This is deliberately a lower-bound detector: detached pen accents are
    ignored and touching digits remain one group.  It may therefore reject a
    candidate that drops a clearly separate glyph, but it never creates the
    identity of that glyph from a value range.
    """
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop.copy()
    mask = np.uint8(gray < 185)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    components = [
        tuple(map(int, stat))
        for stat in stats[1:count]
        if int(stat[cv2.CC_STAT_AREA]) >= max(3, round(gray.size * .00035))
    ]
    if len(components) < 2:
        return None
    plausible = [item for item in components if item[2] < gray.shape[1] * .72 and item[3] < gray.shape[0] * .96]
    if len(plausible) < 2:
        return None
    tallest = max(item[3] for item in plausible)
    median_area = float(np.median([item[4] for item in plausible]))
    digits = [
        item for item in plausible
        if item[3] >= tallest * .62
        and item[2] >= max(2, round(tallest * .10))
        and item[4] >= max(6, round(tallest * .72))
        and (item[3] >= tallest * .78 or item[4] >= median_area * .32)
    ]
    digits.sort(key=lambda item: item[0])
    if len(digits) < 2 or len(digits) > 8:
        return None

    heights = np.asarray([item[3] for item in digits], dtype=np.float32)
    height_consistency = float(np.min(heights) / max(1.0, np.max(heights)))
    confidence = max(.52, min(.90, .55 + .35 * height_consistency))
    separator_x: int | None = None
    separator_width = 0
    separator_visible = False
    if fraction_digits and len(digits) > fraction_digits:
        left_digit = digits[-fraction_digits - 1]
        right_digit = digits[-fraction_digits]
        gap_left = left_digit[0] + left_digit[2]
        gap_right = right_digit[0]
        if gap_right >= gap_left - 2:
            median_top = float(np.median([item[1] for item in digits]))
            median_bottom = float(np.median([item[1] + item[3] for item in digits]))
            marks = []
            for item in components:
                if item in digits:
                    continue
                x, y, width, height, area = item
                center_x = x + width / 2
                center_y = y + height / 2
                if (
                    gap_left - 3 <= center_x <= gap_right + 3
                    and center_y >= median_top + (median_bottom - median_top) * .45
                    and height <= tallest * .72
                    and area <= float(np.median([digit[4] for digit in digits])) * .58
                ):
                    marks.append(item)
            if marks:
                mark = max(marks, key=lambda item: (item[4], item[1] + item[3] / 2))
                separator_x, separator_width = mark[0], max(1, mark[2])
                separator_visible = True
                confidence = min(.94, confidence + .06)
            else:
                # A comma often touches the preceding handwritten digit.  The
                # whitespace before the known fractional glyph is still a safe
                # split point, but is not reported as a visually seen mark.
                separator_x = max(1, round((gap_left + gap_right) / 2))
                separator_width = 1
    return NumericGeometry(len(digits), separator_x, separator_width, confidence, separator_visible)


def _split_part(mask: np.ndarray, start: int, end: int, count: int) -> list[tuple[int, int]] | None:
    if count <= 0:
        return [] if count == 0 else None
    part = mask[:, max(0, start):min(mask.shape[1], end)]
    ys, xs = np.where(part)
    if not len(xs):
        return None
    left, right = int(xs.min()) + start, int(xs.max()) + start + 1
    if count == 1:
        return [(left, right)]
    width = right - left
    if width < count * 3:
        return None
    projection = np.sum(mask[:, left:right], axis=0).astype(np.float32)
    cuts = []
    previous = left
    for index in range(1, count):
        expected = left + width * index / count
        radius = max(2, round(width / count * .38))
        low = max(previous + 2, round(expected - radius))
        high = min(right - (count - index) * 2, round(expected + radius) + 1)
        if low >= high:
            return None
        positions = np.arange(low, high)
        local_projection = projection[positions - left]
        balance_penalty = np.abs(positions - expected) / max(1.0, width) * mask.shape[0] * .18
        cut = int(positions[np.argmin(local_projection + balance_penalty)])
        cuts.append(cut)
        previous = cut
    bounds = [left, *cuts, right]
    segments = [(bounds[index], bounds[index + 1]) for index in range(count)]
    if any(np.count_nonzero(mask[:, a:b]) < 4 for a, b in segments):
        return None
    return segments


def segment_digits(crop: np.ndarray, text: str, separator_x: int | None = None, separator_width: int = 0) -> list[np.ndarray] | None:
    """Segment a crop according to a candidate shape; fail if geometry is weak."""
    normalized = text.replace(",", ".").replace("−", "-").lstrip("+-")
    if not re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop.copy()
    mask = gray < 185
    height, width = mask.shape
    if not np.any(mask):
        return None
    if "." in normalized:
        if separator_x is None:
            return None
        left_text, right_text = normalized.split(".", 1)
        separator_left = max(0, separator_x - 1)
        separator_right = min(width, separator_x + max(1, separator_width) + 1)
        mask[:, separator_left:separator_right] = False
        bounds = _split_part(mask, 0, separator_left, len(left_text))
        right_bounds = _split_part(mask, separator_right, width, len(right_text))
        if bounds is None or right_bounds is None:
            return None
        bounds += right_bounds
    else:
        bounds = _split_part(mask, 0, width, len(normalized))
        if bounds is None:
            return None

    glyphs = []
    inverted = 255 - gray
    for left, right in bounds:
        region_mask = mask[:, left:right]
        ys, xs = np.where(region_mask)
        if not len(xs):
            return None
        x1, x2 = left + int(xs.min()), left + int(xs.max()) + 1
        y1, y2 = int(ys.min()), int(ys.max()) + 1
        glyph = inverted[y1:y2, x1:x2]
        scale = min(20 / max(1, glyph.shape[1]), 20 / max(1, glyph.shape[0]))
        target_width = max(1, round(glyph.shape[1] * scale))
        target_height = max(1, round(glyph.shape[0] * scale))
        resized = cv2.resize(glyph, (target_width, target_height), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        canvas = np.zeros((28, 28), dtype=np.uint8)
        x = (28 - target_width) // 2
        y = (28 - target_height) // 2
        canvas[y:y + target_height, x:x + target_width] = resized
        glyphs.append(canvas)
    return glyphs


class DigitVerifier:
    def __init__(self, model_path: str | Path | None = None) -> None:
        path = Path(model_path) if model_path else Path(__file__).parent / "models" / "emnist_digit_cnn.onnx"
        self.session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.validation_accuracy = .99585

    def _probabilities(self, glyphs: list[np.ndarray]) -> np.ndarray:
        values = np.stack(glyphs).astype(np.float32)[:, np.newaxis] / 255
        values = (values - .5) / .5
        logits = self.session.run(None, {self.input_name: values})[0]
        logits -= np.max(logits, axis=1, keepdims=True)
        probabilities = np.exp(logits)
        probabilities /= np.sum(probabilities, axis=1, keepdims=True)
        return probabilities

    def verify(
        self,
        crop: np.ndarray,
        candidates: list[str],
        separator_x: int | None,
        separator_width: int = 0,
    ) -> list[DigitVerification]:
        results = []
        # Candidate strings with the same number of integer/fraction glyphs
        # share exactly the same segmentation.  Run the ONNX verifier once for
        # that geometry instead of once per textual hypothesis.
        cache: dict[tuple[int, int], tuple[list[np.ndarray], np.ndarray] | None] = {}
        for candidate in dict.fromkeys(candidates):
            normalized = candidate.replace(",", ".").replace("−", "-").lstrip("+-")
            if not re.fullmatch(r"\d+(?:\.\d+)?", normalized):
                continue
            left, dot, right = normalized.partition(".")
            shape = (len(left), len(right) if dot else -1)
            if shape not in cache:
                glyphs = segment_digits(crop, candidate, separator_x, separator_width)
                cache[shape] = (glyphs, self._probabilities(glyphs)) if glyphs else None
            cached = cache[shape]
            if cached is None:
                continue
            glyphs, probabilities = cached
            digit_text = "".join(character for character in candidate if character.isdigit())
            if len(digit_text) != len(glyphs):
                continue
            best = np.argmax(probabilities, axis=1)
            expected = np.asarray([int(character) for character in digit_text])
            support = probabilities[np.arange(len(expected)), expected]
            confidence = float(np.mean(np.max(probabilities, axis=1)))
            # A geometric mean prevents one implausible digit from being hidden
            # by several easy digits shared by all candidates.
            joint_support = float(np.exp(np.mean(np.log(np.clip(support, 1e-9, 1)))))
            results.append(DigitVerification(candidate.replace(",", "."), joint_support, "".join(map(str, best)), confidence))
        return sorted(results, key=lambda item: item.support, reverse=True)

    def repeated_digit_support(
        self,
        crop: np.ndarray,
        candidates: list[str],
        separator_x: int | None,
        separator_width: int = 0,
    ) -> dict[str, float]:
        """Use a confidently labelled glyph as an in-cell handwriting exemplar.

        Sequence recognizers can collapse or relabel the second of two similar
        handwritten digits.  When two segmented glyphs have closely matching
        HOG shapes and one is classified very confidently, this supplies a
        small independent vote for candidates that label both alike.
        """
        candidates = list(dict.fromkeys(candidate.replace(",", ".") for candidate in candidates))
        references = [
            candidate for candidate in candidates
            if segment_digits(crop, candidate, separator_x, separator_width)
        ]
        if not references:
            return {}
        reference = max(references, key=lambda value: sum(character.isdigit() for character in value))
        glyphs = segment_digits(crop, reference, separator_x, separator_width)
        if not glyphs or len(glyphs) < 2:
            return {}
        features = hog_features(np.stack(glyphs))
        norms = np.maximum(1e-9, np.linalg.norm(features, axis=1))
        similarities = features @ features.T / (norms[:, None] * norms[None, :])
        probabilities = self._probabilities(glyphs)
        support: dict[str, float] = {}
        for candidate in candidates:
            digits = "".join(character for character in candidate if character.isdigit())
            if len(digits) != len(glyphs):
                continue
            votes = []
            for first in range(len(digits)):
                for second in range(first + 1, len(digits)):
                    if digits[first] != digits[second]:
                        continue
                    digit = int(digits[first])
                    similarity = float(similarities[first, second])
                    first_anchor = (
                        float(probabilities[first, digit]) >= .88
                        and float(np.max(probabilities[second])) < .85
                    )
                    second_anchor = (
                        float(probabilities[second, digit]) >= .88
                        and float(np.max(probabilities[first])) < .85
                    )
                    anchor = float(max(probabilities[first, digit], probabilities[second, digit]))
                    if similarity >= .62 and (first_anchor or second_anchor):
                        votes.append(similarity * anchor)
            if votes:
                support[candidate] = float(max(votes))
        return support
