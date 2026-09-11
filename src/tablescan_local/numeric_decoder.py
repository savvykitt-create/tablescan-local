"""Constraint-aware CTC decoding for handwritten numeric cells.

The OCR model still supplies every character probability.  This module only
searches those probabilities more carefully: it never manufactures a glyph
that the model did not score.  Hard template rules prune impossible prefixes;
soft/expected ranges deliberately do not affect the result.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import exp, inf, log, log10, log1p

import numpy as np

from .constraints import ValueConstraints


NEG_INF = -inf


def _log_add(*values: float) -> float:
    if len(values) == 2:
        first, second = values
        if first == NEG_INF:
            return second
        if second == NEG_INF:
            return first
        if second > first:
            first, second = second, first
        return first + log1p(exp(second - first))
    finite = tuple(value for value in values if value != NEG_INF)
    if not finite:
        return NEG_INF
    maximum = max(finite)
    return maximum + log(sum(exp(value - maximum) for value in finite))


@dataclass(frozen=True, slots=True)
class CtcCandidate:
    text: str
    log_probability: float
    confidence: float


@dataclass(frozen=True, slots=True)
class CtcSequence:
    """Compact original probabilities, reusable after candidate discovery.

    Keeping only numeric columns saves memory without renormalizing them.
    A candidate absent from a truncated beam can still be scored exactly.
    """

    probabilities: np.ndarray
    characters: tuple[str, ...]

    @classmethod
    def from_output(cls, probabilities: np.ndarray, characters: list[str]) -> CtcSequence:
        if probabilities.ndim != 2 or probabilities.shape[1] != len(characters):
            raise ValueError("CTC probabilities and character dictionary do not match")
        indices = [i for i, c in enumerate(characters) if i == 0 or c in "0123456789.,+-−"]
        return cls(probabilities[:, indices].copy(), tuple(characters[i] for i in indices))

    def log_likelihoods(self, candidates: list[str]) -> dict[str, float]:
        """CTC forward sum for every literal candidate, including repeated digits.

        Decimal/sign spellings are evaluated as separate literal CTC sequences
        and their probabilities summed, so aliases do not change token collapse.
        """
        from itertools import product
        variants: list[tuple[str, str]] = []
        for value in dict.fromkeys(candidates):
            choices = [(".", ",") if c == "." else ("-", "−") if c == "-" else (c,) for c in value]
            variants.extend((value, "".join(chars)) for chars in product(*choices))
        result = {value: NEG_INF for value in candidates}
        if not variants or not len(self.probabilities):
            return result
        lookup = {c: i for i, c in enumerate(self.characters)}
        variants = [(v, s) for v, s in variants if all(c in lookup for c in s)]
        if not variants:
            return result
        states = max(2 * len(s) + 1 for _, s in variants)
        tokens = np.zeros((len(variants), states), dtype=np.intp)
        valid = np.zeros(tokens.shape, dtype=bool)
        skip = np.zeros(tokens.shape, dtype=bool)
        ends = []
        for row, (_, text) in enumerate(variants):
            length = 2 * len(text) + 1
            valid[row, :length] = True
            tokens[row, 1:length:2] = [lookup[c] for c in text]
            for position in range(3, length, 2):
                skip[row, position] = tokens[row, position] != tokens[row, position - 2]
            ends.append(length - 1)
        alpha = np.full(tokens.shape, NEG_INF)
        alpha[:, 0] = 0.0
        with np.errstate(divide="ignore"):
            logs = np.log(np.clip(self.probabilities.astype(np.float64), 0.0, 1.0))
        for timestep in logs:
            advance = np.full_like(alpha, NEG_INF)
            advance[:, 1:] = alpha[:, :-1]
            jump = np.full_like(alpha, NEG_INF)
            jump[:, 2:] = alpha[:, :-2]
            jump[~skip] = NEG_INF
            alpha = np.logaddexp(np.logaddexp(alpha, advance), jump) + timestep[tokens]
            alpha[~valid] = NEG_INF
        for row, ((value, _), end) in enumerate(zip(variants, ends, strict=True)):
            probability = float(np.logaddexp(alpha[row, end], alpha[row, max(0, end - 1)])) if end else float(alpha[row, end])
            result[value] = _log_add(result[value], probability)
        return result


class NumericPrefixGrammar:
    """A fail-closed prefix grammar compiled from one cell's hard rules."""

    def __init__(self, rule: ValueConstraints) -> None:
        rule.validate()
        self.rule = rule
        self.allowed = tuple(dict.fromkeys(self.normalize(value) for value in rule.allowed_values))
        self.maximum_integer_digits = self._maximum_integer_digits()

    @staticmethod
    def normalize(text: str) -> str:
        return text.replace(",", ".").replace("−", "-")

    def _maximum_integer_digits(self) -> int:
        bounds = [abs(float(value)) for value in (self.rule.minimum, self.rule.maximum) if value is not None]
        if not bounds:
            return 10
        largest = max(bounds)
        # Keep room for leading zeroes: they are legal and may genuinely be
        # written, so a bound must not prune them too aggressively.
        digits = 1 if largest < 1 else int(log10(largest)) + 1
        return min(12, digits + 2)

    def prefix_allowed(self, raw_prefix: str) -> bool:
        prefix = self.normalize(raw_prefix)
        if self.allowed:
            return any(value.startswith(prefix) for value in self.allowed)
        if not prefix:
            return True
        if len(prefix) > 16:
            return False
        if prefix[0] in "+-":
            if len(prefix) > 1 and prefix[1] in "+-":
                return False
            if prefix[0] == "-" and self.rule.minimum is not None and self.rule.minimum >= 0:
                return False
            body = prefix[1:]
        else:
            body = prefix
        if any(character not in "0123456789." for character in body):
            return False
        if body.count(".") > 1 or body.startswith("."):
            return False
        if self.rule.value_format == "integer" or self.rule.decimal_places == 0:
            return "." not in body and len(body) <= self.maximum_integer_digits
        integer, separator, fraction = body.partition(".")
        if len(integer) > self.maximum_integer_digits:
            return False
        if separator and self.rule.decimal_places is not None and len(fraction) > self.rule.decimal_places:
            return False
        return True

    def complete_allowed(self, raw_text: str) -> bool:
        text = self.normalize(raw_text)
        return bool(text) and not self.rule.hard_errors(text)


