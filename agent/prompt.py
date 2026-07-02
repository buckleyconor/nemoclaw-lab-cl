"""System-prompt assembly — soul.md + the five SKILL.md files (ADR-010).

The skills are live instruction content: the harness loads them (YAML
frontmatter stripped) into the LLM's system prompt, so editing a SKILL.md
changes agent behaviour without a Python change.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_AGENT_DIR = Path(__file__).parent

# Guide first for orientation, then the four phase skills.
SKILL_ORDER = (
    "infra-sentinel-guide",
    "infra-sentinel-monitor",
    "infra-sentinel-diagnose",
    "infra-sentinel-notify",
    "infra-sentinel-remediate",
)


def _strip_frontmatter(text: str) -> str:
    """Drop a leading ``--- ... ---`` YAML frontmatter block, if present."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


@lru_cache(maxsize=4)
def build_system_prompt(agent_dir: Path = _AGENT_DIR) -> str:
    """Concatenate soul.md and the skill files into one system prompt."""
    parts = [_strip_frontmatter((agent_dir / "soul.md").read_text(encoding="utf-8"))]
    for skill in SKILL_ORDER:
        skill_md = agent_dir / "skills" / skill / "SKILL.md"
        if skill_md.exists():
            parts.append(_strip_frontmatter(skill_md.read_text(encoding="utf-8")))
    return "\n\n---\n\n".join(part.strip() for part in parts)
