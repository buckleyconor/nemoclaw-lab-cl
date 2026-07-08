"""Restricted operator console — TERMINAL_MODE=restricted's PTY-attached process.

Not a shell. This is the entire program the terminal daemon (`services/terminal/
main.py`) spawns onto the PTY when running in restricted mode (ADR-013, M9
30-tenant Kubernetes deployment): a numbered menu of exactly six edit targets —
SOUL.md, AGENTS.md, and the four infra-sentinel skills — plus a reset action
(option 7) that blanks and re-pushes all six. Every target is a hardcoded
(sandbox path, scratch path) pair; no operator-typed string ever reaches a
subprocess argv, so there is no shell-injection surface to sanitize.

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
# Where `nemoclaw <sandbox> skill install <dir>` actually deploys a skill —
# distinct from _SANDBOX_WORKSPACE above, and *not* a path under it. A skill's
# real, agent-loaded SKILL.md always lives here, keyed by the `name:` field in
# its frontmatter, regardless of which workspace directory it was edited from.
_SANDBOX_SKILLS = "/sandbox/.openclaw/skills"


@dataclasses.dataclass(frozen=True)
class MenuTarget:
    key: str
    label: str
    sandbox_path: str
    kind: str  # "workspace_file" | "skill"
    scratch_rel: str  # path relative to the tenant's scratch workspace dir


MENU_TARGETS: tuple[MenuTarget, ...] = (
    MenuTarget(
        "1",
        "Edit SOUL.md and push into the OpenShell sandbox automatically",
        f"{_SANDBOX_WORKSPACE}/SOUL.md",
        "workspace_file",
        "SOUL.md",
    ),
    MenuTarget(
        "2",
        "Edit AGENTS.md and push into the OpenShell sandbox automatically",
        f"{_SANDBOX_WORKSPACE}/AGENTS.md",
        "workspace_file",
        "AGENTS.md",
    ),
    MenuTarget(
        "3",
        "Edit infra-sentinel-monitor/SKILL.md and install in the NemoClaw agent",
        f"{_SANDBOX_SKILLS}/infra-sentinel-monitor",
        "skill",
        "skills/infra-sentinel-monitor",
    ),
    MenuTarget(
        "4",
        "Edit infra-sentinel-diagnose/SKILL.md and install in the NemoClaw agent",
        f"{_SANDBOX_SKILLS}/infra-sentinel-diagnose",
        "skill",
        "skills/infra-sentinel-diagnose",
    ),
    MenuTarget(
        "5",
        "Edit infra-sentinel-notify/SKILL.md and install in the NemoClaw agent",
        f"{_SANDBOX_SKILLS}/infra-sentinel-notify",
        "skill",
        "skills/infra-sentinel-notify",
    ),
    MenuTarget(
        "6",
        "Edit infra-sentinel-remediate/SKILL.md and install in the NemoClaw agent",
        f"{_SANDBOX_SKILLS}/infra-sentinel-remediate",
        "skill",
        "skills/infra-sentinel-remediate",
    ),
)

RESET_KEY = "7"
RESET_LABEL = "Reset all config (SOUL.md, AGENTS.md and all SKILL.md files are blanked)"

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
    lines.append(f"  {RESET_KEY}) {RESET_LABEL}")
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


def validate_skill_content(text: str) -> str | None:
    """Check a SKILL.md's frontmatter is installable; return an error or None.

    `nemoclaw skill install` needs YAML frontmatter with a `name:` key to
    know which sandbox skill slot to deploy to. A mangled paste (the common
    failure: text pasted in vim normal mode) breaks this in ways the install
    may not surface clearly — and a "pushed" checkmark on broken content is
    exactly the silent-blank-skill state that once presented as "the agent
    never detects faults". Deliberately shallow: frontmatter shape only, no
    opinion on the skill's actual instructions.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "file must start with a '---' frontmatter line"
    try:
        close = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return "frontmatter '---' is never closed"
    if not any(line.lstrip().startswith("name:") for line in lines[1:close]):
        return "frontmatter has no 'name:' key"
    return None


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

    if target.kind == "skill":
        error = validate_skill_content(path.read_text() if path.exists() else "")
        if error is not None:
            print(f"[console] NOT pushed — {error}.", file=out)
            print(
                "[console] The paste likely got mangled (did you press 'i' before "
                "pasting?). Pick the same menu item and try again.",
                file=out,
            )
            return False

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
    # The "(target=N)" suffix is a stable, machine-parseable marker the
    # embedded terminal panel (ui/src/components/TerminalPanel.tsx) watches
    # for in the raw PTY byte stream — it fires immediately on push success,
    # unlike the green checkmark in format_menu(), which only appears on the
    # *next* full menu redraw (after "Press Enter to return to the menu...").
    print(f"[console] pushed to sandbox. (target={target.key})", file=out)
    return True


def _skill_name(target: MenuTarget) -> str:
    """Directory-name portion of scratch_rel, e.g. "infra-sentinel-monitor"."""
    return target.scratch_rel.rsplit("/", 1)[-1]


def blank_content(target: MenuTarget) -> str:
    """The "blanked" body written for a target during reset.

    SOUL.md / AGENTS.md are plain workspace files with no format requirement,
    so blank means a truly empty string. A skill's SKILL.md is different:
    `nemoclaw skill install` parses YAML frontmatter and requires a `name:`
    key to know which sandbox skill slot to deploy to — an empty file fails
    that parse before anything is uploaded (verified against a live sandbox:
    the failed push never touches the previously-installed skill, so nothing
    is silently uninstalled, but the reset action itself was a no-op for
    skills). This stub keeps the frontmatter's mandatory `name:` and drops
    every actual instruction — Purpose/Steps/Notes — so the skill's behavior
    is genuinely blank while the file stays installable.
    """
    if target.kind == "skill":
        return f'---\nname: "{_skill_name(target)}"\n---\n'
    return ""


def run_reset(
    config: ConsoleConfig,
    *,
    targets: tuple[MenuTarget, ...] = MENU_TARGETS,
    run: type[subprocess.run] = subprocess.run,  # type: ignore[valid-type]
    out=sys.stdout,
) -> bool:
    """Blank every target's scratch file and push all six into the sandbox.

    No download and no editor: the scratch copy is overwritten with
    blank_content() and pushed with the same literal-argv upload /
    skill-install calls run_target uses. Returns True iff every push
    succeeded.
    """
    all_ok = True
    for target in targets:
        path = edit_path(target, config.workspace_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(blank_content(target))

        push = run(
            push_argv(config.nemoclaw_bin, config.sandbox_name, target, config.workspace_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        name = target.scratch_rel if target.kind == "workspace_file" else f"{target.scratch_rel}/SKILL.md"
        if push.returncode != 0:
            print(f"[console] reset push FAILED for {name}: {push.stderr.strip()}", file=out)
            all_ok = False
        else:
            print(f"[console] blanked and pushed {name}", file=out)
    if all_ok:
        print("[console] reset complete — all config files blanked in the sandbox.", file=out)
    return all_ok


def load_config() -> ConsoleConfig:
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
    config = load_config()
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
        if selection.strip() == RESET_KEY:
            try:
                confirm = input(
                    "[console] This blanks SOUL.md, AGENTS.md and all four SKILL.md "
                    "files in the sandbox. Type 'yes' to continue: "
                )
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if confirm.strip().lower() == "yes":
                if run_reset(config):
                    completed.clear()  # nothing is "configured" any more
            else:
                print("[console] reset cancelled.")
            try:
                input("\n[console] Press Enter to return to the menu...")
            except (EOFError, KeyboardInterrupt):
                print()
                return
            continue
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
