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
    RESET_KEY,
    RESET_LABEL,
    ConsoleConfig,
    blank_content,
    download_argv,
    edit_path,
    editor_argv,
    find_target,
    format_menu,
    push_argv,
    run_reset,
    run_target,
    validate_skill_content,
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
        # Skills deploy to /sandbox/.openclaw/skills/<name> — this is where
        # `nemoclaw skill install` actually writes, and is NOT a path under
        # the workspace mirror (/sandbox/.openclaw/workspace/...). Verified
        # against a live sandbox: install always lands here regardless of
        # which workspace directory the source SKILL.md was edited from.
        assert f"/sandbox/.openclaw/skills/{skill}" in sandbox_paths
        assert f"/sandbox/.openclaw/workspace/skills/{skill}" not in sandbox_paths


def test_find_target_returns_none_for_unknown_selection() -> None:
    assert find_target("8") is None
    assert find_target("nemoclaw-lab; rm -rf /") is None


def test_reset_key_is_not_an_edit_target() -> None:
    """Option 7 (reset) is a console action, not a MenuTarget — find_target
    must not resolve it, so the main loop's dedicated reset branch handles it."""
    assert RESET_KEY == "7"
    assert find_target(RESET_KEY) is None


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
        # v0.0.109 OpenShell upload semantics: the destination is the parent
        # DIRECTORY, not the file path. A file-path destination collides with
        # the workspace templates the managed runtime seeds at first boot
        # ("mkdir: cannot create directory '.../SOUL.md': File exists").
        assert argv == [
            "nemoclaw",
            "tenant-a",
            "upload",
            str(tmp_path / target.scratch_rel),
            target.sandbox_path.rsplit("/", 1)[0] + "/",
        ]


def test_editor_argv_is_restricted_vim_by_default() -> None:
    argv = editor_argv("vim", ["-Z"], Path("/tmp/SOUL.md"))
    assert argv == ["vim", "-Z", "/tmp/SOUL.md"]


def test_format_menu_lists_all_targets_reset_and_quit() -> None:
    menu = format_menu("tenant-a")
    for t in MENU_TARGETS:
        assert t.label in menu
    assert f"7) {RESET_LABEL}" in menu
    assert "q) Quit" in menu
    # Reset renders after the edit targets and before Quit.
    assert menu.index("6)") < menu.index("7)") < menu.index("q) Quit")


def test_menu_labels_describe_the_push_destination() -> None:
    labels = [t.label for t in MENU_TARGETS]
    assert labels[0] == "Edit SOUL.md and push into the OpenShell sandbox automatically"
    assert labels[1] == "Edit AGENTS.md and push into the OpenShell sandbox automatically"
    for label in labels[2:]:
        assert label.endswith("and install in the NemoClaw agent")


def test_format_menu_marks_completed_targets_green() -> None:
    menu = format_menu("tenant-a", completed=frozenset({"1", "3"}))
    assert f"\033[32m✓ {MENU_TARGETS[0].label}\033[0m" in menu
    assert f"\033[32m✓ {MENU_TARGETS[2].label}\033[0m" in menu
    # Untouched targets render plain, with no color codes.
    assert f"  2) {MENU_TARGETS[1].label}" in menu
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


def test_validate_skill_content_accepts_wellformed_frontmatter() -> None:
    good = '---\nname: "infra-sentinel-monitor"\ndescription: "x"\n---\n\n# Skill\n'
    assert validate_skill_content(good) is None


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("", "must start with"),
        ("# Skill without frontmatter\n", "must start with"),
        ("---\ndescription: no name here\n---\n", "no 'name:'"),
        ("---\nname: never-closed\n", "never closed"),
    ],
)
def test_validate_skill_content_rejects_mangled_pastes(text: str, fragment: str) -> None:
    error = validate_skill_content(text)
    assert error is not None and fragment in error


