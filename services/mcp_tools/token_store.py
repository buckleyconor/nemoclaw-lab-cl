"""ApprovalTokenStore — in-memory single-use approval token registry.

The Gateway (M5) mints tokens via ``mint()``.  The remediation tool consumes
them via ``consume()``.  Both operations are synchronous; async wrapping is
the caller's responsibility.
"""

from __future__ import annotations

import secrets

from libs.common.models import ApprovalDecision, ApprovalToken


class ApprovalTokenStore:
    """Thread-safe in-memory token store (single-process demo only).

    Production upgrade path: swap body for Redis SETNX with TTL.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, ApprovalToken] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # Write path (Gateway)
    # ──────────────────────────────────────────────────────────────────────────

    def mint(
        self,
        fault_event_id: str,
        decision: ApprovalDecision | str,
        decided_by: str = "human",
    ) -> ApprovalToken:
        """Create and store a new single-use token bound to ``fault_event_id``."""
        if isinstance(decision, str):
            decision = ApprovalDecision(decision)
        tok = ApprovalToken(
            token=secrets.token_urlsafe(32),
            fault_event_id=fault_event_id,
            decision=decision,
            decided_by=decided_by,
        )
        self._tokens[tok.token] = tok
        return tok

    def store(self, token: ApprovalToken) -> None:
        """Store a pre-built token (used in tests and Gateway)."""
        self._tokens[token.token] = token

    # ──────────────────────────────────────────────────────────────────────────
    # Read / consume path (remediation tool)
    # ──────────────────────────────────────────────────────────────────────────

    def get(self, token_str: str) -> ApprovalToken | None:
        return self._tokens.get(token_str)

    def consume(self, token_str: str) -> None:
        """Mark token as consumed.  No-op if token doesn't exist."""
        if token_str in self._tokens:
            self._tokens[token_str] = self._tokens[token_str].model_copy(
                update={"consumed": True}
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Test helpers
    # ──────────────────────────────────────────────────────────────────────────

    def clear(self) -> None:
        self._tokens.clear()

    def __len__(self) -> int:
        return len(self._tokens)
