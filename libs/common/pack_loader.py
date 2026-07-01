"""Pack Loader — validates and loads a Domain Pack from its directory.

A pack that fails any schema or referential integrity check raises PackLoadError
immediately, so services refuse to start with a bad pack (PACK-01 invariant).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from libs.common.models import KBArticle, Pack, Scenario, SimulatorProfile


class PackLoadError(Exception):
    """Raised when a pack fails validation or integrity checks."""


@dataclass
class LoadedPack:
    pack: Pack
    scenarios: list[Scenario]
    kb_articles: dict[str, KBArticle]       # article_id → article
    log_bundles: dict[str, str]             # bundle_ref (pack-relative) → text
    simulator_profile: SimulatorProfile
    # signature → kb_article_id — the deterministic fallback index used by kb.search
    # when the semantic similarity score is below the confidence threshold.
    signature_index: dict[str, str] = field(default_factory=dict)
    # Convenience index built from scenarios list; avoids O(n) lookups in hot paths.
    scenarios_by_id: dict[str, Scenario] = field(default_factory=dict)


def load_pack(pack_dir: Path) -> LoadedPack:
    """Load and validate a Domain Pack.

    Args:
        pack_dir: Absolute or relative path to the pack directory
                  (e.g. ``packs/datacenter-xe9680``).

    Raises:
        PackLoadError: On any schema violation or referential integrity failure.
    """
    pack_dir = pack_dir.resolve()
    if not pack_dir.is_dir():
        raise PackLoadError(f"Pack directory not found: {pack_dir}")

    pack = _load_pack_yaml(pack_dir)
    sim_profile = _load_simulator_profile(pack_dir, pack.id)
    scenarios = _load_scenarios(pack_dir, pack)
    kb_articles = _load_kb_articles(pack_dir, pack.id)
    _check_kb_refs(scenarios, kb_articles, pack_dir)
    log_bundles = _load_log_bundles(pack_dir, scenarios)
    scenarios = _backfill_log_extracts(scenarios)
    signature_index = _build_signature_index(scenarios)

    return LoadedPack(
        pack=pack,
        scenarios=scenarios,
        kb_articles=kb_articles,
        log_bundles=log_bundles,
        simulator_profile=sim_profile,
        signature_index=signature_index,
        scenarios_by_id={s.id: s for s in scenarios},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_pack_yaml(pack_dir: Path) -> Pack:
    path = pack_dir / "pack.yaml"
    if not path.exists():
        raise PackLoadError(f"Missing pack.yaml in {pack_dir}")
    raw = _read_yaml(path)
    try:
        return Pack.model_validate(raw)
    except ValidationError as exc:
        raise PackLoadError(f"pack.yaml schema error:\n{exc}") from exc


def _load_simulator_profile(pack_dir: Path, pack_id: str) -> SimulatorProfile:
    path = pack_dir / "simulator-profile.yaml"
    if not path.exists():
        raise PackLoadError(f"Missing simulator-profile.yaml in {pack_dir}")
    raw = _read_yaml(path)
    try:
        profile = SimulatorProfile.model_validate(raw)
    except ValidationError as exc:
        raise PackLoadError(f"simulator-profile.yaml schema error:\n{exc}") from exc
    if profile.pack_id != pack_id:
        raise PackLoadError(
            f"simulator-profile.yaml pack_id '{profile.pack_id}' "
            f"does not match pack id '{pack_id}'"
        )
    return profile


def _load_scenarios(pack_dir: Path, pack: Pack) -> list[Scenario]:
    scenarios_dir = pack_dir / "scenarios"
    if not scenarios_dir.is_dir():
        raise PackLoadError(f"Missing scenarios/ directory in {pack_dir}")

    asset_ids = {a.id for a in pack.assets}
    scenarios: list[Scenario] = []

    for yaml_path in sorted(scenarios_dir.glob("*.yaml")):
        raw = _read_yaml(yaml_path)
        try:
            scenario = Scenario.model_validate(raw)
        except ValidationError as exc:
            raise PackLoadError(f"{yaml_path.name}: schema error:\n{exc}") from exc

        if scenario.pack_id != pack.id:
            raise PackLoadError(
                f"{yaml_path.name}: pack_id '{scenario.pack_id}' "
                f"does not match pack '{pack.id}'"
            )
        if scenario.target_asset not in asset_ids:
            raise PackLoadError(
                f"{yaml_path.name}: target_asset '{scenario.target_asset}' "
                f"not in pack assets {sorted(asset_ids)}"
            )
        scenarios.append(scenario)

    if not scenarios:
        raise PackLoadError(f"Pack '{pack.id}' has no scenario files in {scenarios_dir}")

    return scenarios


def _load_kb_articles(pack_dir: Path, pack_id: str) -> dict[str, KBArticle]:
    kb_dir = pack_dir / "kb"
    if not kb_dir.is_dir():
        raise PackLoadError(f"Missing kb/ directory in {pack_dir}")
    return {
        article.id: article
        for md_path in sorted(kb_dir.glob("*.md"))
        for article in [_parse_kb_article(md_path, pack_id)]
    }


def _parse_kb_article(path: Path, pack_id: str) -> KBArticle:
    """Parse a KB article markdown file.

    The article id is always the filename stem (e.g. ``KB000123``).
    Optional YAML frontmatter supplies ``title`` and ``remediation_step_ids``.
    """
    text = path.read_text()
    metadata: dict = {}
    body_md = text

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                metadata = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                pass  # bad frontmatter; fall back to defaults
            body_md = parts[2].strip()

    title = metadata.get("title") or _extract_h1(body_md) or path.stem
    step_ids: list[str] = metadata.get("remediation_step_ids") or []

    return KBArticle(
        id=path.stem,
        pack_id=pack_id,
        title=title,
        body_md=body_md,
        remediation_step_ids=step_ids,
    )


def _extract_h1(body_md: str) -> str | None:
    for line in body_md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _check_kb_refs(
    scenarios: list[Scenario],
    kb_articles: dict[str, KBArticle],
    pack_dir: Path,
) -> None:
    for scenario in scenarios:
        ref = scenario.kb_article_ref
        kb_path = (pack_dir / ref).resolve()
        if not kb_path.exists():
            raise PackLoadError(
                f"Scenario '{scenario.id}': kb_article_ref '{ref}' "
                f"not found at {kb_path}"
            )
        article_id = kb_path.stem
        if article_id not in kb_articles:
            raise PackLoadError(
                f"Scenario '{scenario.id}': KB article id '{article_id}' derived "
                f"from '{ref}' not in loaded articles — filename must match article id"
            )


def _load_log_bundles(pack_dir: Path, scenarios: list[Scenario]) -> dict[str, str]:
    bundles: dict[str, str] = {}
    for scenario in scenarios:
        ref = scenario.log_bundle_ref
        bundle_path = (pack_dir / ref).resolve()
        if not bundle_path.exists():
            raise PackLoadError(
                f"Scenario '{scenario.id}': log_bundle_ref '{ref}' "
                f"not found at {bundle_path}"
            )
        bundles[ref] = bundle_path.read_text()
    return bundles


def _backfill_log_extracts(scenarios: list[Scenario]) -> list[Scenario]:
    """Derive log_extract from emit.log_entries when not explicitly set."""
    result = []
    for s in scenarios:
        if s.log_extract is None and s.emit.log_entries:
            s = s.model_copy(update={"log_extract": s.emit.log_entries[0].message})
        result.append(s)
    return result


def _build_signature_index(scenarios: list[Scenario]) -> dict[str, str]:
    """Map every known error signature to its KB article id.

    This is the deterministic fallback used by kb.search when semantic
    similarity is below the confidence threshold — guarantees the demo
    always resolves to the correct article, even if the LLM paraphrases.
    """
    index: dict[str, str] = {}
    for scenario in scenarios:
        kb_id = Path(scenario.kb_article_ref).stem
        for sig in scenario.error_signatures:
            index[sig] = kb_id
    return index


def _read_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise PackLoadError(f"{path.name}: invalid YAML: {exc}") from exc
