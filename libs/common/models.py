"""Shared Pydantic models — Pack, Scenario, runtime entities. Implemented in M1."""

from __future__ import annotations

from pydantic import BaseModel


class _Placeholder(BaseModel):
    """Remove once M1 models are implemented."""

    note: str = "M1 — implement Pack, Scenario, KBArticle, runtime entities here"
