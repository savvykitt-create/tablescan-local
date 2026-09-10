import cv2
import numpy as np

from tablescan_local.domain import CellResult, NormalizedRect, PageResult, TableTemplate
from tablescan_local.digit_verifier import DigitVerification, NumericGeometry
from tablescan_local.writer_adapter import WriterStyleProfile, apply_writer_adaptation


def glyph(value: str, shift: int = 0) -> np.ndarray:
    canvas = np.zeros((28, 28), np.uint8)
    cv2.putText(canvas, value, (4 + shift, 23), cv2.FONT_HERSHEY_SIMPLEX, .85, 255, 2, cv2.LINE_AA)
    return canvas


def style_profile() -> WriterStyleProfile:
    profile = WriterStyleProfile()
    for index, shift in enumerate((-1, 0, 1)):
        profile.add((0, index + 10, 1), "4", glyph("4", shift))
        profile.add((0, index + 10, 2), "9", glyph("9", shift))
    return profile


class SupportingVerifier:
    def verify(self, _crop, candidates, _separator_x, _separator_width):
        support = {"49.1": .86, "99.1": .42, "19.1": .18}
        return [
            DigitVerification(value, support.get(value, .1), "491", .91)
            for value in sorted(candidates, key=lambda item: support.get(item, .1), reverse=True)
        ]


class ThirdCandidateVerifier(SupportingVerifier):
    def verify(self, _crop, candidates, _separator_x, _separator_width):
        support = {"19.1": .95, "49.1": .86, "99.1": .42}
        return [
            DigitVerification(value, support.get(value, .1), "191", .93)
            for value in sorted(candidates, key=lambda item: support.get(item, .1), reverse=True)
        ]


def test_page_style_ranks_any_candidate_digits_without_pair_rules():
    profile = style_profile()
    ranking = profile.rank(
        [glyph("4"), glyph("9"), glyph("1")],
        ["49.1", "99.1"],
        variable_positions=[0],
        exclude=(0, 99, 99),
    )

    assert ranking is not None
    assert ranking.winner == "49.1"
    assert ranking.evidence_cells == 3
    assert ranking.margin > 0


def test_repeated_glyphs_in_one_cell_are_one_source_and_self_is_excluded():
    profile = WriterStyleProfile()
    for shift in (-1, 0, 1):
        profile.add((0, 1, 1), "4", glyph("4", shift))
    profile.add((0, 2, 1), "4", glyph("4"))

    assert profile.source_count("4") == 2
    assert profile.source_count("4", exclude=(0, 1, 1)) == 1
    assert profile.support(glyph("4"), "4", exclude=(0, 1, 1)) is None


def test_writer_pass_suggests_only_existing_candidate_and_keeps_value_unchanged(monkeypatch):
    template = TableTemplate(
        "t", "t", NormalizedRect(0, 0, 1, 1),
        row_guides=[0, 1], column_guides=[0, .2, .6, 1], row_label_columns=1,
    )
    template.ensure_column_rules()
    target = CellResult(
        0, 1, "99.1", "99.1", .99,
        flags=["high_accuracy_consensus"],
        alternatives="49.1 | 19.1",
        applied_rule="Measurements",
        candidate_scores={"99.1": 5.0, "49.1": 3.0, "19.1": 1.0},
    )
    page = PageResult(0, "source.png", [target], [])
    profile = style_profile()
    monkeypatch.setattr("tablescan_local.writer_adapter.build_writer_profile", lambda *_: profile)
    monkeypatch.setattr("tablescan_local.writer_adapter._prepared_crop", lambda *_: np.ones((2, 2, 3), np.uint8))
    monkeypatch.setattr(
        "tablescan_local.writer_adapter._segmented",
        lambda *_: [glyph("4"), glyph("9"), glyph("1")],
    )
    monkeypatch.setattr(
        "tablescan_local.writer_adapter.infer_numeric_geometry",
        lambda *_: NumericGeometry(3, 1, 1, .9, True),
    )

    assert apply_writer_adaptation(page, template, SupportingVerifier()) == 0
    assert target.final_text == "99.1"
    assert target.writer_suggestion == "49.1"
    assert "writer_style_ambiguous" in target.flags
    assert "writer_style_selected" not in target.flags
    assert target.needs_review
    assert "3 independent cells" in target.writer_evidence


