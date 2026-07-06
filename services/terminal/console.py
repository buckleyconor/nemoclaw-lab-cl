"""Restricted operator console — TERMINAL_MODE=restricted's PTY-attached process.

Not a shell. This is the entire program the terminal daemon (`services/terminal/
main.py`) spawns onto the PTY when running in restricted mode (ADR-013, M9
30-tenant Kubernetes deployment): a numbered menu of exactly six edit targets —
SOUL.md, AGENTS.md, and the four infra-sentinel skills. Every target is a
hardcoded (sandbox path, scratch path) pair; no operator-typed string ever
reaches a subprocess argv, so there is no shell-injection surface to sanitize.

Per selection: best-effort `nemoclaw <name> download` seeds a scratch copy,
`vim -Z` (restricted vim — no `:!`, `:sh`, suspend, or writing another file)
edits it in place on the PTY, then `nemoclaw <name> upload` (SOUL.md/AGENTS.md)
or `nemoclaw <name> skill install` (skills) pushes it back. All subprocess
calls use literal argv lists, never `shell=True`.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

_SANDBOX_WORKSPACE = "/sandbox/.openclaw/workspace"


@dataclasses.dataclass(frozen=True)
class MenuTarget:
    key: str
    label: str
    sandbox_path: str
    kind: str  # "workspace_file" | "skill"
    scratch_rel: str  # path relative to the tenant's scratch workspace dir


MENU_TARGETS: tuple[MenuTarget, ...] = (
    MenuTarget("1", "Edit SOUL.md", f"{_SANDBOX_WORKSPACE}/SOUL.md", "workspace_file", "SOUL.md"),
    MenuTarget(
        "2", "Edit AGENTS.md", f"{_SANDBOX_WORKSPACE}/AGENTS.md", "workspace_file", "AGENTS.md"
    ),
    MenuTarget(
        "3",
        "Edit infra-sentinel-monitor/SKILL.md",
        f"{_SANDBOX_WORKSPACE}/skills/infra-sentinel-monitor",
        "skill",
        "skills/infra-sentinel-monitor",
    ),
    MenuTarget(
        "4",
        "Edit infra-sentinel-diagnose/SKILL.md",
        f"{_SANDBOX_WORKSPACE}/skills/infra-sentinel-diagnose",
        "skill",
        "skills/infra-sentinel-diagnose",
    ),
    MenuTarget(
        "5",
        "Edit infra-sentinel-notify/SKILL.md",
        f"{_SANDBOX_WORKSPACE}/skills/infra-sentinel-notify",
        "skill",
        "skills/infra-sentinel-notify",
    ),
    MenuTarget(
        "6",
        "Edit infra-sentinel-remediate/SKILL.md",
        f"{_SANDBOX_WORKSPACE}/skills/infra-sentinel-remediate",
        "skill",
        "skills/infra-sentinel-remediate",
    ),
)

QUIT_KEYS = ("q", "Q")

# ED2 (clear visible screen) + cursor-home. Deliberately leaves xterm.js's
# scrollback buffer alone (no CSI 3J) — history stays reviewable by scrolling
# up, but the *current* viewport never shows more than one menu at a time.
# Without this, vim's alternate-screen exit restores whatever was on screen
# before it launched (the stale pre-edit menu), and the next menu print then
# lands right below it — two menus visible at once.
_CLEAR_SCREEN = "\033[2J\033[H"
_GREEN = "\033[32m"
_RESET = "\033[0m"


def format_menu(
    sandbox_name: str,
    targets: tuple[MenuTarget, ...] = MENU_TARGETS,
    completed: frozenset[str] = frozenset(),
) -> str:
    lines = [f"NemoClaw operator console — tenant: {sandbox_name}"]
    for t in targets:
        if t.key in completed:
            lines.append(f"  {t.key}) {_GREEN}✓ {t.label}{_RESET}")
        else:
            lines.append(f"  {t.key}) {t.label}")
    lines.append("  q) Quit")
    lines.append("> ")
    return "\n".join(lines)


def find_target(
    selection: str, targets: tuple[MenuTarget, ...] = MENU_TARGETS
) -> MenuTarget | None:
    for t in targets:
        if t.key == selection.strip():
            return t
    return None


def edit_path(target: MenuTarget, workspace_dir: Path) -> Path:
    """The single file `vim -Z` is confined to for this target."""
    if target.kind == "skill":
        return workspace_dir / target.scratch_rel / "SKILL.md"
    return workspace_dir / target.scratch_rel


def download_argv(
    nemoclaw_bin: str, sandbox_name: str, target: MenuTarget, workspace_dir: Path
) -> list[str]:
    dest = workspace_dir / target.scratch_rel
    return [nemoclaw_bin, sandbox_name, "download", target.sandbox_path, str(dest)]


def push_argv(
    nemoclaw_bin: str, sandbox_name: str, target: MenuTarget, workspace_dir: Path
) -> list[str]:
    if target.kind == "skill":
        return [
            nemoclaw_bin,
            sandbox_name,
            "skill",
            "install",
            str(workspace_dir / target.scratch_rel),
        ]
    scratch = workspace_dir / target.scratch_rel
    return [nemoclaw_bin, sandbox_name, "upload", str(scratch), target.sandbox_path]


def editor_argv(editor_bin: str, editor_args: list[str], path: Path) -> list[str]:
    return [editor_bin, *editor_args, str(path)]


@dataclasses.dataclass(frozen=True)
class ConsoleConfig:
    sandbox_name: str
    workspace_dir: Path
    nemoclaw_bin: str = "nemoclaw"
    editor_bin: str = "vim"
    editor_args: tuple[str, ...] = ("-Z",)


def run_target(
    target: MenuTarget,
    config: ConsoleConfig,
    *,
    run: type[subprocess.run] = subprocess.run,  # type: ignore[valid-type]
    out=sys.stdout,
) -> bool:
    """Download-edit-push one target; return True iff the push succeeded."""
    path = edit_path(target, config.workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    dl_argv = download_argv(config.nemoclaw_bin, config.sandbox_name, target, config.workspace_dir)
    dl = run(dl_argv, capture_output=True, text=True, check=False)
    if dl.returncode != 0:
        print(f"[console] download skipped (not yet in sandbox?): {dl.stderr.strip()}", file=out)

    ed_argv = editor_argv(config.editor_bin, list(config.editor_args), path)
    run(ed_argv, check=False)

    push = run(
        push_argv(config.nemoclaw_bin, config.sandbox_name, target, config.workspace_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    print(push.stdout.strip(), file=out)
    if push.returncode != 0:
        print(f"[console] push FAILED: {push.stderr.strip()}", file=out)
        return False
    print("[console] pushed to sandbox.", file=out)
    return True


def _load_config() -> ConsoleConfig:
    sandbox_name = os.environ.get("SANDBOX_NAME", "")
    if not sandbox_name:
        raise RuntimeError(
            "SANDBOX_NAME is not set — refusing to start the restricted console. "
            "Set it alongside TERMINAL_MODE=restricted (see deploy/scripts/run-terminal-tenant.sh)."
        )
    workspace_dir = Path(
        os.environ.get(
            "TERMINAL_WORKSPACE_DIR", str(Path.home() / "nemoclaw-tenants" / sandbox_name / "work")
        )
    )
    editor_args = tuple(os.environ.get("TERMINAL_EDITOR_ARGS", "-Z").split())
    return ConsoleConfig(
        sandbox_name=sandbox_name,
        workspace_dir=workspace_dir,
        nemoclaw_bin=os.environ.get("TERMINAL_NEMOCLAW_BIN", "nemoclaw"),
        editor_bin=os.environ.get("TERMINAL_EDITOR_BIN", "vim"),
        editor_args=editor_args,
    )


def main() -> None:
    config = _load_config()
    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    completed: set[str] = set()

    while True:
        print(_CLEAR_SCREEN, end="")
        print(format_menu(config.sandbox_name, completed=frozenset(completed)), end="", flush=True)
        try:
            selection = input()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if selection.strip() in QUIT_KEYS:
            return
        target = find_target(selection)
        if target is None:
            print(f"[console] no such option: {selection!r}")
            continue
        if run_target(target, config):
            completed.add(target.key)
        try:
            input("\n[console] Press Enter to return to the menu...")
        except (EOFError, KeyboardInterrupt):
            print()
            return


if __name__ == "__main__":
    main()
