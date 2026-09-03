"""`deploy/scripts/lib/envfile.sh` — the .env reader/writer every deploy script shares.

Why this file exists: the deploy layer carries the incident history (atomic
writes, inline-comment stripping, refusing to clobber operator values) but had
no automated coverage at all, so regressions only surfaced on a fresh host.
Two scripts had each re-implemented `env_get` and dropped the comment
stripping, which made `doctor.sh` and `bootstrap.sh` disagree about the same
`.env` — the endpoint read green in one and 401 in the other.

The library is bash, so these drive it through a real shell rather than
reimplementing its semantics in Python.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[2] / "deploy" / "scripts" / "lib" / "envfile.sh"


def run_lib(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Source envfile.sh and run `script` in bash, from `cwd`."""
    return subprocess.run(
        ["bash", "-c", f"set -uo pipefail\nsource {LIB}\n{script}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def env_get(cwd: Path, key: str) -> str:
    proc = run_lib(f'env_get "{key}"', cwd)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.rstrip("\n")


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    return tmp_path / ".env"


# ─────────────────────────────────────────────────────────────────────────────
# env_get — reading
# ─────────────────────────────────────────────────────────────────────────────


def test_reads_a_plain_value(env_file: Path) -> None:
    env_file.write_text("LLM_MODEL=qwen3.6-35b-a3b-fp8\n")
    assert env_get(env_file.parent, "LLM_MODEL") == "qwen3.6-35b-a3b-fp8"


def test_strips_a_trailing_inline_comment(env_file: Path) -> None:
    """.env.example ships `LLM_API_KEY=CHANGE_ME   # REQUIRED — ...`.

    An operator who pastes a real key ahead of that comment must not send the
    comment text as part of the credential — the endpoint 401s and the cause
    is invisible.
    """
    env_file.write_text(
        "LLM_API_KEY=sk-real-key   # REQUIRED — the shared lab endpoint needs a real key\n"
    )
    assert env_get(env_file.parent, "LLM_API_KEY") == "sk-real-key"


def test_keeps_a_hash_that_is_not_a_comment(env_file: Path) -> None:
    """Only whitespace-then-# starts a comment, matching docker-compose."""
    env_file.write_text("LLM_BASE_URL=https://model.example.lab/v1#frag\n")
    assert env_get(env_file.parent, "LLM_BASE_URL") == "https://model.example.lab/v1#frag"


def test_commented_placeholder_reads_as_unset(env_file: Path) -> None:
    """`# KEY=...` in .env.example means "not set", not "set to that value"."""
    env_file.write_text("# LLM_PROXY_PORT=18100\n")
    assert env_get(env_file.parent, "LLM_PROXY_PORT") == ""


def test_missing_key_is_empty_and_does_not_fail(env_file: Path) -> None:
    env_file.write_text("PACK_ID=datacenter-xe9680\n")
    assert env_get(env_file.parent, "NOPE") == ""


def test_missing_file_is_empty_and_does_not_fail(tmp_path: Path) -> None:
    assert env_get(tmp_path, "LLM_MODEL") == ""


def test_value_containing_equals_survives(env_file: Path) -> None:
    """Only the first `=` separates key from value — tokens contain them."""
    env_file.write_text("OPENCLAW_HOOK_TOKEN=abc==def\n")
    assert env_get(env_file.parent, "OPENCLAW_HOOK_TOKEN") == "abc==def"


def test_first_occurrence_wins(env_file: Path) -> None:
    env_file.write_text("PACK_ID=first\nPACK_ID=second\n")
    assert env_get(env_file.parent, "PACK_ID") == "first"


# ─────────────────────────────────────────────────────────────────────────────
# env_upsert — writing
# ─────────────────────────────────────────────────────────────────────────────


def test_upsert_replaces_in_place_preserving_order(env_file: Path) -> None:
    env_file.write_text("A=1\nPACK_ID=old\nB=2\n")
    proc = run_lib("env_upsert PACK_ID laptop-fleet", env_file.parent)
    assert proc.returncode == 0, proc.stderr
    assert env_file.read_text() == "A=1\nPACK_ID=laptop-fleet\nB=2\n"


def test_upsert_appends_when_absent(env_file: Path) -> None:
    env_file.write_text("A=1\n")
    run_lib("env_upsert TERMINAL_BIND 172.17.0.1", env_file.parent)
    assert env_file.read_text() == "A=1\nTERMINAL_BIND=172.17.0.1\n"


def test_upsert_replaces_a_commented_placeholder_rather_than_duplicating(env_file: Path) -> None:
    """.env.example ships commented placeholders; leaving both would mean the
    commented line stays as misleading documentation of a stale value."""
    env_file.write_text("A=1\n# TERMINAL_BIND=172.17.0.1\nB=2\n")
    run_lib("env_upsert TERMINAL_BIND 172.18.0.1", env_file.parent)
    assert env_file.read_text() == "A=1\nTERMINAL_BIND=172.18.0.1\nB=2\n"


def test_upsert_handles_values_with_slashes_and_colons(env_file: Path) -> None:
    """URLs and hex tokens are the normal payload — a sed-based rewrite would
    need escaping here, which is why the library uses awk."""
    env_file.write_text("TERMINAL_WS_URL=placeholder\n")
    run_lib('env_upsert TERMINAL_WS_URL "ws://host.docker.internal:8005/ws"', env_file.parent)
    assert env_get(env_file.parent, "TERMINAL_WS_URL") == "ws://host.docker.internal:8005/ws"


def test_upsert_preserves_file_mode(env_file: Path) -> None:
    """.env holds the LLM key and the hook token. mktemp stages at 0600, so
    without `chmod --reference` a 0644 file would silently tighten (or a 0600
    one loosen) on every write."""
    env_file.write_text("A=1\n")
    env_file.chmod(0o600)
    run_lib("env_upsert A 2", env_file.parent)
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_upsert_leaves_no_temp_files_behind(env_file: Path) -> None:
    env_file.write_text("A=1\n")
    run_lib("env_upsert A 2", env_file.parent)
    assert [p.name for p in env_file.parent.iterdir()] == [".env"]


def test_upsert_creates_the_file_when_missing(tmp_path: Path) -> None:
    run_lib("env_upsert PACK_ID datacenter-xe9680", tmp_path)
    assert (tmp_path / ".env").read_text() == "PACK_ID=datacenter-xe9680\n"


# ─────────────────────────────────────────────────────────────────────────────
# env_set_checked — never clobber an operator's value
# ─────────────────────────────────────────────────────────────────────────────


def test_set_checked_writes_when_unset(env_file: Path) -> None:
    env_file.write_text("A=1\n")
    proc = run_lib("env_set_checked TERMINAL_BIND 172.17.0.1", env_file.parent)
    assert proc.returncode == 0
    assert env_get(env_file.parent, "TERMINAL_BIND") == "172.17.0.1"


def test_set_checked_refuses_to_clobber_a_different_value(env_file: Path) -> None:
    """bootstrap.sh auto-detects TERMINAL_BIND from docker0; an operator who
    set it deliberately must win, and must be told."""
    env_file.write_text("TERMINAL_BIND=10.1.2.3\n")
    proc = run_lib("env_set_checked TERMINAL_BIND 172.17.0.1", env_file.parent)
    assert proc.returncode == 1
    assert env_get(env_file.parent, "TERMINAL_BIND") == "10.1.2.3"
    assert "10.1.2.3" in proc.stderr


def test_set_checked_is_a_no_op_when_already_correct(env_file: Path) -> None:
    """No rewrite at all — no mtime churn, and no write for a concurrent
    reader to interleave with."""
    env_file.write_text("TERMINAL_BIND=172.17.0.1\n")
    before = env_file.stat().st_mtime_ns
    proc = run_lib("env_set_checked TERMINAL_BIND 172.17.0.1", env_file.parent)
    assert proc.returncode == 0
    assert env_file.stat().st_mtime_ns == before


# ─────────────────────────────────────────────────────────────────────────────
# bridge_ip
# ─────────────────────────────────────────────────────────────────────────────


def test_bridge_ip_returns_an_address(tmp_path: Path) -> None:
    """Detected from docker0, falling back to docker's stock default — the
    point is that it never returns empty, since callers build URLs with it."""
    proc = run_lib("bridge_ip", tmp_path)
    assert proc.returncode == 0, proc.stderr
    octets = proc.stdout.strip().split(".")
    assert len(octets) == 4 and all(o.isdigit() for o in octets)


# ─────────────────────────────────────────────────────────────────────────────
# No script may re-implement env_get (the doctor/bootstrap disagreement above)
# ─────────────────────────────────────────────────────────────────────────────


def test_no_deploy_script_redefines_env_get() -> None:
    scripts = (LIB.parent.parent).glob("*.sh")
    offenders = [
        p.name
        for p in scripts
        if any(line.lstrip().startswith("env_get()") for line in p.read_text().splitlines())
    ]
    assert offenders == [], (
        f"{offenders} redefine env_get instead of using lib/envfile.sh. Local copies have "
        "twice dropped the inline-comment stripping, making scripts disagree about the same .env."
    )