def test_unrelated_verifier_winner_vetoes_pairwise_style_rewrite(monkeypatch):
    template = TableTemplate(
        "t", "t", NormalizedRect(0, 0, 1, 1),
        row_guides=[0, 1], column_guides=[0, .2, .6, 1], row_label_columns=1,
    )
    template.ensure_column_rules()
    target = CellResult(
        0, 1, "99.1", "99.1", .99,
        flags=["high_accuracy_consensus"], alternatives="49.1 | 19.1",
        applied_rule="Measurements",
        candidate_scores={"99.1": 5.0, "49.1": 3.0, "19.1": 1.0},
    )
    page = PageResult(0, "source.png", [target], [])
    monkeypatch.setattr("tablescan_local.writer_adapter.build_writer_profile", lambda *_: style_profile())
    monkeypatch.setattr("tablescan_local.writer_adapter._prepared_crop", lambda *_: np.ones((2, 2, 3), np.uint8))
    monkeypatch.setattr(
        "tablescan_local.writer_adapter._segmented",
        lambda *_: [glyph("4"), glyph("9"), glyph("1")],
    )
    monkeypatch.setattr(
        "tablescan_local.writer_adapter.infer_numeric_geometry",
        lambda *_: NumericGeometry(3, 1, 1, .9, True),
    )

    assert apply_writer_adaptation(page, template, ThirdCandidateVerifier()) == 0
    assert target.final_text == "99.1"
    assert not target.writer_suggestion
    assert "writer_style_selected" not in target.flags


def test_shape_similarity_tolerates_one_pixel_stroke_shift():
    profile = WriterStyleProfile()
    left = profile._mask(glyph("4", -1))
    right = profile._mask(glyph("4", 1))
    different = profile._mask(glyph("9"))

    assert profile._shape_similarity(left, right) > profile._shape_similarity(left, different)


def test_two_style_sources_can_flag_but_cannot_rewrite(monkeypatch):
    template = TableTemplate(
        "t", "t", NormalizedRect(0, 0, 1, 1),
        row_guides=[0, 1], column_guides=[0, .2, 1], row_label_columns=1,
    )
    template.ensure_column_rules()
    target = CellResult(
        0, 1, "99.1", "99.1", .99,
        flags=["high_accuracy_consensus"], alternatives="49.1",
        applied_rule="Measurements", candidate_scores={"99.1": 5.0, "49.1": 3.0},
    )
    page = PageResult(0, "source.png", [target], [])
    profile = WriterStyleProfile()
    for index, shift in enumerate((-1, 1)):
        profile.add((0, index + 1, 1), "4", glyph("4", shift))
        profile.add((0, index + 1, 2), "9", glyph("9", shift))
    monkeypatch.setattr("tablescan_local.writer_adapter.build_writer_profile", lambda *_: profile)
    monkeypatch.setattr("tablescan_local.writer_adapter._prepared_crop", lambda *_: np.ones((2, 2, 3), np.uint8))
    monkeypatch.setattr(
        "tablescan_local.writer_adapter._segmented",
        lambda *_: [glyph("4"), glyph("9"), glyph("1")],
    )
    monkeypatch.setattr(
        "tablescan_local.writer_adapter.infer_numeric_geometry",
        lambda *_: NumericGeometry(3, 1, 1, .9, True),
    )

    assert apply_writer_adaptation(page, template, SupportingVerifier()) == 0
    assert target.final_text == "99.1"
    assert "writer_style_ambiguous" in target.flags
    assert target.needs_review


def test_writer_pass_never_invents_a_missing_candidate(monkeypatch):
    template = TableTemplate(
        "t", "t", NormalizedRect(0, 0, 1, 1),
        row_guides=[0, 1], column_guides=[0, .2, 1], row_label_columns=1,
    )
    template.ensure_column_rules()
    target = CellResult(
        0, 1, "99.1", "99.1", .99,
        flags=["high_accuracy_consensus"], applied_rule="Measurements",
        candidate_scores={"99.1": 5.0},
    )
    page = PageResult(0, "source.png", [target], [])
    monkeypatch.setattr("tablescan_local.writer_adapter.build_writer_profile", lambda *_: style_profile())

    assert apply_writer_adaptation(page, template) == 0
    assert target.final_text == "99.1"
    assert not target.writer_evidence
