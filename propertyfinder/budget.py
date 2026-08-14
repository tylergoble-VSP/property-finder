"""The quota, as an object rather than as a paragraph in a README.

The provider bills a fixed number of calls a month (a thousand, on the plan this tool
was built for) and that allowance is shared with a sibling project. In the original
build this fact lived only in documentation, which meant nothing structurally prevented
a careless loop from spending a month's budget in an afternoon — documentation cannot
refuse.

So the ceiling is a value that gets passed to whatever might spend it. The adapter asks
before every request and raises rather than send; the daily orchestrator asks what is
left before deciding how deep to sweep. Nothing here knows about HTTP, and nothing here
reads configuration: a budget enforces exactly the number it was handed, which is what
makes "spend at most twenty calls on this experiment" a one-line thing to say.
"""
from __future__ import annotations

from dataclasses import dataclass


class BudgetExceeded(RuntimeError):
    """The run's ceiling was reached. Nothing was sent, and nothing was charged."""


@dataclass
class CallBudget:
    """A ceiling on billable calls, and a running count of what has been spent."""

    max_calls: int
    spent: int = 0
    label: str = "this run"

    def __post_init__(self) -> None:
        if self.max_calls < 0:
            raise ValueError(f"a call budget cannot be negative (got {self.max_calls})")

    @property
    def remaining(self) -> int:
        return max(self.max_calls - self.spent, 0)

    def check(self, cost: int = 1) -> None:
        """Raise if `cost` more calls would break the ceiling. Spends nothing."""
        if self.spent + cost > self.max_calls:
            raise BudgetExceeded(
                f"{self.label}: {self.spent} of {self.max_calls} calls spent, so the "
                f"next {cost} would exceed the ceiling — nothing was sent"
            )

    def spend(self, cost: int = 1) -> None:
        """Charge `cost` calls, refusing before the fact if they do not fit."""
        self.check(cost)
        self.spent += cost

    def __str__(self) -> str:
        return f"{self.spent}/{self.max_calls} calls spent ({self.remaining} left)"
