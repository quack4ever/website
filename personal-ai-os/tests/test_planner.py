"""Planner tests.

These check the ARITHMETIC, not the vibes. If the "quantum-inspired" layer is
real, its numbers must be reproducible and hand-checkable.
"""
from __future__ import annotations

import math

import pytest

from assistant.planner import beam, superposition as sp


def strategy(sid, actions=(), scores=None, success=0.9, steps=3, title=None):
    return sp.Strategy(id=sid, title=title or sid.title(), actions=actions,
                       scores=scores or {}, step_success=success, steps=steps)


# ===========================================================================
# Utility - checkable by hand
# ===========================================================================
def test_neutral_scores_give_neutral_utility():
    all_half = {c.name: 0.5 for c in sp.DEFAULT_CRITERIA}
    assert sp.utility_of(strategy("s", scores=all_half)) == pytest.approx(0.5)


def test_perfect_scores_account_for_inverted_criteria():
    """All 1.0 means 'maximum effort required', which is BAD - so not 1.0.

    weights: reliability .25 + impact .22 + reversibility .18 + speed .15
             + preference_fit .08 = 0.88, and user_effort (.12) inverts to 0.
    """
    all_one = {c.name: 1.0 for c in sp.DEFAULT_CRITERIA}
    assert sp.utility_of(strategy("s", scores=all_one)) == pytest.approx(0.88)


def test_low_effort_is_better_than_high_effort():
    easy = strategy("easy", scores={"user_effort": 0.0})
    hard = strategy("hard", scores={"user_effort": 1.0})
    assert sp.utility_of(easy) > sp.utility_of(hard)


def test_missing_scores_default_to_neutral():
    assert sp.utility_of(strategy("s")) == pytest.approx(0.5)


def test_scores_outside_the_range_are_clamped():
    wild = strategy("s", scores={c.name: 99.0 for c in sp.DEFAULT_CRITERIA})
    assert 0.0 <= sp.utility_of(wild) <= 1.0


# ===========================================================================
# Maths helpers
# ===========================================================================
def test_jaccard_is_correct():
    assert sp.jaccard(["a", "b"], ["a", "b"]) == 1.0
    assert sp.jaccard(["a", "b"], ["c", "d"]) == 0.0
    assert sp.jaccard(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)
    assert sp.jaccard([], []) == 0.0


def test_softmax_sums_to_one_and_ranks_correctly():
    out = sp.softmax([0.1, 0.5, 0.9])
    assert sum(out) == pytest.approx(1.0)
    assert out[2] > out[1] > out[0]


def test_softmax_survives_extreme_values():
    """Naive exp() would overflow here; subtracting the max prevents it."""
    out = sp.softmax([1000.0, 999.0, -1000.0], temperature=0.01)
    assert sum(out) == pytest.approx(1.0)
    assert all(math.isfinite(v) for v in out)


# ===========================================================================
# Constraints = destructive interference
# ===========================================================================
def test_a_broken_hard_rule_annihilates_a_strategy():
    never_delete = sp.Constraint(
        "never delete", lambda s: "delete_files" in s.actions,
        "the user said never delete anything")
    good = strategy("good", actions=["move_files"], scores={"impact": 1.0})
    bad = strategy("bad", actions=["delete_files"], scores={"impact": 1.0})

    results = {e.strategy.id: e for e in
               sp.evaluate([good, bad], constraints=[never_delete])}
    assert results["bad"].feasible is False
    assert results["bad"].probability == 0.0
    assert "never delete" in results["bad"].violated
    assert results["good"].probability == pytest.approx(1.0)


def test_probabilities_still_sum_to_one_after_options_are_removed():
    constraint = sp.Constraint("no", lambda s: s.id == "c", "")
    results = sp.evaluate([strategy("a"), strategy("b"), strategy("c")],
                          constraints=[constraint])
    assert sum(e.probability for e in results) == pytest.approx(1.0)


