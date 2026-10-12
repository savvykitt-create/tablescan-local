"""Experimental early exit; agreement is evidence, not calibrated certainty."""
import math
import re

from .digit_verifier import infer_numeric_geometry


POLICY = 'agreement-v1-review-required'


def recognize(engine, crop, constraints, retry_crops):
    from .ocr import (OcrValue, canonical_numeric, remove_edge_rules,
                      isolate_blue_ink, detect_decimal_separator)
    cache = {}

    def complete():
        value = engine._recognize_numeric_slow(crop, constraints, retry_crops,
                                                recognition_cache=cache)
        value.flags = [*(value.flags or []), 'adaptive_full_pass']
        return value

    if constraints.value_format not in {'numeric', 'integer'}:
        return complete()
    clean = remove_edge_rules(crop)
    ink = isolate_blue_ink(clean)
    readings = []
    hypotheses_seen = []
    for view in (clean, ink):
        prepared = engine._prepare(view)
        key = (prepared.shape, prepared.tobytes())
        for name, model in (('ppocrv5', engine._engine), ('ppocrv6', engine._precision_engine)):
            cache_key = (name, *key)
            if cache_key not in cache:
                cache[cache_key] = engine._recognize_hypotheses(prepared, model, constraints)
            raw, confidence, hypotheses, _ = cache[cache_key]
            value = canonical_numeric(raw)
            # Neither a grammar-constrained beam nor confidence alone can
            # establish agreement. Keep literal readings and close competitors.
            if (not re.fullmatch(r'\d+(?:\.\d+)?', value)
                    or not math.isfinite(confidence) or confidence < .95
                    or constraints.hard_errors(value) or constraints.warnings(value)
                    or not hypotheses or canonical_numeric(hypotheses[0].text) != value):
                return complete()
            best = hypotheses[0].log_probability
            if not math.isfinite(best) or any(
                    not math.isfinite(item.log_probability)
                    or best - item.log_probability < math.log(10)
                    for item in hypotheses[1:] if canonical_numeric(item.text) != value):
                return complete()
            readings.append((value, confidence, raw))
            hypotheses_seen.extend(hypotheses)
    value = readings[0][0]
    if any(item[0] != value for item in readings):
        return complete()
    fraction = len(value.split('.')[1]) if '.' in value else None
    if fraction:
        separator = detect_decimal_separator(ink, fraction)
        if separator is None or separator.kind != 'mark' or separator.confidence < .7:
            return complete()
    digits = sum(character.isdigit() for character in value)
    for source in (crop, *retry_crops):
        geometry = infer_numeric_geometry(isolate_blue_ink(remove_edge_rules(source)), fraction)
        if geometry is not None and geometry.confidence >= .6 and geometry.digit_count != digits:
            return complete()
    candidates = list(dict.fromkeys([value, *(canonical_numeric(h.text) for h in hypotheses_seen)]))
    # Early exits stay in Review until independently validated. Full-cascade
    # decisions retain their existing policy. This marker is never confidence.
    return OcrValue(value, min(item[1] for item in readings),
                    ' | '.join(candidates[1:])[:1000],
                    ['adaptive_short_pass', 'adaptive_review_required', 'numeric_verification_required'],
                    readings[0][2], candidates,
                    {value: min(item[1] for item in readings)})
