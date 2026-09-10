from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from math import exp
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

ort.disable_telemetry_events()

from rapidocr_onnxruntime import RapidOCR
from .constraints import ValueConstraints
from .digit_verifier import DigitVerifier, infer_numeric_geometry
from .numeric_decoder import CtcCandidate, ctc_prefix_beam_search


COMPLEX_NUMBER_RE = re.compile(
    r"^(?:[<>≤≥]=?\s*)?[+\-−]?\d+(?:[.,]\d+)?"
    r"(?:\s*(?:±|\+/-|[-–])\s*[+\-−]?\d+(?:[.,]\d+)?)?"
    r"(?:[eE][+\-−]?\d+)?%?$"
)


@dataclass(slots=True)
class OcrValue:
    text: str
    confidence: float
    alternative: str = ""
    flags: list[str] | None = None
    raw_text: str | None = None
    candidates: list[str] = field(default_factory=list)
    candidate_confidences: dict[str, float] = field(default_factory=dict)
    candidate_scores: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.flags is None:
            self.flags = []
        if self.raw_text is None:
            self.raw_text = self.text


@dataclass(frozen=True, slots=True)
class DecimalEvidence:
    """A separator-shaped component located between digit-sized components."""

    x: int
    width: int
    confidence: float
    kind: str = "mark"


def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").split()).strip()


def clean_numeric(text: str) -> str:
    # Do not manufacture digits from letters or discard unrecognized symbols.
    # Such a prediction must remain visible and fail the numeric grammar check.
    return clean_text(text).replace(" ", "").replace("—", "−")


def canonical_numeric(text: str) -> str:
    """Use one decimal spelling while keeping non-numeric OCR text visible."""
    cleaned = clean_numeric(text)
    return cleaned.replace(",", ".") if is_complex_number(cleaned) else cleaned


def is_simple_number(value: str) -> bool:
    return bool(re.fullmatch(r"[+\-−]?\d+(?:[.,]\d+)?", value.strip()))


def is_complex_number(value: str) -> bool:
    return bool(COMPLEX_NUMBER_RE.fullmatch(value.strip()))