# ===========================================================================
# Constructive interference
# ===========================================================================
def test_agreeing_strategies_gain_weight():
    """Two approaches proposing the same actions reinforce each other.

    All three have identical utility, so any difference in the final
    probability comes purely from interference.
    """
    scores = {"impact": 0.8}
    agreeing_a = strategy("a", actions=["index", "sort"], scores=scores)
    agreeing_b = strategy("b", actions=["index", "sort"], scores=scores)
    lone = strategy("c", actions=["rewrite", "email"], scores=scores)

    results = {e.strategy.id: e for e in
               sp.evaluate([agreeing_a, agreeing_b, lone], seed=1)}
    assert results["a"].agreement > results["c"].agreement
    assert results["a"].probability > results["c"].probability
    assert results["b"].probability > results["c"].probability


def test_interference_cannot_rescue_a_bad_idea():
    """Agreement is a nudge, not a veto over quality."""
    strong = strategy("strong", actions=["unique"], scores={c.name: 1.0
                      for c in sp.DEFAULT_CRITERIA if c.higher_is_better})
    weak_a = strategy("weak_a", actions=["same"], scores={"impact": 0.05,
                      "reliability": 0.05, "speed": 0.05, "reversibility": 0.05})
    weak_b = strategy("weak_b", actions=["same"], scores={"impact": 0.05,
                      "reliability": 0.05, "speed": 0.05, "reversibility": 0.05})
    results = {e.strategy.id: e for e in sp.evaluate([strong, weak_a, weak_b])}
    assert results["strong"].probability > results["weak_a"].probability


# ===========================================================================
# Monte Carlo simulation
# ===========================================================================
def test_more_steps_means_lower_completion_rate():
    short = sp.simulate(strategy("s", success=0.8, steps=2), 1.0, runs=4000,
                        rng=__import__("random").Random(7))
    long = sp.simulate(strategy("l", success=0.8, steps=8), 1.0, runs=4000,
                       rng=__import__("random").Random(7))
    assert short["completion_rate"] > long["completion_rate"]
    # 0.8^2 = 0.64 and 0.8^8 = 0.168 - the simulation should land near those.
    assert short["completion_rate"] == pytest.approx(0.64, abs=0.05)
    assert long["completion_rate"] == pytest.approx(0.168, abs=0.05)


def test_a_certain_strategy_has_no_volatility():
    result = sp.simulate(strategy("s", success=1.0, steps=5), 0.9, runs=200)
    assert result["completion_rate"] == 1.0
    assert result["std_dev"] == pytest.approx(0.0)


def test_evaluation_is_deterministic_for_a_given_seed():
    options = [strategy("a", actions=["x"]), strategy("b", actions=["y"])]
    first = [e.expected_utility for e in sp.evaluate(options, seed=42)]
    second = [e.expected_utility for e in sp.evaluate(options, seed=42)]
    assert first == second


# ===========================================================================
# Collapse (choosing)
# ===========================================================================
def test_collapse_prefers_the_best_expected_outcome():
    good = strategy("good", scores={c.name: 0.9 for c in sp.DEFAULT_CRITERIA
                                            if c.higher_is_better}, success=0.99)
    poor = strategy("poor", scores={"impact": 0.2}, success=0.99)
    choice = sp.collapse(sp.evaluate([good, poor], seed=3))
    assert choice.chosen.strategy.id == "good"
    assert choice.runner_up.strategy.id == "poor"


def test_collapse_avoids_options_over_the_risk_ceiling():
    risky = strategy("risky", scores={c.name: 1.0 for c in sp.DEFAULT_CRITERIA
                                              if c.higher_is_better},
                     success=0.35, steps=8)
    safe = strategy("safe", scores={"impact": 0.6, "reliability": 0.6},
                    success=0.99, steps=2)
    choice = sp.collapse(sp.evaluate([risky, safe], seed=5), risk_ceiling=0.5)
    assert choice.chosen.strategy.id == "safe"
    assert choice.forced_by_risk is False


def test_when_everything_is_risky_we_say_so_instead_of_refusing():
    a = strategy("a", success=0.2, steps=9)
    b = strategy("b", success=0.25, steps=9)
    choice = sp.collapse(sp.evaluate([a, b], seed=5), risk_ceiling=0.1)
    assert choice.chosen is not None
    assert choice.forced_by_risk is True
    assert "exceeds your risk limit" in choice.reason