def test_run_target_skill_with_broken_frontmatter_is_not_pushed(tmp_path: Path) -> None:
    """A mangled paste (e.g. pasted in vim normal mode) must not go green —
    the push is skipped entirely and the item stays incomplete."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if len(argv) > 2 and argv[2] == "download":
            return _FakeCompleted(returncode=1, stderr="not found")
        return _FakeCompleted(returncode=0, stdout="ok")

    target = next(t for t in MENU_TARGETS if t.kind == "skill")
    config = ConsoleConfig(
        sandbox_name="tenant-a", workspace_dir=tmp_path, editor_bin="true", editor_args=()
    )
    skill_file = edit_path(target, config.workspace_dir)
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("garbled paste with no frontmatter at all\n")

    assert run_target(target, config, run=fake_run) is False
    assert not any(len(c) > 2 and c[2] == "skill" for c in calls)  # no install attempted


def test_run_target_skill_with_valid_frontmatter_still_pushes(tmp_path: Path) -> None:
    def fake_run(argv, **kwargs):
        if len(argv) > 2 and argv[2] == "download":
            return _FakeCompleted(returncode=1, stderr="not found")
        return _FakeCompleted(returncode=0, stdout="ok")

    target = next(t for t in MENU_TARGETS if t.kind == "skill")
    config = ConsoleConfig(
        sandbox_name="tenant-a", workspace_dir=tmp_path, editor_bin="true", editor_args=()
    )
    skill_file = edit_path(target, config.workspace_dir)
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text('---\nname: "x"\n---\nBody\n')

    assert run_target(target, config, run=fake_run) is True


def test_blank_reset_stub_passes_skill_validation() -> None:
    """run_reset's blank skill stub must stay installable under the new check."""
    target = next(t for t in MENU_TARGETS if t.kind == "skill")
    assert validate_skill_content(blank_content(target)) is None


def test_run_target_creates_scratch_parent_directory(tmp_path: Path) -> None:
    def fake_run(argv, **kwargs):
        return _FakeCompleted(returncode=0, stdout="ok")

    target = next(t for t in MENU_TARGETS if t.kind == "skill")
    config = ConsoleConfig(
        sandbox_name="tenant-a", workspace_dir=tmp_path, editor_bin="true", editor_args=()
    )

    run_target(target, config, run=fake_run)

    assert (tmp_path / target.scratch_rel).is_dir()


def test_run_reset_blanks_every_target_and_pushes_all_six(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _FakeCompleted(returncode=0, stdout="ok")

    config = ConsoleConfig(
        sandbox_name="tenant-a", workspace_dir=tmp_path, editor_bin="true", editor_args=()
    )

    ok = run_reset(config, run=fake_run)

    assert ok is True
    # One push per target, no downloads and no editor invocations.
    assert len(calls) == len(MENU_TARGETS)
    for argv in calls:
        assert argv[2] in ("upload", "skill")
    # Every scratch file exists and matches blank_content() for its kind:
    # truly empty for SOUL.md/AGENTS.md, a minimal frontmatter stub for
    # skills (a fully empty SKILL.md fails `skill install`'s YAML parse).
    for target in MENU_TARGETS:
        path = edit_path(target, tmp_path)
        assert path.exists()
        assert path.read_text() == blank_content(target)
        if target.kind == "workspace_file":
            assert path.read_text() == ""
        else:
            assert path.read_text().startswith("---\n")
            assert "name:" in path.read_text()


def test_blank_content_is_empty_for_workspace_files() -> None:
    for target in MENU_TARGETS:
        if target.kind == "workspace_file":
            assert blank_content(target) == ""


def test_blank_content_for_skills_has_valid_installable_frontmatter() -> None:
    """Regression test for the reported bug: a fully empty SKILL.md fails
    `nemoclaw skill install` with "missing YAML frontmatter" and the reset
    push fails for every skill. The stub must carry the `name:` key the CLI
    requires, matching the skill's own directory name, with no other content
    — the skill's actual instructions (Purpose/Steps/Notes) are what "blanked"
    means here."""
    for target in MENU_TARGETS:
        if target.kind != "skill":
            continue
        content = blank_content(target)
        skill_name = target.scratch_rel.rsplit("/", 1)[-1]
        assert content == f'---\nname: "{skill_name}"\n---\n'
        assert "Purpose" not in content
        assert "Steps" not in content


def test_run_reset_returns_false_when_any_push_fails(tmp_path: Path) -> None:
    def fake_run(argv, **kwargs):
        if argv[2] == "skill":  # fail the skill installs, let uploads succeed
            return _FakeCompleted(returncode=1, stderr="boom")
        return _FakeCompleted(returncode=0, stdout="ok")

    config = ConsoleConfig(
        sandbox_name="tenant-a", workspace_dir=tmp_path, editor_bin="true", editor_args=()
    )

    assert run_reset(config, run=fake_run) is False


def test_run_reset_argv_is_literal_no_shell_metacharacters(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _FakeCompleted(returncode=0, stdout="ok")

    config = ConsoleConfig(
        sandbox_name="tenant-a", workspace_dir=tmp_path, editor_bin="true", editor_args=()
    )
    run_reset(config, run=fake_run)

    for argv in calls:
        assert argv[0] == "nemoclaw"
        assert argv[1] == "tenant-a"
        assert all(isinstance(part, str) for part in argv)
