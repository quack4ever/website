"""Beam search: exploring multi-step plans without exploring everything.

THE PROBLEM
-----------
Say each step of a plan has 5 possible next steps, and a plan is 6 steps long.
Checking every path means 5^6 = 15,625 plans.  At 8 steps it is 390,625.  The
tree explodes.

THE FIX, IN ONE SENTENCE
------------------------
At every level, keep only the best few partial plans and throw the rest away.

ANALOGY
-------
You are planning a route across a city.  A perfectionist checks every possible
turn.  A sensible person says "I'll consider the three most promising routes
so far, and at each junction I'll extend those three" - carrying a *beam* of
candidates forward instead of one guess or all of them.

That is beam search.  ``beam_width`` is how many you carry.  Width 1 is greedy
(fast, often wrong); infinite width is exhaustive (correct, impossibly slow);
3 to 5 is the useful middle.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence


class Step(NamedTuple):
    title: str
    tool: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None
    capability: Optional[str] = None
    cost: float = 1.0
    gain: float = 0.0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "tool": self.tool,
                "arguments": self.arguments or {}, "capability": self.capability,
                "cost": self.cost, "gain": self.gain, "note": self.note}


class Node(NamedTuple):
    steps: Sequence[Step]
    score: float
    state: Dict[str, Any]
    complete: bool = False

    @property
    def depth(self) -> int:
        return len(self.steps)


#: Given the current state, produce the possible next steps.
ExpandFn = Callable[[Dict[str, Any], Sequence[Step]], Sequence[Step]]
#: Given a state and the steps taken, how good is this partial plan?
ScoreFn = Callable[[Dict[str, Any], Sequence[Step]], float]
#: Apply a step to the state, returning the new state.
ApplyFn = Callable[[Dict[str, Any], Step], Dict[str, Any]]
#: Is this plan finished?
GoalFn = Callable[[Dict[str, Any], Sequence[Step]], bool]


def default_score(state: Dict[str, Any], steps: Sequence[Step]) -> float:
    """Value gained minus effort spent, with a small nudge toward brevity.

    The 0.02-per-step penalty is what stops the search padding a plan with
    harmless-but-pointless steps to accumulate tiny gains.
    """
    return sum(s.gain for s in steps) - sum(s.cost for s in steps) * 0.25 \
        - 0.02 * len(steps)


def search(
    initial_state: Dict[str, Any],
    expand: ExpandFn,
    apply: ApplyFn,
    *,
    score: Optional[ScoreFn] = None,
    is_goal: Optional[GoalFn] = None,
    beam_width: int = 3,
    max_depth: int = 5,
    max_nodes: int = 2000,
) -> List[Node]:
    """Return the best complete plans found, best first.

    ``max_nodes`` is a hard safety valve: even with a narrow beam, a badly
    behaved ``expand`` function could otherwise run forever.
    """
    score = score or default_score
    beam: List[Node] = [Node(steps=(), score=0.0, state=dict(initial_state))]
    finished: List[Node] = []
    examined = 0

    for _depth in range(max(1, int(max_depth))):
        candidates: List[Node] = []
        for node in beam:
            if node.complete:
                finished.append(node)
                continue
            for step in expand(node.state, node.steps):
                examined += 1
                if examined > max_nodes:
                    break
                steps = tuple(node.steps) + (step,)
                state = apply(dict(node.state), step)
                complete = bool(is_goal(state, steps)) if is_goal else False
                candidates.append(Node(steps, score(state, steps), state, complete))
            if examined > max_nodes:
                break

        if not candidates:
            break

        candidates.sort(key=lambda n: n.score, reverse=True)
        beam = candidates[: max(1, int(beam_width))]
        finished.extend(n for n in beam if n.complete)
        remaining = [n for n in beam if not n.complete]
        if not remaining:
            break
        beam = remaining
        if examined > max_nodes:
            break

    # If nothing formally reached the goal, the best partial plans are still
    # the most useful thing we can offer - returning nothing would be worse.
    results = finished or list(beam)
    results.sort(key=lambda n: n.score, reverse=True)

    unique: List[Node] = []
    seen = set()
    for node in results:
        signature = tuple(s.title for s in node.steps)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(node)
    return unique[: max(1, int(beam_width))]
