"""Terminal daemon — host-side PTY backend for the embedded operator terminal.

Runs ON THE LAB HOST, never in a container (ADR-012): the `nemoclaw` /
`openclaw` / `openshell` CLIs, their state, and the Docker daemon that runs
the OpenShell sandboxes all live here. The dashboard reaches it through the
Gateway's WS proxy (`/api/terminal/ws`), which injects the bearer token.

Start via `make terminal` (deploy/scripts/run-terminal.sh). Binds a
host-local address only (loopback, or the docker bridge IP so the
containerized Gateway can reach it — see run-terminal.sh); requires
TERMINAL_TOKEN. See docs/SPEC-EMBEDDED-TERMINAL.md §4.

TERMINAL_MODE selects what's spawned onto the PTY:
  "shell" (default)   full login shell (`/bin/bash -l`) — local dev (M12).
  "restricted"         `services.terminal.console`'s locked-down edit menu —
                       the M9 30-tenant Kubernetes deployment (ADR-013).
                       Requires SANDBOX_NAME; one daemon process per tenant
                       (deploy/scripts/run-terminal-tenant.sh).

Wire protocol (per connection — one WS, one PTY, one shell):
  binary frames        raw terminal bytes, both directions
  text  frames (in)    {"type": "resize", "cols": C, "rows": R}
  text  frames (out)   {"type": "exit", "code": N}   just before close
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import secrets
import signal
import struct
import subprocess
import sys
import termios
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

_REPO_ROOT = Path(__file__).parent.parent.parent
_READ_CHUNK = 65536


def _require_token() -> str:
    token = os.environ.get("TERMINAL_TOKEN", "")
    if not token:
        raise RuntimeError(
            "TERMINAL_TOKEN is not set — refusing to start. "
            "Use deploy/scripts/run-terminal.sh (or `make terminal`), which "
            "generates one and prints the matching Gateway .env lines."
        )
    return token


def _authorized(ws: WebSocket, token: str) -> bool:
    header = ws.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    return scheme.lower() == "bearer" and secrets.compare_digest(presented, token)


def _require_sandbox_name() -> None:
    """Restricted mode (ADR-013) needs a fixed target sandbox — fail fast, like the token."""
    if not os.environ.get("SANDBOX_NAME", ""):
        raise RuntimeError(
            "TERMINAL_MODE=restricted requires SANDBOX_NAME to be set — refusing to "
            "start. See deploy/scripts/run-terminal-tenant.sh."
        )


def _shell_argv() -> list[str]:
    """The command spawned onto the PTY: a full login shell, or the restricted
    operator console (ADR-013) when TERMINAL_MODE=restricted."""
    if os.environ.get("TERMINAL_MODE", "shell") == "restricted":
        return [sys.executable, "-m", "services.terminal.console"]
    return ["/bin/bash", "-l"]


def _spawn_shell() -> tuple[subprocess.Popen[bytes], int]:
    """Spawn the session command (see `_shell_argv`) in a fresh PTY; return (process, master fd)."""
    master_fd, slave_fd = os.openpty()

    def _become_session_leader() -> None:
        # New session + adopt the PTY (already dup'd onto stdin) as the
        # controlling terminal, so job control and Ctrl-C work in the shell.
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    try:
        proc = subprocess.Popen(
            _shell_argv(),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=_REPO_ROOT,
            env={**os.environ, "TERM": "xterm-256color"},
            preexec_fn=_become_session_leader,
        )
    except BaseException:
        os.close(master_fd)
        raise
    finally:
        os.close(slave_fd)
    return proc, master_fd


def _resize_pty(master_fd: int, cols: int, rows: int) -> None:
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)


async def _pump_pty_to_ws(ws: WebSocket, master_fd: int) -> None:
    """Forward PTY output to the WS as binary frames until EOF/EIO."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _on_readable() -> None:
        try:
            data = os.read(master_fd, _READ_CHUNK)
        except OSError:  # EIO: slave side closed (shell exited)
            data = b""
        if data:
            queue.put_nowait(data)
        else:
            loop.remove_reader(master_fd)
            queue.put_nowait(None)

    loop.add_reader(master_fd, _on_readable)
    try:
        while True:
            data = await queue.get()
            if data is None:
                break
            await ws.send_bytes(data)
    finally:
        with contextlib.suppress(ValueError, OSError):
            loop.remove_reader(master_fd)


async def _pump_ws_to_pty(ws: WebSocket, master_fd: int) -> None:
    """Forward WS input to the PTY; text frames are JSON control messages."""
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            break
        data = message.get("bytes")
        if data:
            os.write(master_fd, data)
            continue
        text = message.get("text")
        if not text:
            continue
        try:
            control = json.loads(text)
        except ValueError:
            continue
        if control.get("type") == "resize":
            with contextlib.suppress(OSError, TypeError, ValueError):
                _resize_pty(master_fd, int(control["cols"]), int(control["rows"]))


def _terminate_session(proc: subprocess.Popen[bytes]) -> None:
    """SIGHUP then SIGKILL the shell's whole process group."""
    if proc.poll() is not None:
        return
    for sig in (signal.SIGHUP, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, sig)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=2.0)
            return


def create_app(token: str | None = None) -> FastAPI:
    resolved_token = token or _require_token()
    if os.environ.get("TERMINAL_MODE", "shell") == "restricted":
        _require_sandbox_name()

    app = FastAPI(
        title="NemoClaw Terminal Daemon",
        version="0.1.0",
        description="PTY backend for the dashboard's embedded operator terminal (ADR-012).",
    )

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def terminal_ws(ws: WebSocket) -> None:
        if not _authorized(ws, resolved_token):
            # 1008 = policy violation; nothing is spawned for bad tokens.
            await ws.close(code=1008)
            return
        await ws.accept()

        proc, master_fd = _spawn_shell()
        loop = asyncio.get_running_loop()
        try:
            output = asyncio.ensure_future(_pump_pty_to_ws(ws, master_fd))
            inputs = asyncio.ensure_future(_pump_ws_to_pty(ws, master_fd))
            done, pending = await asyncio.wait(
                {output, inputs}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            for task in done:
                with contextlib.suppress(Exception):
                    task.result()

            if output in done and inputs not in done:
                # Shell exited on its own — report the exit code, then close.
                code = await loop.run_in_executor(None, proc.wait)
                with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                    await ws.send_text(json.dumps({"type": "exit", "code": code}))
                    await ws.close()
        finally:
            await loop.run_in_executor(None, _terminate_session, proc)
            with contextlib.suppress(OSError):
                os.close(master_fd)

    return app


# No module-level app: creating one requires TERMINAL_TOKEN, and importing
# this module (tests, tooling) must not. run-terminal.sh starts uvicorn with
# --factory services.terminal.main:create_app, which enforces the token gate.
