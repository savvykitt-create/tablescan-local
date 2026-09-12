"""Writer-adaptive second pass for ambiguous numeric OCR candidates.

The adapter never learns from user corrections and never invents a digit.  It
builds a temporary page-local profile from stable OCR cells, then ranks only
the literal candidates already produced by the recognition cascade.  A cell
cannot vote for itself and repeated digits in one cell count as one source.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path

import cv2
import numpy as np

from .imaging import read_image
from .digit_verifier import DigitVerifier, hog_features, infer_numeric_geometry, segment_digits
from .domain import CellResult, PageResult, TableTemplate
from .ocr import canonical_numeric, is_simple_number, isolate_blue_ink, remove_edge_rules


CellKey = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class GlyphPrototype:
    cell: CellKey
    feature: np.ndarray
    mask: np.ndarray


@dataclass(frozen=True, slots=True)
class StyleRanking:
    winner: str
    scores: dict[str, float]
    margin: float
    evidence_cells: int


@dataclass(slots=True)
class WriterStyleProfile:
    """A bounded collection of pseudo-labelled glyphs from one page."""

    minimum_cells: int = 2
    maximum_samples_per_digit: int = 32
    samples: dict[str, list[GlyphPrototype]] = field(default_factory=lambda: defaultdict(list))

    @staticmethod
    def _feature(glyph: np.ndarray) -> np.ndarray:
        feature = hog_features(glyph)[0].astype(np.float32)
        norm = float(np.linalg.norm(feature))
        return feature / norm if norm > 1e-9 else feature

    @staticmethod
    def _mask(glyph: np.ndarray) -> np.ndarray:
        """Return a thickness-tolerant, centred glyph mask."""
        if glyph.ndim == 3:
            glyph = cv2.cvtColor(glyph, cv2.COLOR_BGR2GRAY)
        mask = np.uint8(glyph > max(18, int(np.percentile(glyph, 70)) * .22))
        if not np.any(mask):
            return mask
        ys, xs = np.where(mask)
        isolated = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        height, width = isolated.shape
        scale = min(22 / max(1, width), 22 / max(1, height))
        resized = cv2.resize(
            isolated,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_NEAREST,
        )
        canvas = np.zeros((28, 28), dtype=np.uint8)
        top = (28 - resized.shape[0]) // 2
        left = (28 - resized.shape[1]) // 2
        canvas[top:top + resized.shape[0], left:left + resized.shape[1]] = resized
        return canvas

    @staticmethod
    def _shape_similarity(left: np.ndarray, right: np.ndarray) -> float:
        """Symmetric ink coverage with a one-pixel stroke-width tolerance."""
        if not np.any(left) or not np.any(right):
            return 0.0
        kernel = np.ones((3, 3), np.uint8)
        left_wide = cv2.dilate(left, kernel)
        right_wide = cv2.dilate(right, kernel)
        coverage_left = np.count_nonzero(left & right_wide) / max(1, np.count_nonzero(left))
        coverage_right = np.count_nonzero(right & left_wide) / max(1, np.count_nonzero(right))
        return float((coverage_left + coverage_right) / 2)

    def add(self, cell: CellKey, digit: str, glyph: np.ndarray) -> None:
        if digit not in "0123456789":
            return
        existing = self.samples[digit]
        if len(existing) >= self.maximum_samples_per_digit:
            return
        existing.append(GlyphPrototype(cell, self._feature(glyph), self._mask(glyph)))

    def source_count(self, digit: str, exclude: CellKey | None = None) -> int:
        return len({item.cell for item in self.samples.get(digit, []) if item.cell != exclude})

    def support(self, glyph: np.ndarray, digit: str, exclude: CellKey | None = None) -> tuple[float, int] | None:
        """Return robust nearest-prototype support and independent source count."""
        feature = self._feature(glyph)
        mask = self._mask(glyph)
        by_cell: dict[CellKey, float] = {}
        for prototype in self.samples.get(digit, []):
            if prototype.cell == exclude:
                continue
            hog_similarity = float(np.dot(feature, prototype.feature))
            shape_similarity = self._shape_similarity(mask, prototype.mask)
            similarity = .58 * hog_similarity + .42 * shape_similarity
            by_cell[prototype.cell] = max(by_cell.get(prototype.cell, -1.0), similarity)
        if len(by_cell) < self.minimum_cells:
            return None
        # The top three allow natural variation while the independent-cell
        # requirement prevents one repeated glyph from dominating the class.
        strongest = sorted(by_cell.values(), reverse=True)[:3]
        return float(np.mean(strongest)), len(by_cell)

    def rank(
        self,
        glyphs: list[np.ndarray],
        candidates: list[str],
        variable_positions: list[int],
        exclude: CellKey | None = None,
    ) -> StyleRanking | None:
        scored: dict[str, float] = {}
        evidence: dict[str, int] = {}
        for candidate in candidates:
            digits = "".join(character for character in candidate if character.isdigit())
            if len(digits) != len(glyphs):
                continue
            supports: list[float] = []
            sources: list[int] = []
            for position in variable_positions:
                result = self.support(glyphs[position], digits[position], exclude)
                if result is None:
                    break
                value, count = result
                supports.append(value)
                sources.append(count)
            else:
                if supports:
                    scored[candidate] = float(np.mean(supports))
                    evidence[candidate] = min(sources)
        if len(scored) < 2:
            return None
        ordered = sorted(scored, key=lambda value: scored[value], reverse=True)
        winner = ordered[0]
        return StyleRanking(
            winner=winner,
            scores=scored,
            margin=scored[winner] - scored[ordered[1]],
            evidence_cells=evidence[winner],
        )


_UNSAFE_PROFILE_FLAGS = frozenset({
    "ambiguous_rule_proposals",
    "expected_range_selected_alternative",
    "high_accuracy_no_consensus",
    "low_confidence",
    "preprocessing_disagreement",
    "rule_conflict",
    "separator_inferred_from_rule",
    "separator_reclassified_from_one",
    "table_outlier",
    "unstable_consensus",
    "writer_style_ambiguous",
    "writer_style_selected",
})


def _shape(value: str) -> tuple[int, int] | None:
    normalized = canonical_numeric(value).replace("−", "-").lstrip("+-")
    if not is_simple_number(normalized):
        return None
    left, dot, right = normalized.partition(".")
    return len(left), len(right) if dot else -1


def _candidate_values(cell: CellResult, template: TableTemplate) -> list[str]:
    constraints, _ = template.value_constraints(cell.row, cell.column)
    ranked_scores = sorted(cell.candidate_scores, key=cell.candidate_scores.get, reverse=True)
    raw = [cell.final_text, *cell.alternatives.split(" | "), *ranked_scores]
    candidates: list[str] = []
    required_shape = _shape(cell.final_text)
    for value in raw:
        candidate = canonical_numeric(value)
        if not candidate or candidate in candidates or _shape(candidate) != required_shape:
            continue
        if constraints.hard_errors(candidate):
            continue
        candidates.append(candidate)
        if len(candidates) == 16:
            break
    return candidates


def _prepared_crop(cell: CellResult) -> np.ndarray | None:
    if not cell.crop_path:
        return None
    crop = read_image(cell.crop_path)
    if crop is None or crop.size == 0:
        return None
    return isolate_blue_ink(remove_edge_rules(crop))


def _segmented(cell: CellResult, crop: np.ndarray) -> list[np.ndarray] | None:
    shape = _shape(cell.final_text)
    if shape is None:
        return None
    fraction_digits = shape[1] if shape[1] >= 0 else None
    geometry = infer_numeric_geometry(crop, fraction_digits)
    if geometry is None:
        return None
    return segment_digits(crop, cell.final_text, geometry.separator_x, geometry.separator_width)


def _trusted_positions(cell: CellResult) -> list[int]:
    """Return digit positions with strong score mass despite cell-level debate."""
    selected = canonical_numeric(cell.final_text)
    selected_digits = "".join(character for character in selected if character.isdigit())
    required_shape = _shape(selected)
    if not selected_digits or required_shape is None:
        return []
    weighted: list[tuple[str, float]] = []
    for value, raw_score in cell.candidate_scores.items():
        candidate = canonical_numeric(value)
        score = float(raw_score)
        digits = "".join(character for character in candidate if character.isdigit())
        if _shape(candidate) != required_shape or len(digits) != len(selected_digits):
            continue
        if score > 0 and isfinite(score):
            weighted.append((digits, score))
    if not weighted:
        return []

    trusted = []
    for position, selected_digit in enumerate(selected_digits):
        mass: dict[str, float] = defaultdict(float)
        for digits, score in weighted:
            mass[digits[position]] += score
        own = mass.get(selected_digit, 0.0)
        total = sum(mass.values())
        runner_up = max((score for digit, score in mass.items() if digit != selected_digit), default=0.0)
        if own / max(1e-9, total) >= .60 and own / max(1e-9, runner_up) >= 2.0:
            trusted.append(position)
    return trusted


def _safe_profile_cell(cell: CellResult) -> bool:
    return bool(
        cell.status == "automatic"
        and cell.confidence >= .92
        and "high_accuracy_consensus" in cell.flags
        and not (_UNSAFE_PROFILE_FLAGS & set(cell.flags))
        and _shape(cell.final_text) is not None
    )


def build_writer_profile(page: PageResult, template: TableTemplate) -> WriterStyleProfile:
    """Build a page-local style profile without user corrections or exclusions."""
    profile = WriterStyleProfile()
    for cell in page.cells:
        if cell.row < template.header_rows or cell.column < template.row_label_columns:
            continue
        if cell.row in page.excluded_rows or not _safe_profile_cell(cell):
            continue
        crop = _prepared_crop(cell)
        glyphs = _segmented(cell, crop) if crop is not None else None
        digits = "".join(character for character in cell.final_text if character.isdigit())
        if not glyphs or len(glyphs) != len(digits):
            continue
        key = (page.page_index, cell.row, cell.column)
        for position in _trusted_positions(cell):
            profile.add(key, digits[position], glyphs[position])
    return profile


def apply_writer_adaptation(
    page: PageResult,
    template: TableTemplate,
    digit_verifier: DigitVerifier | None = None,
) -> int:
    """Add conservative writer-specific review evidence to one page.

    The pass never rewrites a value.  Even agreement between page prototypes
    and the generic digit verifier remains an explicit suggestion for a human
    reviewer because real-page validation showed that both can share a mistake.
    The return value is retained for API compatibility and is always zero.
    """
    profile = build_writer_profile(page, template)
    changed = 0
    for cell in page.cells:
        if cell.row < template.header_rows or cell.column < template.row_label_columns:
            continue
        if cell.row in page.excluded_rows or cell.status == "excluded":
            continue
        candidates = _candidate_values(cell, template)
        if len(candidates) < 2:
            continue
        digit_strings = ["".join(character for character in candidate if character.isdigit()) for candidate in candidates]
        if not digit_strings or len({len(value) for value in digit_strings}) != 1:
            continue
        crop = _prepared_crop(cell)
        glyphs = _segmented(cell, crop) if crop is not None else None
        if not glyphs or len(glyphs) != len(digit_strings[0]):
            continue
        key = (page.page_index, cell.row, cell.column)
        current = canonical_numeric(cell.final_text)
        current_digits = "".join(character for character in current if character.isdigit())
        comparisons: list[tuple[float, StyleRanking]] = []
        for candidate, candidate_digits in zip(candidates, digit_strings, strict=True):
            if candidate == current:
                continue
            variable_positions = [
                index for index, (left, right) in enumerate(zip(current_digits, candidate_digits, strict=True))
                if left != right
            ]
            if not variable_positions:
                continue
            pair = profile.rank(glyphs, [current, candidate], variable_positions, exclude=key)
            if pair is None:
                continue
            comparisons.append((pair.scores[candidate] - pair.scores[current], pair))
        if not comparisons:
            continue
        _, ranking = max(comparisons, key=lambda item: item[0])
        winner_score = ranking.scores[ranking.winner]
        shape = _shape(cell.final_text)
        fraction_digits = shape[1] if shape and shape[1] >= 0 else None
        geometry = infer_numeric_geometry(crop, fraction_digits) if crop is not None else None
        verifications = (
            # The verifier sees the complete bounded candidate set.  A third
            # candidate winning is useful evidence that the apparent pairwise
            # style match is not safe enough to rewrite the cell.
            digit_verifier.verify(crop, candidates, geometry.separator_x, geometry.separator_width)
            if digit_verifier is not None and crop is not None and geometry is not None
            else []
        )
        verifier_scores = {item.text: item.support for item in verifications}
        verifier_winner = verifications[0] if verifications else None
        verifier_margin = (
            verifier_scores.get(ranking.winner, 0.0) - verifier_scores.get(current, 0.0)
        )
        note = (
            f"page-local prototypes: {ranking.evidence_cells} independent cells; "
            f"best={ranking.winner} ({winner_score:.3f}); margin={ranking.margin:.3f}; "
            f"independent verifier={verifier_winner.text if verifier_winner else 'unavailable'}"
        )
        if ranking.winner == current:
            if winner_score >= .70 and ranking.margin >= .035:
                cell.flags = sorted(set([*cell.flags, "writer_style_agrees", "writer_style_profile_used"]))
                cell.writer_evidence = note
            continue

        differing = sum(a != b for a, b in zip(
            "".join(character for character in current if character.isdigit()),
            "".join(character for character in ranking.winner if character.isdigit()),
            strict=True,
        ))
        strong = (
            ranking.evidence_cells >= 3
            and winner_score >= .72
            and ranking.margin >= .055
            and differing <= 2
            and "digit_verifier_agrees" not in cell.flags
            and verifier_winner is not None
            and verifier_winner.text == ranking.winner
            and verifier_winner.confidence >= .70
            and verifier_winner.support >= .30
            and verifier_margin >= .06
        )
        if strong or (
            winner_score >= .69
            and ranking.margin >= .020
            and verifier_winner is not None
            and verifier_winner.text == ranking.winner
        ):
            # A plausible writer-specific contradiction is a real dispute, but
            # never an automatic correction.  Page prototypes and the generic
            # verifier made the same wrong 4/9 decision in real validation.
            cell.writer_suggestion = ranking.winner
            cell.flags = sorted(set([*cell.flags, "writer_style_ambiguous", "writer_style_profile_used"]))
            cell.writer_evidence = note
        elif verifier_winner is not None and verifier_winner.text == current:
            # A generic digit model can veto a misleading nearest-neighbour
            # match.  Record that the conflict was checked without turning a
            # previously stable value into a new dispute.
            cell.flags = sorted(set([*cell.flags, "writer_style_conflict_rejected"]))
            cell.writer_evidence = note
    return changed
