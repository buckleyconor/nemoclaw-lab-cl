"""kb MCP tool — semantic + deterministic KB article search."""

from __future__ import annotations

from services.mcp_tools.kb_index import KBIndex, KBSearchResult


async def kb_search(
    kb_index: KBIndex,
    signature: str,
    fallback_kb_id: str | None = None,
) -> KBSearchResult | None:
    """Return the best-matching KB article for an error signature.

    Args:
        kb_index:       Initialised KBIndex for the current pack.
        signature:      Error signature or log extract extracted by the agent.
        fallback_kb_id: Article id to use when both semantic search and
                        signature_index return nothing.

    Returns:
        KBSearchResult dict, or None if no match found.
    """
    return kb_index.search(signature, fallback_kb_id=fallback_kb_id)
