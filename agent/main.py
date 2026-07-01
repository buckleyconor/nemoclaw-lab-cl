"""Agent continuous polling loop — production entrypoint.

Configuration via environment variables (see agent/config.yaml for names):
  LLM_BASE_URL            vLLM-compatible OpenAI endpoint
  LLM_API_KEY             API key (default: "vllm")
  LLM_MODEL               Model name
  GATEWAY_URL             Gateway service URL  (default: http://gateway:8001)
  MCP_TOOLS_URL           MCP Tools service URL (default: http://mcp-tools:8004)
  PACK_ID                 Pack to load (default: datacenter-xe9680)
  AGENT_POLL_INTERVAL_SECONDS   Seconds between loop iterations (default: 5)
  AGENT_APPROVAL_TIMEOUT_SECONDS  Max wait for human approval (default: 600)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

import httpx

from agent.llm import OpenAILLMClient
from agent.loop import run_agent_loop
from agent.tools import MCPAgentTools
from libs.common.pack_loader import load_pack

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

_GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:8001")
_MCP_TOOLS_URL = os.environ.get("MCP_TOOLS_URL", "http://mcp-tools:8004")
_PACK_ID = os.environ.get("PACK_ID", "datacenter-xe9680")
_POLL_INTERVAL = float(os.environ.get("AGENT_POLL_INTERVAL_SECONDS", "5.0"))
_APPROVAL_TIMEOUT = float(os.environ.get("AGENT_APPROVAL_TIMEOUT_SECONDS", "600.0"))


def _make_llm_client() -> OpenAILLMClient:
    base_url = os.environ.get("LLM_BASE_URL") or os.environ.get("VLLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("VLLM_API_KEY", "vllm")
    model = os.environ.get("LLM_MODEL") or os.environ.get("VLLM_MODEL")
    if not base_url or not model:
        raise RuntimeError(
            "LLM_BASE_URL and LLM_MODEL env vars are required. "
            "Set them in the Helm values or secret."
        )
    return OpenAILLMClient(base_url=base_url, api_key=api_key, model=model)


async def _run(stop: asyncio.Event) -> None:
    root = Path(__file__).parent.parent
    loaded_pack = load_pack(root / "packs" / _PACK_ID)
    llm = _make_llm_client()

    log.info(
        "Agent started — pack=%s gateway=%s mcp=%s poll=%.1fs",
        _PACK_ID, _GATEWAY_URL, _MCP_TOOLS_URL, _POLL_INTERVAL,
    )

    while not stop.is_set():
        try:
            async with httpx.AsyncClient(base_url=_GATEWAY_URL, timeout=30.0) as gw:
                async with MCPAgentTools.connect(_MCP_TOOLS_URL) as tools:
                    result = await run_agent_loop(
                        tools=tools,
                        gateway_client=gw,
                        llm_client=llm,
                        loaded_pack=loaded_pack,
                        approval_timeout=_APPROVAL_TIMEOUT,
                        poll_interval=_POLL_INTERVAL,
                    )
            log.info("Loop result: status=%s fault=%s", result.status, result.fault_id)
        except Exception as exc:
            log.error("Agent loop error: %s", exc, exc_info=True)

        if not stop.is_set():
            await asyncio.sleep(_POLL_INTERVAL)

    log.info("Agent shut down cleanly.")


def main() -> None:
    stop = asyncio.Event()

    def _handle_signal(*_) -> None:
        log.info("Shutdown signal received — stopping after current loop.")
        stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    asyncio.run(_run(stop))


if __name__ == "__main__":
    main()