class LocalOcrEngine:
    """Local-only OCR backed by the ONNX models shipped with RapidOCR."""

    model_version = "ppocrv5-server+en-mobile/numeric-v3-rules"

    def __init__(self, high_accuracy: bool = False) -> None:
        models = Path(__file__).parent / "models"
        self._engine = RapidOCR(rec_model_path=str(models / "ch_PP-OCRv5_rec_server.onnx"))
        self._numeric_check = RapidOCR(rec_model_path=str(models / "en_PP-OCRv5_rec_mobile.onnx"))
        self._high_accuracy = high_accuracy
        self._precision_engine = None
        self._digit_verifier = None
        if high_accuracy:
            self._precision_engine = RapidOCR(
                rec_model_path=str(models / "PP-OCRv6_medium_rec.onnx"),
                rec_keys_path=str(models / "ppocrv6_dict.txt"),
                rec_img_shape=[3, 48, 320],
            )
            self._digit_verifier = DigitVerifier()
            self.model_version = "ppocrv5+ppocrv6+en/numeric-template-cascade-v11-whitespace-glyphs"

    @staticmethod
    def _prepare(crop: np.ndarray, threshold: bool = False) -> np.ndarray:
        if crop.size == 0:
            return np.full((32, 96, 3), 255, dtype=np.uint8)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        if threshold:
            # Blurring before thresholding erased small decimal points.
            gray = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 7)
        height = 64
        scale = height / max(1, gray.shape[0])
        width = max(64, round(gray.shape[1] * scale))
        resized = cv2.resize(gray, (width, height), interpolation=cv2.INTER_CUBIC)
        bordered = cv2.copyMakeBorder(resized, 10, 10, 16, 16, cv2.BORDER_CONSTANT, value=255)
        return cv2.cvtColor(bordered, cv2.COLOR_GRAY2BGR)

    @staticmethod
    def _ink_ratio(crop: np.ndarray) -> float:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        return float(np.mean(gray < 205))

    def _recognize_direct(self, crop: np.ndarray, engine=None) -> tuple[str, float]:
        result, _ = (engine or self._engine)(crop, use_det=False, use_cls=False, use_rec=True)
        if not result:
            return "", 1.0
        first = result[0]
        return clean_text(str(first[0])), float(first[1])

    def recognize_cell(
        self,
        crop: np.ndarray,
        numeric: bool,
        constraints: ValueConstraints | None = None,
        retry_crops: list[np.ndarray] | None = None,
    ) -> OcrValue:
        source_crops = [crop, *(retry_crops or [])]
        # Empty paper and a lone cancellation stroke must be decided from the
        # pixels before value rules or a language model get a chance to turn
        # them into plausible digits.  All grid-safe crops must agree so that
        # a character clipped by the standard inset is never discarded.
        mark_kinds = [cell_mark_kind(source) for source in source_crops]
        visually_blank = mark_kinds and all(kind == "empty" for kind in mark_kinds)
        numeric_strike = numeric and mark_kinds and all(kind in {"empty", "strike"} for kind in mark_kinds)
        if visually_blank or numeric_strike:
            empty = OcrValue("", 1.0)
            return constrain_reading(empty, constraints) if constraints is not None else empty
        if numeric and constraints is not None and getattr(self, "_high_accuracy", False):
            value = self._recognize_numeric_slow(crop, constraints, retry_crops or [])
        else:
            value = self._read_cell(crop, numeric)
        return constrain_reading(value, constraints) if constraints is not None else value

    @staticmethod
    def _recognize_probabilities(crop: np.ndarray, engine) -> tuple[str, float, np.ndarray, list[str]]:
        """Run one recognizer pass and retain its CTC probabilities."""
        recognizer = engine.text_rec
        _, image_height, image_width = recognizer.rec_image_shape
        max_wh_ratio = max(image_width / image_height, crop.shape[1] / max(1, crop.shape[0]))
        batch = recognizer.resize_norm_img(crop, max_wh_ratio)[np.newaxis, :].astype(np.float32)
        predictions = recognizer.session(batch)[0]
        raw, confidence = recognizer.postprocess_op(predictions)[0]
        return clean_text(str(raw)), float(confidence), predictions[0], recognizer.postprocess_op.character

    def _recognize_hypotheses(
        self,
        crop: np.ndarray,
        engine,
        constraints: ValueConstraints,
    ) -> tuple[str, float, list[CtcCandidate]]:
        # Test doubles and future OCR adapters may expose only the public
        # recognition interface.  They retain the safe greedy path.
        if not hasattr(engine, "text_rec"):
            raw, confidence = self._recognize_direct(crop, engine)
            return raw, confidence, []
        try:
            raw, confidence, probabilities, characters = self._recognize_probabilities(crop, engine)
            hypotheses = ctc_prefix_beam_search(
                probabilities, characters, constraints, beam_width=64, result_limit=6,
            )
            return raw, confidence, hypotheses
        except (ValueError, IndexError, FloatingPointError):
            # An incompatible third-party model must not abort a document.  Its
            # literal greedy reading remains available and requires review.
            raw, confidence = self._recognize_direct(crop, engine)
            return raw, confidence, []

    @staticmethod
    def _rotate_view(view: np.ndarray, angle: float) -> np.ndarray:
        height, width = view.shape[:2]
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
        return cv2.warpAffine(
            view, matrix, (width, height), flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255),
        )

    def _slow_views(self, crop: np.ndarray) -> list[np.ndarray]:
        cleaned = remove_edge_rules(crop)
        ink = isolate_blue_ink(cleaned)
        gray = cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(gray)
        views = [cleaned, ink, tight_ink(ink), cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR)]
        views.extend(self._rotate_view(cleaned, angle) for angle in (-3, -1.5, 1.5, 3))
        for constant in (3, 5, 7, 9):
            threshold = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, constant,
            )
            views.append(cv2.cvtColor(threshold, cv2.COLOR_GRAY2BGR))
        return views

    def _recognize_numeric_slow(
        self,
        crop: np.ndarray,
        constraints: ValueConstraints,
        retry_crops: list[np.ndarray],
    ) -> OcrValue:
        """Constraint-aware cascade over models, crops, and CTC hypotheses."""
        if crop.size == 0 or self._ink_ratio(crop) < 0.003:
            return OcrValue("", 1.0)
        models = (
            ("ppocrv5", self._engine, 1.0),
            ("numeric-mobile", self._numeric_check, .7),
            ("ppocrv6", self._precision_engine, 1.0),
        )
        scores: dict[str, float] = defaultdict(float)
        confidence_by_value: dict[str, float] = defaultdict(float)
        model_support: dict[str, set[str]] = defaultdict(set)
        crop_support: dict[str, set[str]] = defaultdict(set)
        beam_values: set[str] = set()
        raw_values: list[str] = []
        named_views = [(f"base-{index}", view) for index, view in enumerate(self._slow_views(crop))]
        # Alternative grid-aware crops are intentionally given only two views:
        # enough to rescue clipped strokes without multiplying the full cascade.
        for crop_index, retry in enumerate(retry_crops):
            cleaned = remove_edge_rules(retry)
            ink = isolate_blue_ink(cleaned)
            named_views.extend(((f"retry-{crop_index}-clean", cleaned), (f"retry-{crop_index}-tight", tight_ink(ink))))

        source_crops = [crop, *retry_crops]
        prepared_sources = [isolate_blue_ink(remove_edge_rules(source)) for source in source_crops]
        expected_fraction = constraints.decimal_places if constraints.recover_decimal_separator else None
        geometry_sources = [
            (source, geometry) for source in prepared_sources
            if (geometry := infer_numeric_geometry(source, expected_fraction)) is not None
        ]
        eligible_geometry = [
            item for item in geometry_sources
            if not expected_fraction or item[1].separator_x is not None
        ] or geometry_sources
        geometric_source, geometry = max(
            eligible_geometry, key=lambda item: (item[1].digit_count, item[1].confidence),
            default=(None, None),
        )
        separator_sources = [
            (source, evidence) for source in prepared_sources[:3]
            if (evidence := detect_decimal_separator(source, expected_fraction)) is not None
        ]
        strongest_source, strongest_separator = max(
            separator_sources, key=lambda item: item[1].confidence,
            default=(None, None),
        )
        split_source = next((source for source in prepared_sources if decimal_parts(source, expected_fraction) is not None), None)
        parts = decimal_parts(split_source, expected_fraction) if split_source is not None else None
        geometry_boundary_available = bool(
            parts is None and geometric_source is not None and geometry is not None
            and geometry.separator_x is not None
        )
        raw_literals: list[str] = []
        recognition_cache: dict[tuple[str, tuple[int, ...], bytes], tuple[str, float, list[CtcCandidate]]] = {}

        for view_name, view in named_views:
            prepared = self._prepare(view)
            image_key = (prepared.shape, prepared.tobytes())
            for model_name, model, weight in models:
                cache_key = (model_name, *image_key)
                cached = recognition_cache.get(cache_key)
                if cached is None:
                    cached = self._recognize_hypotheses(prepared, model, constraints)
                    recognition_cache[cache_key] = cached
                raw, confidence, hypotheses = cached
                literal = clean_numeric(raw)
                value = canonical_numeric(literal)
                if value:
                    raw_literals.append(literal)
                    raw_values.append(value)
                    confidence_by_value[value] = max(confidence_by_value[value], confidence)
                contributions: dict[str, float] = {}
                if value and not constraints.hard_errors(value):
                    contributions[value] = weight * max(.1, confidence)

                if hypotheses:
                    best_log_probability = hypotheses[0].log_probability
                    for rank, hypothesis in enumerate(hypotheses):
                        candidate = clean_numeric(hypothesis.text).replace(",", ".")
                        if not candidate or constraints.hard_errors(candidate):
                            continue
                        relative = exp(max(-30.0, hypothesis.log_probability - best_log_probability))
                        beam_weight = weight * .78 * relative / (1 + rank * .20)
                        contributions[candidate] = max(contributions.get(candidate, 0.0), beam_weight)
                        confidence_by_value[candidate] = max(confidence_by_value[candidate], hypothesis.confidence)
                        beam_values.add(candidate)

                for candidate, contribution in contributions.items():
                    scores[candidate] += contribution
                    model_support[candidate].add(model_name)
                    crop_support[candidate].add(view_name.split("-", 1)[0])

                # A separator may be restored only when a distinct, low ink
                # component is physically visible between the digit groups.
                if parts is not None and re.fullmatch(r"\d{2,4}", value):
                    proposal = constraints.decimal_proposal(value)
                    if proposal is not None:
                        scores[proposal] += weight * max(.1, confidence) * .72
                        confidence_by_value[proposal] = max(confidence_by_value[proposal], confidence)
                        model_support[proposal].add(model_name)
                        crop_support[proposal].add(view_name.split("-", 1)[0])

        if strongest_separator is not None:
            for value in list(scores):
                if "." in value or "," in value:
                    scores[value] += strongest_separator.confidence * 1.25
        if parts is not None:
            left, right = parts
            prepared_left = self._prepare(tight_ink(left))
            prepared_right = self._prepare(tight_ink(right))
            for model_name, model, weight in models:
                a, ca = self._recognize_direct(prepared_left, model)
                b, cb = self._recognize_direct(prepared_right, model)
                a, b = clean_numeric(a), clean_numeric(b)
                if a.isdigit() and b.isdigit():
                    value = a + "." + b
                    if not constraints.hard_errors(value):
                        scores[value] += weight * min(ca, cb) * 1.8
                        confidence_by_value[value] = max(confidence_by_value[value], min(ca, cb))
                        model_support[value].add(model_name)
                        crop_support[value].add("split")

        geometry_used = False
        if geometry is not None and geometry.confidence >= .60:
            for candidate in list(scores):
                digit_count = sum(character.isdigit() for character in candidate)
                missing = geometry.digit_count - digit_count
                if missing > 0:
                    # A decoder is allowed to disagree about a glyph identity,
                    # but not to win by silently dropping a separately visible
                    # digit-sized ink group.
                    scores[candidate] *= .20 ** missing
                    geometry_used = True
                elif missing == 0:
                    scores[candidate] += geometry.confidence * .65

        digit_verifications = []
        verifier_source = strongest_source if strongest_source is not None else geometric_source
        verifier_x = strongest_separator.x if strongest_separator is not None else (geometry.separator_x if geometry else None)
        verifier_width = strongest_separator.width if strongest_separator is not None else (geometry.separator_width if geometry else 0)
        if verifier_source is not None and self._digit_verifier is not None and scores and verifier_x is not None:
            provisional = sorted(scores, key=scores.get, reverse=True)[:12]
            digit_verifications = self._digit_verifier.verify(
                verifier_source,
                provisional,
                verifier_x,
                verifier_width,
            )
            # This independent model is deliberately a verifier, not an
            # authority.  It can break a close tie but cannot overturn strong
            # multi-model OCR evidence by itself.
            for verification in digit_verifications:
                scores[verification.text] += .55 * verification.support * verification.confidence
        ordered = sorted(scores, key=scores.get, reverse=True)
        if not ordered:
            fallback = self._recognize_numeric(crop)
            fallback.flags = sorted(set([*(fallback.flags or []), "high_accuracy_no_consensus"]))
            return fallback
        value = ordered[0]
        raw_primary = raw_literals[0] if raw_literals else value
        flags = ["numeric_verification_required", "high_accuracy_consensus", "multistage_cascade"]
        if value in beam_values:
            flags.append("constrained_decoder_used")
        if any(name == "retry" for name in crop_support[value]):
            flags.append("crop_retry_contributed")
        if strongest_separator is not None:
            flags.append("decimal_separator_detected")
            if strongest_separator.kind == "one_like" and "." in value:
                flags.append("separator_reclassified_from_one")
        elif constraints.decimal_places:
            flags.append("separator_not_visually_confirmed")
        if geometry_used:
            flags.append("visible_digit_count_used")
        if geometry_boundary_available:
            flags.append("decimal_boundary_inferred_from_glyphs")
        margin = scores[value] / max(1e-9, scores[ordered[1]]) if len(ordered) > 1 else float("inf")
        if len(ordered) > 1 and margin < 1.8:
            flags.append("model_disagreement")
        if len(model_support[value]) < 2 or margin < 1.25:
            flags.append("unstable_consensus")
        if digit_verifications and digit_verifications[0].confidence >= .70:
            if digit_verifications[0].text == value:
                flags.append("digit_verifier_agrees")
            else:
                flags.append("digit_verifier_disagreement")
        if confidence_by_value[value] < .92:
            flags.append("low_confidence")
        if value != raw_primary:
            flags.append("alternative_selected")
        # Preserve all literal model readings as audit alternatives, including
        # readings rejected by the user's hard value rule.
        candidates = list(dict.fromkeys([*ordered, *raw_values]))
        return OcrValue(
            value, confidence_by_value[value], " | ".join(v for v in candidates if v != value)[:1000],
            flags, raw_primary, candidates, dict(confidence_by_value), dict(scores),
        )

    def _read_cell(self, crop: np.ndarray, numeric: bool) -> OcrValue:
        if crop.size == 0 or self._ink_ratio(crop) < 0.003:
            return OcrValue("", 1.0)
        if numeric:
            return self._recognize_numeric(crop)
        raw_a, confidence_a = self._recognize_direct(self._prepare(crop, threshold=False))
        raw_b, confidence_b = self._recognize_direct(self._prepare(crop, threshold=True))
        value_a = clean_numeric(raw_a) if numeric else clean_text(raw_a)
        value_b = clean_numeric(raw_b) if numeric else clean_text(raw_b)
        if confidence_b > confidence_a:
            value, confidence, alternative = value_b, confidence_b, value_a
        else:
            value, confidence, alternative = value_a, confidence_a, value_b

        flags: list[str] = []
        if confidence < (0.92 if numeric else 0.86):
            flags.append("low_confidence")
        if alternative and value and alternative != value:
            flags.append("preprocessing_disagreement")
        if numeric and value and not is_complex_number(value):
            flags.append("invalid_numeric_format")
        if not value:
            flags.append("empty_prediction")
        return OcrValue(value, confidence, alternative, flags)

    def _recognize_numeric(self, crop: np.ndarray) -> OcrValue:
        cleaned = remove_edge_rules(crop)
        ink = isolate_blue_ink(cleaned)
        candidates: list[tuple[str, float, float]] = []
        # Independent model opinions and non-blurred views. Model confidence is
        # a ranking hint, not a measured probability of a correct transcription.
        for view, threshold, engine, weight in (
            (cleaned, False, self._engine, 1.4),
            (cleaned, True, self._engine, 1.0),
            (tight_ink(ink), False, self._engine, 1.0),
            (cleaned, False, self._numeric_check, 0.8),
            (tight_ink(ink), False, self._numeric_check, 0.8),
        ):
            raw, confidence = self._recognize_direct(self._prepare(view, threshold), engine)
            candidates.append((raw, confidence, weight))
        raw_primary = candidates[0][0]
        parts = decimal_parts(ink)
        if parts is not None:
            left, right = parts
            for engine in (self._engine, self._numeric_check):
                a, ca = self._recognize_direct(self._prepare(tight_ink(left)), engine)
                b, cb = self._recognize_direct(self._prepare(tight_ink(right)), engine)
                a, b = clean_numeric(a), clean_numeric(b)
                if re.fullmatch(r"[+\-−]?\d+", a) and b.isdigit():
                    # A decimal is proposed only at an actual small, low ink
                    # component BETWEEN digit groups, never by number length.
                    candidates.append((a + "." + b, min(ca, cb), 1.0))
        scores: dict[str, float] = defaultdict(float)
        confidence_by_value: dict[str, float] = defaultdict(float)
        for raw, confidence, weight in candidates:
            value = canonical_numeric(raw)
            if not value:
                continue
            scores[value] += weight * max(0.1, confidence)
            confidence_by_value[value] = max(confidence_by_value[value], confidence)
        for value in scores:
            if not is_complex_number(value):
                scores[value] *= 0.2
            if parts is not None and re.fullmatch(r"[+\-−]?\d+\.\d+", value):
                scores[value] += 2.0
        ordered = sorted(scores, key=scores.get, reverse=True)
        value = ordered[0] if ordered else ""
        confidence = confidence_by_value[value]
        flags = ["numeric_verification_required"]
        if len(ordered) > 1:
            flags.append("model_disagreement")
        if confidence < 0.92:
            flags.append("low_confidence")
        if value and not is_complex_number(value):
            flags.append("invalid_numeric_format")
        if not value:
            flags.append("empty_prediction")
        if parts is not None and "." not in value:
            flags.append("possible_missing_decimal")
        if value != canonical_numeric(raw_primary):
            flags.append("alternative_selected")
        if any(v.startswith("1") and v[1:] in scores for v in ordered):
            flags.append("possible_border_digit")
        return OcrValue(value, confidence, " | ".join(ordered[1:5]), flags, raw_primary, ordered, dict(confidence_by_value))

    def recognize_region(self, crop: np.ndarray, numeric: bool = False) -> OcrValue:
        if numeric:
            constraints = ValueConstraints("numeric") if getattr(self, "_high_accuracy", False) else None
            return self.recognize_cell(crop, numeric=True, constraints=constraints)
        if self._ink_ratio(crop) < 0.008:
            return OcrValue("", 1.0, flags=[])
        result, _ = self._engine(crop)
        if not result:
            return OcrValue("", 0.0, flags=["empty_prediction"])
        ordered = sorted(result, key=lambda item: (min(point[1] for point in item[0]), min(point[0] for point in item[0])))
        text = clean_text(" ".join(str(item[1]) for item in ordered))
        confidence = min(float(item[2]) for item in ordered)
        if numeric:
            text = clean_numeric(text)
        flags: list[str] = []
        if confidence < 0.88:
            flags.append("low_confidence")
        if numeric and text and not is_complex_number(text):
            flags.append("invalid_numeric_format")
        return OcrValue(text, confidence, flags=flags)

    def detect_text_boxes(self, image: np.ndarray) -> list[tuple[list[list[float]], str, float]]:
        result, _ = self._engine(image)
        return result or []