def ctc_prefix_beam_search(
    probabilities: np.ndarray,
    characters: list[str],
    rule: ValueConstraints,
    *,
    beam_width: int = 48,
    result_limit: int = 8,
) -> list[CtcCandidate]:
    """Return the most likely rule-compatible strings from CTC probabilities.

    ``characters[0]`` is the PaddleOCR CTC blank.  Only numeric glyphs are
    expanded, but their probabilities remain the original model probabilities
    rather than being renormalized over the reduced alphabet.
    """
    if probabilities.ndim != 2 or probabilities.shape[1] != len(characters):
        raise ValueError("CTC probabilities and character dictionary do not match")
    if beam_width < 1 or result_limit < 1:
        raise ValueError("Beam width and result limit must be positive")

    grammar = NumericPrefixGrammar(rule)
    glyphs = set("0123456789.,+-−")
    if grammar.allowed:
        glyphs.update("".join(rule.allowed_values))
    indices = [(index, character) for index, character in enumerate(characters) if character in glyphs]
    blank_index = 0
    beams: dict[str, tuple[float, float]] = {"": (0.0, NEG_INF)}
    clipped = np.clip(probabilities.astype(np.float64, copy=False), 1e-30, 1.0)
    prefix_cache: dict[str, bool] = {"": True}

    def prefix_allowed(prefix: str) -> bool:
        allowed = prefix_cache.get(prefix)
        if allowed is None:
            allowed = grammar.prefix_allowed(prefix)
            prefix_cache[prefix] = allowed
        return allowed

    for timestep in range(clipped.shape[0]):
        row = np.log(clipped[timestep])
        next_beams: dict[str, tuple[float, float]] = {}

        def add(prefix: str, blank: float = NEG_INF, nonblank: float = NEG_INF) -> None:
            old_blank, old_nonblank = next_beams.get(prefix, (NEG_INF, NEG_INF))
            next_beams[prefix] = (_log_add(old_blank, blank), _log_add(old_nonblank, nonblank))

        for prefix, (prob_blank, prob_nonblank) in beams.items():
            add(prefix, blank=_log_add(prob_blank, prob_nonblank) + float(row[blank_index]))
            for index, character in indices:
                probability = float(row[index])
                if prefix.endswith(character):
                    # Repeating a token without an intervening blank collapses
                    # to the current CTC prefix.  A blank path may emit it as a
                    # genuinely repeated character.
                    add(prefix, nonblank=prob_nonblank + probability)
                    extended = prefix + character
                    if prefix_allowed(extended):
                        add(extended, nonblank=prob_blank + probability)
                else:
                    extended = prefix + character
                    if prefix_allowed(extended):
                        add(extended, nonblank=_log_add(prob_blank, prob_nonblank) + probability)

        ranked = sorted(
            next_beams.items(),
            key=lambda item: _log_add(item[1][0], item[1][1]),
            reverse=True,
        )[:beam_width]
        beams = dict(ranked)

    normalized: dict[str, float] = {}
    for prefix, (prob_blank, prob_nonblank) in beams.items():
        text = grammar.normalize(prefix)
        if not grammar.complete_allowed(text):
            continue
        score = _log_add(prob_blank, prob_nonblank)
        normalized[text] = max(normalized.get(text, NEG_INF), score)
    if not normalized:
        return []
    ordered = sorted(normalized.items(), key=lambda item: item[1], reverse=True)[:result_limit]
    timesteps = max(1, probabilities.shape[0])
    return [
        CtcCandidate(text, score, max(0.0, min(1.0, exp(score / timesteps))))
        for text, score in ordered
    ]