def test_collapse_reports_when_nothing_is_possible():
    blocker = sp.Constraint("blocked", lambda s: True, "everything is forbidden")
    choice = sp.collapse(sp.evaluate([strategy("a")], constraints=[blocker]))
    assert choice.chosen is None
    assert "hard rule" in choice.reason


def test_explanation_names_the_runner_up_and_the_rejects():
    never = sp.Constraint("never delete", lambda s: "delete" in s.actions, "")
    options = [strategy("a", actions=["move"], scores={"impact": 0.9}),
               strategy("b", actions=["copy"], scores={"impact": 0.5}),
               strategy("c", actions=["delete"], scores={"impact": 1.0})]
    text = sp.collapse(sp.evaluate(options, constraints=[never])).explain()
    assert "Chosen:" in text and "Runner-up:" in text
    assert "Ruled out:" in text and "never delete" in text


# ===========================================================================
# Learning from evidence
# ===========================================================================
def test_bayesian_update_moves_toward_observed_reality():
    optimistic = strategy("s", success=0.9)
    after_failures = sp.bayesian_update(optimistic, successes=0, failures=5)
    assert after_failures.step_success < 0.5
    after_successes = sp.bayesian_update(optimistic, successes=20, failures=0)
    assert after_successes.step_success > 0.9


def test_bayesian_update_is_the_beta_binomial_formula():
    """(0.9*4 + 1) / (4 + 1 + 1) = 4.6/6 = 0.7667 - check it exactly."""
    updated = sp.bayesian_update(strategy("s", success=0.9),
                                 successes=1, failures=1, prior_strength=4.0)
    assert updated.step_success == pytest.approx(0.7667, abs=0.0005)


def test_one_failure_does_not_cause_a_panic():
    updated = sp.bayesian_update(strategy("s", success=0.9), 0, 1)
    assert 0.6 < updated.step_success < 0.8


def test_decoherence_fades_confidence_toward_uncertainty_not_toward_zero():
    fresh = strategy("s", success=0.95)
    stale = sp.decohere(fresh, age_days=300, half_life_days=30)
    assert stale.step_success < 0.6
    assert stale.step_success >= 0.5, "should decay toward 0.5, not toward 0"
    assert sp.decohere(fresh, age_days=0).step_success == 0.95


# ===========================================================================
# Beam search
# ===========================================================================
def _toy_problem():
    """A deliberate trap: the greedy first move leads to a worse total."""
    options = {
        "start": [beam.Step("cheap_now", gain=5.0, cost=1.0),
                  beam.Step("invest", gain=1.0, cost=1.0)],
        "cheap_now": [beam.Step("dead_end", gain=0.5, cost=1.0)],
        "invest": [beam.Step("big_payoff", gain=20.0, cost=1.0)],
    }

    def expand(state, steps):
        return options.get(state.get("at", "start"), [])

    def apply(state, step):
        state["at"] = step.title
        return state

    def is_goal(state, steps):
        return state.get("at") in ("dead_end", "big_payoff")

    return expand, apply, is_goal


def test_beam_search_beats_greedy_on_a_delayed_payoff():
    expand, apply, is_goal = _toy_problem()
    greedy = beam.search({"at": "start"}, expand, apply, is_goal=is_goal,
                         beam_width=1, max_depth=3)
    wide = beam.search({"at": "start"}, expand, apply, is_goal=is_goal,
                       beam_width=3, max_depth=3)
    assert [s.title for s in greedy[0].steps] == ["cheap_now", "dead_end"]
    assert [s.title for s in wide[0].steps] == ["invest", "big_payoff"]
    assert wide[0].score > greedy[0].score


def test_beam_search_respects_its_node_budget():
    def expand(state, steps):
        return [beam.Step("s%d" % i, gain=1.0) for i in range(20)]

    def apply(state, step):
        return state
    results = beam.search({}, expand, apply, beam_width=3, max_depth=10,
                          max_nodes=100)
    assert results  # returns the best partial plans rather than nothing


def test_beam_search_never_returns_nothing():
    def expand(state, steps):
        return []

    def apply(state, step):
        return state
    assert beam.search({"a": 1}, expand, apply) != []