def constrain_reading(reading: OcrValue, rule: ValueConstraints) -> OcrValue:
    """Rank existing OCR opinions within hard limits; keep outliers and raw text.

    Expected ranges ONLY flag surprises. An optional missing-separator proposal
    is explicitly marked and requires review; it does not delete/replace digits.
    """
    rule.validate()
    raw_candidates = [reading.text, *reading.candidates, *reading.alternative.split(" | ")]
    candidates = list(dict.fromkeys(
        canonical_numeric(value) if rule.value_format in {"numeric", "integer", "complex_numeric"} else value
        for value in raw_candidates
    ))
    candidates = [v for v in candidates if v]
    flags = list(reading.flags or [])
    compatible = [v for v in candidates if not rule.hard_errors(v)]
    selected = reading.text
    if compatible:
        preferred = [value for value in compatible if not rule.warnings(value)] if rule.prefer_expected_range else []
        selected = preferred[0] if preferred else compatible[0]
        if selected != reading.text:
            flags.append("expected_range_selected_alternative" if preferred else "rule_selected_alternative")
    elif candidates:
        insertions = [p for v in candidates if (p := rule.decimal_proposal(v)) is not None]
        reclassified = [p for v in candidates if (p := rule.separator_reclassification(v)) is not None]
        proposals = list(dict.fromkeys([*reclassified, *insertions]))
        if len(proposals) == 1:
            selected = proposals[0]
            flags.append("separator_reclassified_from_one" if proposals[0] in reclassified else "separator_inferred_from_rule")
            candidates.extend(proposals)
        else:
            flags.append("rule_conflict")
            if proposals:
                flags.append("ambiguous_rule_proposals")
                candidates.extend(proposals)
    if selected and selected != reading.text:
        flags = [f for f in flags if f not in {"invalid_numeric_format", "possible_missing_decimal", "empty_prediction"}]
    flags.extend(rule.hard_errors(selected))
    flags.extend(rule.warnings(selected))
    if selected:
        flags.append("value_rule_review_required")
    return OcrValue(
        selected, reading.candidate_confidences.get(selected, reading.confidence if selected == reading.text else 0.0),
        " | ".join(v for v in candidates if v != selected), sorted(set(flags)),
        reading.raw_text, candidates, reading.candidate_confidences, reading.candidate_scores,
    )


