"""LLM client helpers.

Production: thin wrapper around ``openai.AsyncOpenAI`` pointed at the vLLM endpoint.
Tests:      ``StubLLMClient`` — returns canned completions without any HTTP call.
"""

from __future__ import annotations

from typing import Protocol


# ── Protocol ─────────────────────────────────────────────────────────────────

class LLMClient(Protocol):
    """Minimal interface the agent loop needs from the LLM client."""

    async def complete(self, system: str, user: str) -> str:
        """Return the assistant's response string."""
        ...


# ── Production client ─────────────────────────────────────────────────────────

class OpenAILLMClient:
    """Wraps ``openai.AsyncOpenAI`` with the vLLM endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> None:
        try:
            import openai

            self._client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)
        except ImportError as exc:
            raise ImportError("openai package required for OpenAILLMClient") from exc
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def complete(self, system: str, user: str) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        return resp.choices[0].message.content or ""


# ── Stub client (tests) ───────────────────────────────────────────────────────

class StubLLMClient:
    """Deterministic stub that returns canned completions.

    ``responses`` maps a substring of the user prompt to a fixed reply.
    The first matching entry wins.  Falls back to ``default_response``.
    """

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        default_response: str = "Xid 79",
    ) -> None:
        self._responses: dict[str, str] = responses or {}
        self._default = default_response
        self.calls: list[tuple[str, str]] = []  # (system, user) for assertions

    async def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        for pattern, reply in self._responses.items():
            if pattern.lower() in user.lower() or pattern.lower() in system.lower():
                return reply
        return self._default


# ── Prompts ───────────────────────────────────────────────────────────────────

SIGNATURE_EXTRACTION_SYSTEM = (
    "You are an infrastructure monitoring agent. "
    "Analyse log text and extract the primary error signature — "
    "a short, canonical phrase that identifies the fault class "
    "(e.g. 'Xid 79', 'ECC uncorrectable error', 'PSU 2 input lost'). "
    "Return ONLY the signature string, nothing else. "
    "Do not explain or rephrase."
)

SIGNATURE_EXTRACTION_USER_TMPL = (
    "Extract the error signature from these logs:\n\n{log_text}"
)
