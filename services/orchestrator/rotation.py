"""Scenario rotation policy — no immediate repeats (SC4, U-06).

The demo must feel fresh on a second run: consecutive calls to ``next()``
always return a different scenario id, provided the pack has more than one.
"""

from __future__ import annotations

import random


class RotationPolicy:
    """Selects the next scenario, guaranteeing no immediate repeat.

    Args:
        scenario_ids: All scenario ids available in the active pack.
    """

    def __init__(self, scenario_ids: list[str]) -> None:
        if not scenario_ids:
            raise ValueError("RotationPolicy requires at least one scenario id")
        self._ids = list(scenario_ids)
        self._last: str | None = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def last_used(self) -> str | None:
        """The most recently returned scenario id, or None on first call."""
        return self._last

    @property
    def scenario_ids(self) -> list[str]:
        return list(self._ids)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def next(self) -> str:
        """Return the next scenario id, guaranteed different from last (if >1 available)."""
        candidates = [sid for sid in self._ids if sid != self._last]
        if not candidates:
            # Only one scenario — can't avoid a repeat
            candidates = self._ids
        chosen = random.choice(candidates)
        self._last = chosen
        return chosen

    def force(self, scenario_id: str) -> str:
        """Presenter override: select *scenario_id* regardless of rotation order.

        Also updates ``last_used`` so the subsequent ``next()`` call won't repeat it.

        Raises:
            KeyError: If *scenario_id* is not in the pack.
        """
        if scenario_id not in self._ids:
            raise KeyError(f"Unknown scenario: {scenario_id!r}. Available: {self._ids}")
        self._last = scenario_id
        return scenario_id

    def reset(self) -> None:
        """Clear rotation history (next call treats all scenarios as eligible)."""
        self._last = None
