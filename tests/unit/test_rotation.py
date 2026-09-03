"""U-06 — RotationPolicy: no immediate repeats, presenter override, reset."""

import pytest

from services.orchestrator.rotation import RotationPolicy

FIVE_SCENARIOS = [
    "scn-gpu-xid-79",
    "scn-ecc-uncorrectable",
    "scn-psu-loss",
    "scn-nvlink-down",
    "scn-thermal-throttle",
]


# ─────────────────────────────────────────────────────────────────────────────
# U-06 — two consecutive calls return different scenario ids
# ─────────────────────────────────────────────────────────────────────────────


def test_u06_two_consecutive_calls_differ() -> None:
    """U-06: two consecutive next() calls must return different scenario ids."""
    policy = RotationPolicy(FIVE_SCENARIOS)
    first = policy.next()
    second = policy.next()
    assert first != second, f"Expected different scenarios, got {first!r} twice"


def test_u06_no_immediate_repeat_over_many_calls() -> None:
    """U-06: 100 consecutive calls never produce an immediate repeat."""
    policy = RotationPolicy(FIVE_SCENARIOS)
    prev = policy.next()
    for _ in range(99):
        current = policy.next()
        assert current != prev, f"Immediate repeat: got {current!r} twice in a row"
        prev = current


def test_u06_all_scenarios_eventually_selected() -> None:
    """All scenarios are reachable (no scenario is permanently excluded)."""
    policy = RotationPolicy(FIVE_SCENARIOS)
    seen: set[str] = set()
    for _ in range(50):
        seen.add(policy.next())
    assert seen == set(FIVE_SCENARIOS)


# ─────────────────────────────────────────────────────────────────────────────
# Initial state
# ─────────────────────────────────────────────────────────────────────────────


def test_last_used_is_none_before_first_call() -> None:
    policy = RotationPolicy(FIVE_SCENARIOS)
    assert policy.last_used is None


def test_last_used_updated_after_next() -> None:
    policy = RotationPolicy(FIVE_SCENARIOS)
    chosen = policy.next()
    assert policy.last_used == chosen


# ─────────────────────────────────────────────────────────────────────────────
# Presenter override (force)
# ─────────────────────────────────────────────────────────────────────────────


def test_force_selects_exact_scenario() -> None:
    policy = RotationPolicy(FIVE_SCENARIOS)
    policy.next()  # advance past initial state
    result = policy.force("scn-psu-loss")
    assert result == "scn-psu-loss"
    assert policy.last_used == "scn-psu-loss"


def test_force_unknown_scenario_raises() -> None:
    policy = RotationPolicy(FIVE_SCENARIOS)
    with pytest.raises(KeyError, match="scn-unknown"):
        policy.force("scn-unknown")


def test_after_force_next_avoids_forced_scenario() -> None:
    """next() after force() should not immediately repeat the forced scenario."""
    policy = RotationPolicy(FIVE_SCENARIOS)
    policy.force("scn-psu-loss")
    following = policy.next()
    assert following != "scn-psu-loss"


# ─────────────────────────────────────────────────────────────────────────────
# Reset
# ─────────────────────────────────────────────────────────────────────────────


def test_reset_clears_last_used() -> None:
    policy = RotationPolicy(FIVE_SCENARIOS)
    policy.next()
    policy.reset()
    assert policy.last_used is None


def test_after_reset_all_scenarios_eligible() -> None:
    """After reset, the first next() could return any scenario (no excluded one)."""
    policy = RotationPolicy(FIVE_SCENARIOS)
    policy.force("scn-gpu-xid-79")
    policy.reset()
    # After 20 calls post-reset we should see scn-gpu-xid-79 at least once
    seen = {policy.next() for _ in range(20)}
    assert "scn-gpu-xid-79" in seen


# ─────────────────────────────────────────────────────────────────────────────
# Edge case: single scenario
# ─────────────────────────────────────────────────────────────────────────────


def test_single_scenario_always_returns_same() -> None:
    policy = RotationPolicy(["scn-gpu-xid-79"])
    for _ in range(5):
        assert policy.next() == "scn-gpu-xid-79"


def test_empty_scenarios_raises_on_init() -> None:
    with pytest.raises(ValueError, match="at least one"):
        RotationPolicy([])
