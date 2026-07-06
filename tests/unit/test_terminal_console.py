"""Restricted operator console (ADR-013): argv construction + push sequencing.

Pure-function coverage for all 6 menu targets — confirms every sandbox path
is a hardcoded constant and every subprocess call is a literal argv list, so
there is no operator-input string that could reach a shell.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.terminal.console import (
    MENU_TARGETS,
    ConsoleConfig,
    download_argv,
    edit_path,
    editor_argv,
    find_target,
    format_menu,
    push_argv,
    run_target,
)


def test_menu_has_exactly_six_targets() -> None:
    assert [t.key for t in MENU_TARGETS] == ["1", "2", "3", "4", "5", "6"]


def test_menu_targets_cover_soul_agents_and_four_skills() -> None:
    sandbox_paths = [t.sandbox_path for t in MENU_TARGETS]
    assert "/sandbox/.openclaw/workspace/SOUL.md" in sandbox_paths
    assert "/sandbox/.openclaw/workspace/AGENTS.md" in sandbox_paths
    for skill in (
        "infra-sentinel-monitor",
        "infra-sentinel-diagnose",
        "infra-sentinel-notify",
        "infra-sentinel-remediate",
    ):
        assert f"/sandbox/.openclaw/workspace/skills/{skill}" in sandbox_paths


def test_find_target_returns_none_for_unknown_selection() -> None:
    assert find_target("7") is None
    assert find_target("nemoclaw-lab; rm -rf /") is None


@pytest.mark.parametrize("target", MENU_TARGETS, ids=lambda t: t.key)
def test_edit_path_is_confined_under_workspace_dir(target, tmp_path: Path) -> None:
    path = edit_path(target, tmp_path)
    assert tmp_path in path.parents
    if target.kind == "skill":
        assert path.name == "SKILL.md"


@pytest.mark.parametrize("target", MENU_TARGETS, ids=lambda t: t.key)
def test_download_argv_is_a_literal_list_no_shell_metacharacters(target, tmp_path: Path) -> None:
    argv = download_argv("nemoclaw", "tenant-a", target, tmp_path)
    assert argv[0] == "nemoclaw"
    assert argv[1] == "tenant-a"
    assert argv[2] == "download"
    assert argv[3] == target.sandbox_path
    assert all(isinstance(part, str) for part in argv)


@pytest.mark.parametrize("target", MENU_TARGETS, ids=lambda t: t.key)
def test_push_argv_uses_upload_for_files_and_skill_install_for_skills(
    target, tmp_path: Path
) -> None:
    argv = push_argv("nemoclaw", "tenant-a", target, tmp_path)
    if target.kind == "skill":
        assert argv == [
            "nemoclaw",
            "tenant-a",
            "skill",
            "install",
            str(tmp_path / target.scratch_rel),
        ]
    else:
        assert argv == [
            "nemoclaw",
            "tenant-a",
            "upload",
            str(tmp_path / target.scratch_rel),
            target.sandbox_path,
        ]


def test_editor_argv_is_restricted_vim_by_default() -> None:
    argv = editor_argv("vim", ["-Z"], Path("/tmp/SOUL.md"))
    assert argv == ["vim", "-Z", "/tmp/SOUL.md"]


def test_format_menu_lists_all_targets_and_quit() -> None:
    menu = format_menu("tenant-a")
    for t in MENU_TARGETS:
        assert t.label in menu
    assert "q) Quit" in menu


def test_format_menu_marks_completed_targets_green() -> None:
    menu = format_menu("tenant-a", completed=frozenset({"1", "3"}))
    assert "\033[32m✓ Edit SOUL.md\033[0m" in menu
    assert "\033[32m✓ Edit infra-sentinel-monitor/SKILL.md\033[0m" in menu
    # Untouched targets render plain, with no color codes.
    assert "  2) Edit AGENTS.md" in menu
    assert "\033[32m✓ Edit AGENTS.md" not in menu


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_target_downloads_edits_then_pushes_in_order(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if len(argv) > 2 and argv[2] == "download":
            return _FakeCompleted(returncode=1, stderr="not found")
        return _FakeCompleted(returncode=0, stdout="ok")

    target = MENU_TARGETS[0]  # SOUL.md
    config = ConsoleConfig(
        sandbox_name="tenant-a", workspace_dir=tmp_path, editor_bin="true", editor_args=()
    )

    ok = run_target(target, config, run=fake_run)

    assert ok is True
    kinds = [c[2] if len(c) > 2 else None for c in calls]
    assert kinds[0] == "download"
    assert calls[1][0] == "true"  # the editor invocation
    assert calls[2][2] == "upload"


def test_run_target_returns_false_when_push_fails(tmp_path: Path) -> None:
    def fake_run(argv, **kwargs):
        if len(argv) > 2 and argv[2] in ("download", "upload"):
            return _FakeCompleted(returncode=1, stderr="boom")
        return _FakeCompleted(returncode=0)

    target = MENU_TARGETS[0]
    config = ConsoleConfig(
        sandbox_name="tenant-a", workspace_dir=tmp_path, editor_bin="true", editor_args=()
    )

    assert run_target(target, config, run=fake_run) is False


def test_run_target_creates_scratch_parent_directory(tmp_path: Path) -> None:
    def fake_run(argv, **kwargs):
        return _FakeCompleted(returncode=0, stdout="ok")

    target = next(t for t in MENU_TARGETS if t.kind == "skill")
    config = ConsoleConfig(
        sandbox_name="tenant-a", workspace_dir=tmp_path, editor_bin="true", editor_args=()
    )

    run_target(target, config, run=fake_run)

    assert (tmp_path / target.scratch_rel).is_dir()
