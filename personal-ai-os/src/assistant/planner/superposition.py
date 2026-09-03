"""Quantum-INSPIRED reasoning: holding many plans at once and letting them
interfere.

READ THIS FIRST - THE HONEST BIT
--------------------------------
Your Mac has no qubits.  Nothing in this file performs quantum computation,
and it would be a lie to say otherwise.  What we borrow is the *shape* of
quantum reasoning, implemented in ordinary arithmetic you can check by hand:

    superposition  ->  keep N candidate strategies alive at once, each with a
                       weight, instead of committing to the first idea
    amplitude      ->  a real-valued utility score per strategy
    probability    ->  softmax over those scores
    interference   ->  strategies that agree with other strong strategies get
                       boosted (constructive); strategies that break a hard
                       rule are zeroed (destructive)
    measurement    ->  choosing one to actually run, and recording why
    decoherence    ->  confidence fading as assumptions age
    Bayesian update->  re-weighting as evidence arrives

Every function here is deterministic given a seed, so the tests can check the
maths exactly.

WHY BOTHER? (the plain-English version)
---------------------------------------
If you ask a person "how should I organise my school year?" a *bad* assistant
grabs the first idea and runs.  A good one thinks of four or five genuinely
different approaches, works out what each would cost you, notices that three
of them share the same sensible first step (that agreement is evidence!), and
then tells you which it picked *and what it nearly picked instead*.  That is
all this file does.
"""
from __future__ import annotations

import math
import random
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence

# --------------------------------------------------------------------------
# What "good" means.  Weights sum to 1.0.
# --------------------------------------------------------------------------
class Criterion(NamedTuple):
    name: str
    weight: float
    higher_is_better: bool = True
    description: str = ""


DEFAULT_CRITERIA: List[Criterion] = [
    Criterion("reliability", 0.25, True, "how likely it is to actually work"),
    Criterion("impact", 0.22, True, "how much of the goal it achieves"),
    Criterion("reversibility", 0.18, True, "how easily a mistake can be undone"),
    Criterion("speed", 0.15, True, "how quickly it finishes"),
    Criterion("user_effort", 0.12, False, "how much work it costs YOU"),
    Criterion("preference_fit", 0.08, True, "how well it matches how you like to work"),
]


class Constraint(NamedTuple):
    """A hard rule.  Breaking it zeroes a strategy - destructive interference."""
    name: str
    #: Returns True when the strategy VIOLATES the constraint.
    violated_by: Callable[["Strategy"], bool]
    explanation: str = ""


class Strategy(NamedTuple):
    """One possible way to reach the goal."""
    id: str
    title: str
    description: str = ""
    #: Short tags for the actions involved, e.g. {"index_files", "make_folders"}.
    #: Used to measure how much two strategies agree.
    actions: Sequence[str] = ()
    #: Each criterion scored 0..1.  Missing criteria default to 0.5 (neutral).
    scores: Optional[Dict[str, float]] = None
    #: Chance each individual step works, 0..1.
    step_success: float = 0.9
    #: How many steps it takes (more steps = more chances to fail).
    steps: int = 3
    notes: str = ""


class Evaluation(NamedTuple):
    strategy: Strategy
    utility: float             # weighted score, 0..1
    amplitude: float           # utility after interference
    probability: float         # softmax over amplitudes, sums to 1 across all
    expected_utility: float    # utility x simulated success, from Monte Carlo
    std_dev: float             # spread of simulated outcomes = volatility
    risk: float                # 0..1, higher = more dangerous
    feasible: bool
    violated: Sequence[str] = ()
    agreement: float = 0.0     # how much other strong strategies agree with it

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.strategy.id,
            "title": self.strategy.title,
            "description": self.strategy.description,
            "utility": round(self.utility, 4),
            "probability": round(self.probability, 4),
            "expected_utility": round(self.expected_utility, 4),
            "risk": round(self.risk, 4),
            "volatility": round(self.std_dev, 4),
            "agreement_with_others": round(self.agreement, 4),
            "feasible": self.feasible,
            "ruled_out_by": list(self.violated),
            "actions": list(self.strategy.actions),
        }


# --------------------------------------------------------------------------
# 1. Utility - how good is this strategy on its own?
# --------------------------------------------------------------------------
def utility_of(strategy: Strategy,
               criteria: Optional[Sequence[Criterion]] = None) -> float:
    """Weighted average of the criterion scores, always between 0 and 1.

    For a criterion where LOWER is better (like the effort it costs you), we
    use ``1 - score`` so that every number points the same way.
    """
    criteria = criteria or DEFAULT_CRITERIA
    scores = strategy.scores or {}
    total_weight = sum(c.weight for c in criteria) or 1.0
    total = 0.0
    for criterion in criteria:
        raw = float(scores.get(criterion.name, 0.5))
        raw = max(0.0, min(1.0, raw))
        value = raw if criterion.higher_is_better else (1.0 - raw)
        total += criterion.weight * value
    return total / total_weight


