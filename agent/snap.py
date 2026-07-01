"""Signature snap-to-known — deterministic fallback that guarantees the demo
never fails at the KB-match step due to LLM paraphrasing.

The LLM may return "looks like an Xid 79 GPU bus drop" — snap_to_known maps
this to the canonical signature ``"Xid 79"`` by scanning the LLM output for
any known signature substring.  If nothing matches, the caller falls back to
the scenario's first ``error_signatures[0]`` (U-04).
"""

from __future__ import annotations


def snap_to_known(
    llm_output: str,
    signature_index: dict[str, str],
) -> str | None:
    """Return the first canonical signature found in ``llm_output``, or None.

    Args:
        llm_output:       Raw string returned by the LLM for signature extraction.
        signature_index:  Mapping from canonical signature → KB article id (from
                          ``LoadedPack.signature_index``).

    Returns:
        A canonical signature string, or ``None`` if no known signature is present
        in the LLM output (caller should use the scenario's own
        ``error_signatures[0]`` as fallback).
    """
    lower = llm_output.lower()
    for sig in signature_index:
        if sig.lower() in lower:
            return sig
    return None
