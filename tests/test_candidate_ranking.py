from math import log

from tablescan_local.candidate_ranking import CandidateEvidence
from tablescan_local.numeric_decoder import CtcCandidate


def reading(value, support):
    return CtcCandidate(value, log(support) * len(value), .99)


def evidence():
    result = CandidateEvidence()
    for model in ("a", "b", "c"):
        result.add(model, "original", [reading("21.4", .8), reading("27.4", .3)])
    return result


def test_balanced_evidence_can_overturn_transformed_view_votes():
    selected, scores, flags = evidence().rank("27.4", ["27.4", "21.4"])
    assert selected == "21.4"
    assert scores["21.4"] > scores["27.4"]
    assert "candidate_reranked" in flags


def test_repeated_identical_view_is_not_an_additional_vote():
    result = evidence()
    baseline = result.rank("27.4", ["27.4", "21.4"])
    for _ in range(50):
        result.add("a", "original", [reading("21.4", .8), reading("27.4", .3)])
    assert result.rank("27.4", ["27.4", "21.4"]) == baseline


def test_weak_constrained_hypothesis_does_not_get_a_full_vote():
    result = evidence()
    result.add("d", "original", [reading("27.4", .0001)])
    selected, _, _ = result.rank("27.4", ["27.4", "21.4"])
    assert selected == "21.4"


def test_close_decision_keeps_value_and_requires_review():
    result = CandidateEvidence()
    for model in ("a", "b"):
        result.add(model, "original", [reading("21.4", .81), reading("27.4", .8)])
    selected, _, flags = result.rank("27.4", ["27.4", "21.4"])
    assert selected == "27.4"
    assert flags == ["candidate_ranking_disagreement"]


def test_ranking_never_invents_candidates():
    result = evidence()
    assert result.rank("27.4", ["27.4"])[0] == "27.4"
    assert result.rank("27.4", ["27.4", "23.4"])[0] != "21.4"


def test_touching_digits_do_not_lock_the_incumbent_length():
    result = CandidateEvidence()
    for model in ("a", "b"):
        result.add(model, "original", [reading("48.2", .3), reading("4.2", .08)])
    result.add("c", "original", [reading("4.2", .0001)])
    selected, _, flags = result.rank("4.2", ["4.2", "48.2"], minimum_digits=2)
    assert selected == "48.2"
    assert "candidate_reranked" in flags and "weak_sequence_evidence" in flags


def test_weak_conflicting_models_cannot_overturn_incumbent():
    result = CandidateEvidence()
    result.add("a", "original", [reading("23.5", .30), reading("53.5", .24), reading("43.5", .05)])
    result.add("b", "original", [reading("43.5", .36), reading("53.5", .13)])
    result.add("c", "original", [reading("53.5", .47), reading("43.5", .25)])
    selected, _, flags = result.rank("43.5", ["43.5", "53.5", "23.5"])
    assert selected == "43.5"
    assert "weak_sequence_evidence" in flags


def test_two_strong_split_readings_resolve_whole_cell_ambiguity():
    result = CandidateEvidence()
    for model in ("a", "b", "c"):
        result.add(model, "original", [reading("51.9", .48), reading("51.4", .42)])
    result.add_split("a", "51.4", .97)
    result.add_split("b", "51.4", .98)
    assert result.rank("51.9", ["51.9", "51.4"])[0] == "51.9"
    selected, _, flags = result.rank("51.9", ["51.9", "51.4"], digit_support={"51.4": .97})
    assert selected == "51.4" and "split_consensus_used" in flags


def test_one_strong_split_plus_weak_readings_cannot_outvote_original():
    result = evidence()
    result.add_split("a", "27.4", .98)
    result.add_split("b", "27.4", .68)
    result.add_split("c", "27.4", .21)
    assert result.rank("21.4", ["21.4", "27.4"])[0] == "21.4"


def test_unchanged_weak_winner_still_reports_ambiguity():
    result = CandidateEvidence()
    for model in ("a", "b"):
        result.add(model, "original", [reading("53.4", .44), reading("54.4", .44)])
    selected, _, flags = result.rank("53.4", ["53.4", "54.4"])
    assert selected == "53.4"
    assert "weak_sequence_evidence" in flags and "candidate_ranking_disagreement" in flags


def test_stable_independent_agreement_does_not_create_a_probability_based_alarm():
    result = CandidateEvidence()
    for model in ("a", "b", "c"):
        result.add(model, "original", [reading("53.4", .40), reading("54.4", .10)])
    selected, _, flags = result.rank("53.4", ["53.4", "54.4"])
    assert selected == "53.4" and flags == []


def test_visibly_separate_digit_cannot_be_dropped_by_final_ranking():
    result = CandidateEvidence()
    for model in ("a", "b"):
        result.add(model, "original", [reading("4.2", .9), reading("48.2", .6)])
    assert result.rank("48.2", ["4.2", "48.2"], minimum_digits=3)[0] == "48.2"


def test_missing_model_evidence_preserves_existing_decision():
    result = CandidateEvidence()
    result.add("a", "original", [reading("21.4", .99)])
    assert result.rank("27.4", ["27.4", "21.4"])[0] == "27.4"