# --------------------------------------------------------------------------
# 2. Interference - do the strategies agree with each other?
# --------------------------------------------------------------------------
def jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    """How much two sets of actions overlap: 0 = nothing in common, 1 = identical.

    |A ∩ B| / |A ∪ B|.  Two strategies that both start with "index the files"
    share that action, which is weak evidence that indexing is a good idea.
    """
    a, b = set(left), set(right)
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def softmax(values: Sequence[float], temperature: float = 0.35) -> List[float]:
    """Turn scores into probabilities that add up to 1.

    Temperature controls decisiveness: low temperature makes the best option
    dominate; high temperature keeps the field open.  We subtract the maximum
    before exponentiating, which changes nothing mathematically but stops
    large numbers overflowing.
    """
    if not values:
        return []
    temperature = max(1e-6, float(temperature))
    scaled = [v / temperature for v in values]
    peak = max(scaled)
    exponentials = [math.exp(v - peak) for v in scaled]
    total = sum(exponentials) or 1.0
    return [e / total for e in exponentials]


# --------------------------------------------------------------------------
# 3. Monte Carlo - what actually happens if we run it many times?
# --------------------------------------------------------------------------
def simulate(strategy: Strategy, utility: float, runs: int = 300,
             rng: Optional[random.Random] = None) -> Dict[str, float]:
    """Roll the dice `runs` times to estimate the average result AND the spread.

    Each step succeeds with probability ``step_success``.  A run that completes
    every step earns the full utility; a run that fails partway earns partial
    credit for how far it got.  We care about the spread as much as the
    average: two strategies can have the same average while one is steady and
    the other is a coin flip - and you usually want the steady one.
    """
    rng = rng or random.Random(0)
    steps = max(1, int(strategy.steps))
    probability = max(0.0, min(1.0, float(strategy.step_success)))

    outcomes: List[float] = []
    completions = 0
    for _ in range(max(1, int(runs))):
        completed = 0
        for _ in range(steps):
            if rng.random() <= probability:
                completed += 1
            else:
                break
        fraction = completed / steps
        if completed == steps:
            completions += 1
        # Partial progress is worth something, but much less than finishing.
        outcomes.append(utility * (1.0 if fraction == 1.0 else 0.4 * fraction))

    mean = sum(outcomes) / len(outcomes)
    variance = sum((o - mean) ** 2 for o in outcomes) / len(outcomes)
    return {
        "expected_utility": mean,
        "std_dev": math.sqrt(variance),
        "completion_rate": completions / len(outcomes),
    }


# --------------------------------------------------------------------------
# 4. The whole pipeline
# --------------------------------------------------------------------------
def evaluate(
    strategies: Sequence[Strategy],
    criteria: Optional[Sequence[Criterion]] = None,
    constraints: Optional[Sequence[Constraint]] = None,
    *,
    temperature: float = 0.35,
    interference_strength: float = 0.35,
    simulations: int = 300,
    seed: Optional[int] = 0,
) -> List[Evaluation]:
    """Score every strategy, let them interfere, and rank them.

    Returns evaluations sorted best-first.  Infeasible strategies are kept in
    the list (with probability 0) so you can see what was ruled out and why -
    silently dropping options would hide the reasoning.
    """
    criteria = criteria or DEFAULT_CRITERIA
    constraints = constraints or []
    rng = random.Random(seed if seed is not None else 0)

    # -- step 1: base utility, and hard constraints -------------------------
    utilities: List[float] = []
    violations: List[List[str]] = []
    for strategy in strategies:
        broken = [c.name for c in constraints if c.violated_by(strategy)]
        violations.append(broken)
        utilities.append(0.0 if broken else utility_of(strategy, criteria))

    # -- step 2: a first pass at probability, used to weight the agreement --
    prior = softmax(utilities, temperature)

    # -- step 3: interference ----------------------------------------------
    # A strategy gains weight when OTHER strategies that are themselves strong
    # propose similar actions. Convergent thinking is evidence.
    amplitudes: List[float] = []
    agreements: List[float] = []
    for i, strategy in enumerate(strategies):
        if violations[i]:
            amplitudes.append(0.0)       # destructive: the option is annihilated
            agreements.append(0.0)
            continue
        agreement = 0.0
        for j, other in enumerate(strategies):
            if i == j or violations[j]:
                continue
            agreement += prior[j] * jaccard(strategy.actions, other.actions)
        agreements.append(agreement)
        amplitudes.append(utilities[i] * (1.0 + interference_strength * agreement))

    probabilities = softmax(amplitudes, temperature)
    # An impossible option must have probability exactly zero, not a small
    # softmax residue - so zero them and re-normalise the rest.
    for i, broken in enumerate(violations):
        if broken:
            probabilities[i] = 0.0
    total = sum(probabilities) or 1.0
    probabilities = [p / total for p in probabilities]

    # -- step 4: simulate --------------------------------------------------
    results: List[Evaluation] = []
    for i, strategy in enumerate(strategies):
        if violations[i]:
            results.append(Evaluation(
                strategy, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, False,
                violations[i], 0.0))
            continue
        rolled = simulate(strategy, utilities[i], runs=simulations,
                          rng=random.Random(rng.randint(0, 2**31)))
        # Risk combines two different bad things: the chance it does not
        # finish, and how unpredictable the outcome is.
        risk = min(1.0, (1.0 - rolled["completion_rate"]) * 0.7
                   + min(1.0, rolled["std_dev"] * 2.0) * 0.3)
        results.append(Evaluation(
            strategy=strategy,
            utility=utilities[i],
            amplitude=amplitudes[i],
            probability=probabilities[i],
            expected_utility=rolled["expected_utility"],
            std_dev=rolled["std_dev"],
            risk=risk,
            feasible=True,
            violated=(),
            agreement=agreements[i],
        ))

    results.sort(key=lambda e: (e.feasible, e.expected_utility), reverse=True)
    return results


