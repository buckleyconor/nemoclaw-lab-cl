"""Unit tests for the ADR-010 agent surface.

Covers:
  - agent/prompt.py: soul.md + SKILL.md assembly (frontmatter stripped, guide first)
  - agent/tools.py: the LLM tool allowlist excludes remediation.execute, and the
    fallback schemas match the allowlist exactly
"""

from __future__ import annotations

from pathlib import Path

from agent.prompt import SKILL_ORDER, build_system_prompt
from agent.tools import (
    FALLBACK_TOOL_SCHEMAS,
    LLM_EXPOSED_TOOLS,
    to_function_name,
)

AGENT_DIR = Path(__file__).parent.parent.parent / "agent"


# ── Prompt assembly ───────────────────────────────────────────────────────────


def test_system_prompt_contains_soul_and_all_skills() -> None:
    prompt = build_system_prompt(AGENT_DIR)
    # soul.md identity heading
    assert "AI Infrastructure Sentinel" in prompt
    # each skill body's title made it in
    assert "Skills Guide" in prompt
    assert "Infrastructure Monitor" in prompt
    assert "Fault Diagnosis" in prompt
    assert "Operator Notification" in prompt
    assert "Fault Remediation" in prompt


def test_system_prompt_strips_yaml_frontmatter() -> None:
    prompt = build_system_prompt(AGENT_DIR)
    assert 'name: "infra-sentinel' not in prompt
    assert 'license: "Apache-2.0"' not in prompt


def test_system_prompt_guide_comes_first_among_skills() -> None:
    prompt = build_system_prompt(AGENT_DIR)
    positions = {
        "guide": prompt.find("Skills Guide"),
        "monitor": prompt.find("Skill: Infrastructure Monitor"),
        "diagnose": prompt.find("Skill: Fault Diagnosis"),
    }
    assert -1 not in positions.values()
    assert positions["guide"] < positions["monitor"] < positions["diagnose"]


def test_skill_order_matches_skill_directories() -> None:
    for skill in SKILL_ORDER:
        assert (AGENT_DIR / "skills" / skill / "SKILL.md").is_file(), skill


# ── LLM tool surface ──────────────────────────────────────────────────────────


def test_execute_is_never_in_the_llm_allowlist() -> None:
    assert "remediation.execute" not in LLM_EXPOSED_TOOLS
    assert "remediation.propose" in LLM_EXPOSED_TOOLS
    assert "notify.post_activity" in LLM_EXPOSED_TOOLS


def test_fallback_schemas_match_allowlist_exactly() -> None:
    schema_names = {s["function"]["name"] for s in FALLBACK_TOOL_SCHEMAS}
    allowlist_names = {to_function_name(n) for n in LLM_EXPOSED_TOOLS}
    assert schema_names == allowlist_names
    assert "remediation_execute" not in schema_names


def test_function_names_are_openai_safe() -> None:
    import re

    for schema in FALLBACK_TOOL_SCHEMAS:
        assert re.fullmatch(r"[a-zA-Z0-9_-]+", schema["function"]["name"])