def remove_edge_rules(crop: np.ndarray) -> np.ndarray:
    """Remove near-boundary long rules without trimming digits in the interior."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    result = crop.copy()
    binary = gray < 150
    for x in range(w):
        if (x < w * .08 or x > w * .92) and np.mean(binary[:, x]) > .70:
            result[:, max(0, x - 2):min(w, x + 3)] = 255
    for y in range(h):
        if (y < h * .15 or y > h * .85) and np.mean(binary[y, :]) > .65:
            result[max(0, y - 2):min(h, y + 3), :] = 255
    return result


def cell_mark_kind(crop: np.ndarray) -> str:
    """Classify a cell as ``empty``, a lone ``strike``, or real ``content``.

    The test is deliberately geometric and runs before OCR.  It removes table
    borders, suppresses isolated scan speckles, preserves small decimal marks,
    and accepts a cancellation stroke only when it spans most of the cell.
    """
    if crop.size == 0:
        return "empty"
    cleaned = remove_edge_rules(crop)
    gray = cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY) if cleaned.ndim == 3 else cleaned
    height, width = gray.shape
    if not height or not width:
        return "empty"

    mask: np.ndarray
    if cleaned.ndim == 3:
        blue, green, red = cv2.split(cleaned.astype(np.int16))
        blue_signal = blue - np.maximum(green, red)
        background = float(np.percentile(gray, 75))
        threshold = max(80, min(210, round(background - 30)))
        # Scanned paper can have a broad pale-blue cast.  The previous fixed
        # colour threshold treated that cast as foreground and consequently
        # ignored small black printed row labels.  Require colour to stand out
        # from the local page tint and always retain genuinely dark ink.
        colour_threshold = max(32, round(float(np.percentile(blue_signal, 60))) + 18)
        colored = (blue_signal > colour_threshold) & (gray < 245)
        mask = (gray < threshold) | colored
    else:
        background = float(np.percentile(gray, 75))
        threshold = max(80, min(200, round(background - 32)))
        mask = gray < threshold

    count, labels, stats, _ = cv2.connectedComponentsWithStats(np.uint8(mask))
    minimum_component = max(3, round(gray.size * .00025))
    kept = np.zeros_like(mask, dtype=bool)
    strike_component = False
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        component_x = int(stats[index, cv2.CC_STAT_LEFT])
        component_y = int(stats[index, cv2.CC_STAT_TOP])
        component_width = int(stats[index, cv2.CC_STAT_WIDTH])
        component_height = int(stats[index, cv2.CC_STAT_HEIGHT])
        horizontal_border = (
            component_width >= width * .55
            and (component_y <= height * .18 or component_y + component_height >= height * .82)
        )
        vertical_border = (
            component_height >= height * .55
            and component_width <= max(6, round(width * .05))
            and (component_x <= width * .10 or component_x + component_width >= width * .90)
        )
        if horizontal_border or vertical_border:
            continue
        if area >= minimum_component and (component_width > 1 or component_height > 1):
            kept[labels == index] = True
            component_center_y = component_y + component_height / 2
            if (
                component_width >= width * .72
                and component_height <= height * .40
                and height * .15 <= component_center_y <= height * .85
            ):
                strike_component = True

    ink_pixels = int(np.count_nonzero(kept))
    if ink_pixels < max(6, round(gray.size * .001)):
        return "empty"

    if strike_component:
        return "strike"
    return "content"


def isolate_blue_ink(crop: np.ndarray) -> np.ndarray:
    b, g, r = cv2.split(crop.astype(np.float32))
    signal = np.maximum(np.maximum(b - r, b - g) - 12, 0)
    dark = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) < 180
    colored = signal > 20
    if np.count_nonzero(colored) < 15 or np.count_nonzero(colored & dark) < np.count_nonzero(dark) * .5:
        return crop
    contrast = max(30, np.percentile(signal[colored], 90))
    gray = np.uint8(255 - np.clip(signal / contrast * 255, 0, 255))
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def tight_ink(crop: np.ndarray) -> np.ndarray:
    if crop.size == 0:
        return np.full((32, 32, 3), 255, np.uint8)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ys, xs = np.where(gray < 180)
    if not len(xs):
        return crop
    x1, x2 = max(0, xs.min() - 3), min(gray.shape[1], xs.max() + 4)
    y1, y2 = max(0, ys.min() - 3), min(gray.shape[0], ys.max() + 4)
    return cv2.copyMakeBorder(crop[y1:y2, x1:x2], 6, 6, 8, 8, cv2.BORDER_CONSTANT, value=(255, 255, 255))


def detect_decimal_separator(crop: np.ndarray, expected_fraction_digits: int | None = None) -> DecimalEvidence | None:
    """Find a decimal mark, optionally testing a narrow OCR-``1`` hypothesis.

    The second path is enabled only by an explicit template precision.  It is
    lower confidence than a true small mark and remains review-required.
    """
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    _, _, stats, _ = cv2.connectedComponentsWithStats(np.uint8(gray < 170) * 255)
    items = [s for s in stats[1:] if 3 <= int(s[cv2.CC_STAT_AREA]) <= gray.size * .12]
    if not items:
        return None
    tallest = max(int(s[cv2.CC_STAT_HEIGHT]) for s in items)
    digits = [
        s for s in items
        if int(s[cv2.CC_STAT_HEIGHT]) >= tallest * .62
        and int(s[cv2.CC_STAT_WIDTH]) < gray.shape[1] * .70
    ]
    if len(digits) < 2:
        return None
    top = float(np.median([int(s[cv2.CC_STAT_TOP]) for s in digits]))
    bottom = float(np.median([int(s[cv2.CC_STAT_TOP] + s[cv2.CC_STAT_HEIGHT]) for s in digits]))
    digit_area = max(1.0, float(np.median([int(s[cv2.CC_STAT_AREA]) for s in digits])))
    candidates: list[DecimalEvidence] = []
    ordered_digits = sorted(digits, key=lambda item: int(item[cv2.CC_STAT_LEFT]))
    for component in items:
        x, y, width, height, area = map(int, component)
        vertical = (y + height / 2 - top) / max(1.0, bottom - top)
        has_left = any(int(d[0] + d[2]) <= x + 2 for d in digits)
        has_right = any(int(d[0]) >= x + width - 2 for d in digits)
        if not (has_left and has_right):
            continue
        if height <= tallest * .72 and width <= tallest * .48 and vertical >= .42 and area <= digit_area * .45:
            size_score = 1.0 - min(1.0, area / max(1.0, digit_area * .45))
            baseline_score = 1.0 - min(1.0, abs(vertical - .86) / .68)
            confidence = max(0.0, min(1.0, .35 + .35 * baseline_score + .30 * size_score))
            candidates.append(DecimalEvidence(x, width, confidence, "mark"))
            continue
        if (
            expected_fraction_digits
            and width <= tallest * .34
            and area <= digit_area * .62
            and .32 <= vertical <= 1.35
        ):
            right_count = sum(int(d[0]) >= x + width - 2 for d in ordered_digits if d is not component)
            if right_count == expected_fraction_digits:
                narrowness = 1.0 - min(1.0, width / max(1.0, tallest * .34))
                candidates.append(DecimalEvidence(x, width, .42 + .18 * narrowness, "one_like"))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    if len(candidates) > 1 and candidates[0].confidence - candidates[1].confidence < .16:
        return None
    return candidates[0]


def decimal_parts(crop: np.ndarray, expected_fraction_digits: int | None = None) -> tuple[np.ndarray, np.ndarray] | None:
    """Conservative geometric decimal proposal; ambiguous/multiple marks fail closed."""
    evidence = detect_decimal_separator(crop, expected_fraction_digits)
    if evidence is None:
        return None
    x, width = evidence.x, evidence.width
    if x <= 0 or x + width >= crop.shape[1]:
        return None
    return crop[:, :x].copy(), crop[:, x + width:].copy()
