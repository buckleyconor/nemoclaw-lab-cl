"""LLM client helpers.

Production: thin wrapper around ``openai.AsyncOpenAI`` pointed at the vLLM endpoint.
Tests:      ``StubLLMClient`` — returns canned completions without any HTTP call.
"""

from __future__ import annotations

from typing import Protocol


# ── Protocol ─────────────────────────────────────────────────────────────────

class LLMClient(Protocol):
    """Minimal interface the agent loop needs from the LLM client."""

    async def complete(self, system: str, user: str, *, thinking: bool = True) -> str:
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
        max_tokens: int = 2048,
    ) -> None:
        try:
            import openai

            self._client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)
        except ImportError as exc:
            raise ImportError("openai package required for OpenAILLMClient") from exc
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def complete(self, system: str, user: str, *, thinking: bool = True) -> str:
        kwargs: dict = dict(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        if not thinking:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        resp = await self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        # Qwen 3.6 35B A3B surfaces chain-of-thought in .reasoning_content;
        # prefer the final answer in .content, fall through to reasoning fields
        # only when thinking is enabled and content is empty.
        return msg.content or getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None) or ""


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

    async def complete(self, system: str, user: str, *, thinking: bool = True) -> str:
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

FAULT_ANALYSIS_SYSTEM = (
    "You are an AI infrastructure monitoring agent analysing a hardware fault on a GPU cluster node. "
    "Based on the error signature and log excerpt, write 2-3 sentences in first person: "
    "what the fault indicates, what most likely caused it, and the immediate operational risk. "
    "Be specific and technical. Do not repeat the signature verbatim in the opening. "
    "Do not use bullet points or headings — flowing prose only."
)

FAULT_ANALYSIS_USER_TMPL = (
    "Error signature: {signature}\n\nLog excerpt:\n{log_text}"
)
