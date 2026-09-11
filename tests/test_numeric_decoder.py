import numpy as np

from tablescan_local.constraints import ValueConstraints
from tablescan_local.numeric_decoder import NumericPrefixGrammar, ctc_prefix_beam_search


def test_forced_ctc_scores_match_exhaustive_paths_with_repeats_and_aliases():
    from itertools import product
    from math import exp
    from tablescan_local.numeric_decoder import CtcSequence
    characters = ["blank", "4", "7", ".", ",", "X"]
    rng = np.random.default_rng(137)
    probabilities = rng.dirichlet(np.ones(len(characters)), size=5)
    expected = {v: 0.0 for v in ["44", "4.7", "47", "7.4"]}
    for path in product(range(len(characters)), repeat=5):
        text = "".join(characters[c] for i, c in enumerate(path) if c and (not i or path[i-1] != c)).replace(",", ".")
        if text in expected:
            expected[text] += float(np.prod(probabilities[np.arange(5), path]))
    actual = CtcSequence.from_output(probabilities, characters).log_likelihoods(list(expected))
    for value in expected:
        np.testing.assert_allclose(exp(actual[value]), expected[value], rtol=1e-12)


def test_forced_ctc_can_score_candidate_missing_from_beam_without_renormalizing():
    from math import exp
    from tablescan_local.numeric_decoder import CtcSequence
    sequence = CtcSequence.from_output(np.array([[.01, .08, .01, .90]]), ["blank", "1", "7", "X"])
    scores = sequence.log_likelihoods(["1", "7", "77"])
    np.testing.assert_allclose(exp(scores["7"]), .01)
    assert scores["77"] == -np.inf


def rule(**overrides):
    values = dict(value_format="numeric", minimum=0, maximum=100, decimal_places=1)
    values.update(overrides)
    return ValueConstraints(**values)


def test_prefix_grammar_prunes_impossible_numeric_shapes():
    grammar = NumericPrefixGrammar(rule())
    assert grammar.prefix_allowed("34.")
    assert grammar.prefix_allowed("34.4")
    assert not grammar.prefix_allowed("-3")
    assert not grammar.prefix_allowed("34.44")
    assert not grammar.prefix_allowed("I34")
    assert grammar.complete_allowed("34,4")
    assert not grammar.complete_allowed("344")


def test_constrained_beam_recovers_non_greedy_decimal():
    # Greedy decoding is 344 because blank wins at the third timestep.  The
    # exact-one-decimal grammar keeps the weaker, physically scored dot path.
    characters = ["blank", "3", "4", ".", "I"]
    probabilities = np.array([
        [.04, .90, .02, .02, .02],
        [.04, .02, .90, .02, .02],
        [.52, .01, .01, .44, .02],
        [.04, .02, .90, .02, .02],
    ])
    result = ctc_prefix_beam_search(probabilities, characters, rule(), beam_width=24)
    assert result
    assert result[0].text == "34.4"
    assert all(candidate.text != "344" for candidate in result)


def test_allowlist_is_used_as_an_exact_decoding_trie():
    characters = ["blank", "L", "R", "e", "f", "t"]
    probabilities = np.full((4, len(characters)), .01)
    probabilities[:, 0] = .20
    probabilities[0, 1] = .70
    probabilities[1, 2] = .70
    probabilities[2, 3] = .70
    probabilities[3, 4] = .70
    constraints = ValueConstraints(value_format="text", allowed_values=["Left"])
    # The decoder can also recover a weak but physically scored member of a
    # small enumeration; it cannot emit anything outside the declared list.
    result = ctc_prefix_beam_search(probabilities, characters, constraints, beam_width=16)
    assert [candidate.text for candidate in result] == ["Left"]


def test_integer_grammar_never_emits_separator():
    characters = ["blank", "1", "2", "."]
    probabilities = np.array([[.01, .90, .04, .05], [.01, .04, .90, .05]])
    constraints = ValueConstraints(value_format="integer", minimum=0, maximum=99)
    result = ctc_prefix_beam_search(probabilities, characters, constraints)
    assert result[0].text == "12"
    assert all("." not in candidate.text for candidate in result)
