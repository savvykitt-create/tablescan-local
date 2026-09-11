"""Rerank existing numeric candidates without adding models or training.

Shape-preserving views retain the layout of the original cell. Tight crops,
rotations and thresholds are useful for candidate discovery, but repeated
transformations must not outvote independent recognizers at the final decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, isfinite, log
import re

from .numeric_decoder import CtcCandidate, CtcSequence


def numeric_shape(text: str) -> str | None:
    if not re.fullmatch(r"[+\-−]?\d+(?:[.,]\d+)?", text):
        return None
    return re.sub(r"\d", "#", text.replace(",", ".").replace("−", "-"))


def is_layout_preserving_view(name: str) -> bool:
    return name in {"base-0", "base-1", "base-3"} or (
        name.startswith("retry-") and name.endswith("-clean")
    )


@dataclass
class CandidateEvidence:
    # One vote distribution per distinct image per model, not one per alias.
    observations: dict[str, dict[object, dict[str, float]]] = field(default_factory=dict)
    sequences: dict[str, dict[object, CtcSequence]] = field(default_factory=dict)
    split_readings: dict[str, tuple[str, float]] = field(default_factory=dict)

    def add(self, model: str, image_key: object, hypotheses: list[CtcCandidate], sequence: CtcSequence | None = None) -> None:
        values: dict[str, float] = {}
        for candidate in hypotheses:
            value = candidate.text.replace(",", ".").replace("−", "-")
            probability = candidate.log_probability
            if not value or not isfinite(probability):
                continue
            # Unlike normalization against the best surviving hypothesis, this
            # retains evidence that ALL permitted numbers are visually unlikely.
            support = exp(min(0.0, probability / len(value)))
            values[value] = max(values.get(value, 0.0), support)
        self.observations.setdefault(model, {})[image_key] = values
        if sequence is not None:
            self.sequences.setdefault(model, {})[image_key] = sequence

    def add_split(self, model: str, value: str, confidence: float) -> None:
        self.split_readings[model] = (value, confidence)

    def rank(self, current: str, candidates: list[str], *, minimum_digits: int = 0,
             digit_support: dict[str, float] | None = None) -> tuple[str, dict[str, float], list[str]]:
        shape = numeric_shape(current)
        models = list(self.observations.values())
        if shape is None or len(models) < 2:
            return current, {}, []
        # Every supplied candidate has already passed the template's hard rules.
        # The incumbent is not evidence for its own digit count.
        eligible = [value for value in dict.fromkeys(candidates) if numeric_shape(value) is not None]
        if current not in eligible or len(eligible) < 2:
            return current, {}, []
        by_model = []
        for model, images in self.observations.items():
            views = []
            for key, beam in images.items():
                sequence = self.sequences.get(model, {}).get(key)
                if sequence is None:
                    views.append(beam)
                else:
                    views.append({value: exp(min(0.0, probability / len(value)))
                                  for value, probability in sequence.log_likelihoods(eligible).items()})
            if views:
                by_model.append({value: sum(view.get(value, 0.0) for view in views) / len(views)
                                 for value in eligible})
        scores = {
            value: exp(sum(log(max(.01, model[value])) for model in by_model) / len(by_model))
            for value in eligible
        }
        # Connected components are a LOWER bound: penalize an omitted separate
        # glyph, but never reward a shorter string for matching touching groups.
        for value in scores:
            missing = minimum_digits - sum(c.isdigit() for c in value)
            if missing > 0:
                scores[value] *= .20 ** missing
        ordered = sorted(eligible, key=lambda value: (-scores[value], value != current, value))
        winner = ordered[0]
        margin = scores[winner] / max(1e-12, scores[ordered[1]])
        agreeing = sum(
            model[winner] >= .05 and model[winner] >= 1.10 * max(
                (score for value, score in model.items() if value != winner), default=0.0,
            ) for model in by_model
        )
        corroborated = agreeing >= 2 or (digit_support or {}).get(winner, 0.0) >= .90
        flags = []
        if margin < 1.10:
            flags.append("candidate_ranking_disagreement")
        # Absolute sequence support is not a calibrated accuracy probability.
        # Stable, corroborated readings should not acquire a review requirement
        # solely because this statistic is below an arbitrary probability-like cut.
        if scores[winner] < .5 and (winner != current or not corroborated or margin < 1.10):
            flags.append("weak_sequence_evidence")

        # One split opinion per model, not one vote per transformed image.
        # A weak/clipped split cannot overturn the original cell on its own.
        split_votes: dict[str, int] = {}
        for value, confidence in self.split_readings.values():
            if value in scores and confidence >= .90:
                split_votes[value] = split_votes.get(value, 0) + 1
        split_winners = [value for value, votes in split_votes.items()
                        if votes >= 2 and scores[value] >= scores[winner] * .5
                        and (value == winner or (digit_support or {}).get(value, 0.0) >= .90)]
        if len(split_winners) == 1:
            selected = split_winners[0]
            flags.append("split_consensus_used")
        else:
            # Very weak sequence evidence needs agreement of independent models.
            # An almost-unreadable model cannot veto two usable readings merely
            # because its surviving numeric beam preferred a different glyph.
            selected = winner if margin >= 1.10 and (scores[winner] >= .5 or corroborated) else current
        if selected != current:
            flags.append("candidate_reranked")
        return selected, scores, flags
