"""The evaluation harness: does the review layer make outcomes better?

Cases (prat.evals.cases) are small repositories with a card and a hidden check the agent
never sees. The runner (prat.evals.runner) puts each case's card through the whole pipeline
in a PRAT home of its own and scores every attempt with the hidden check. Results
(prat.evals.results) compare the agent's first attempt, which is what the agent alone would
hand over, with what PRAT handed over after its gates, reviewers and rework, and they are
stored so two evaluations can be compared. A finished card can be exported as a case
(prat.evals.export) and replayed against today's policy, gates and reviewers.
"""

from prat.evals.cases import CaseError, EvalCase, builtin_suite, load_case, load_cases
from prat.evals.results import (
    CaseResult,
    Comparison,
    EvalResult,
    compare,
    find,
    list_results,
    step_rates,
    summarize,
)
from prat.evals.runner import EvalOptions, run_case, run_suite

__all__ = [
    "CaseError",
    "CaseResult",
    "Comparison",
    "EvalCase",
    "EvalOptions",
    "EvalResult",
    "builtin_suite",
    "compare",
    "find",
    "list_results",
    "load_case",
    "load_cases",
    "run_case",
    "run_suite",
    "step_rates",
    "summarize",
]