class Choice(NamedTuple):
    chosen: Optional[Evaluation]
    runner_up: Optional[Evaluation]
    ranked: Sequence[Evaluation]
    reason: str
    forced_by_risk: bool = False

    def explain(self) -> str:
        """A paragraph a human can read and disagree with."""
        if not self.chosen:
            return "No workable strategy was found. " + self.reason
        lines = [
            "Chosen: %s" % self.chosen.strategy.title,
            "  expected value %.2f, risk %.2f, confidence %.0f%%"
            % (self.chosen.expected_utility, self.chosen.risk,
               100 * self.chosen.probability),
            "  why: %s" % self.reason,
        ]
        if self.runner_up:
            lines.append(
                "Runner-up: %s (expected value %.2f, risk %.2f) - say so if you "
                "would rather do that."
                % (self.runner_up.strategy.title,
                   self.runner_up.expected_utility, self.runner_up.risk))
        ruled_out = [e for e in self.ranked if not e.feasible]
        if ruled_out:
            lines.append("Ruled out: " + "; ".join(
                "%s (breaks: %s)" % (e.strategy.title, ", ".join(e.violated))
                for e in ruled_out))
        return "\n".join(lines)


def collapse(evaluations: Sequence[Evaluation], risk_ceiling: float = 0.65) -> Choice:
    """'Measurement': pick one strategy to actually run.

    We take the highest expected value among the options whose risk is under
    the ceiling.  If everything is above the ceiling we do NOT refuse - we pick
    the safest option available and say clearly that we had to.
    """
    feasible = [e for e in evaluations if e.feasible]
    if not feasible:
        return Choice(None, None, evaluations,
                      "every candidate broke a hard rule", False)

    acceptable = [e for e in feasible if e.risk <= risk_ceiling]
    forced = False
    if acceptable:
        pool = sorted(acceptable, key=lambda e: -e.expected_utility)
        reason = ("it has the best expected outcome (%.2f) among the options "
                  "whose risk is under your limit of %.2f"
                  % (pool[0].expected_utility, risk_ceiling))
    else:
        pool = sorted(feasible, key=lambda e: (e.risk, -e.expected_utility))
        forced = True
        reason = ("every option exceeds your risk limit of %.2f, so this is "
                  "the least risky one (risk %.2f). Consider narrowing the "
                  "goal." % (risk_ceiling, pool[0].risk))

    return Choice(pool[0], pool[1] if len(pool) > 1 else None,
                  evaluations, reason, forced)


# --------------------------------------------------------------------------
# 5. Bayesian updating - learning from what actually happened
# --------------------------------------------------------------------------
def bayesian_update(strategy: Strategy, successes: int, failures: int,
                    prior_strength: float = 4.0) -> Strategy:
    """Revise a strategy's success estimate using real evidence.

    This is the Beta-Binomial update, the standard way to learn a probability
    from yes/no outcomes:

        new estimate = (prior_successes + observed_successes)
                       ---------------------------------------
                       (prior_total     + observed_total)

    ``prior_strength`` is how much we trusted the original guess, expressed as
    "it is worth this many observations".  With strength 4 and an initial
    guess of 0.9, one real failure moves the estimate to about 0.72 - a big
    update, but not a panic.
    """
    prior = max(0.0, min(1.0, float(strategy.step_success)))
    alpha = prior * prior_strength
    beta = (1.0 - prior) * prior_strength
    posterior = (alpha + max(0, successes)) / (
        alpha + beta + max(0, successes) + max(0, failures))
    return strategy._replace(step_success=round(posterior, 4))


def decohere(strategy: Strategy, age_days: float, half_life_days: float = 30.0) -> Strategy:
    """'Decoherence': a plan built on old assumptions is less trustworthy.

    Same exponential decay as memory confidence. A plan made three months ago
    about a folder you have since reorganised should not be executed with the
    same certainty as one made this morning.
    """
    if half_life_days <= 0 or age_days <= 0:
        return strategy
    factor = math.pow(0.5, age_days / half_life_days)
    # Decay toward 0.5 (pure uncertainty), not toward 0 (certain failure).
    decayed = 0.5 + (strategy.step_success - 0.5) * factor
    return strategy._replace(step_success=round(max(0.0, min(1.0, decayed)), 4))
