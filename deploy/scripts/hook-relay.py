#!/usr/bin/env python3
"""Relay the OpenClaw wake-hook port onto the docker bridge (ADR-011).

openshell publishes the infra-sentinel dashboard/webhook on the host as an
SSH forward hard-bound to 127.0.0.1:18790, and it refuses a second forward
on the same port — so the Gateway container, which reaches the host only via
the docker bridge (host.docker.internal = 172.17.0.1 on Linux, never
loopback), cannot dial the wake hook directly. This relay binds the bridge
address and dials the loopback forward per connection, so it keeps working
across sandbox/tunnel restarts without touching the openshell-managed
forward.

Host-internal only: the bridge address is reachable from containers and the
host, and ufw must still allow the compose subnet in (SPEC-EMBEDDED-TERMINAL
§6). Never point HOOK_RELAY_BIND at a LAN-facing interface.
"""

import asyncio
import contextlib
import os

BIND = os.environ.get("HOOK_RELAY_BIND", "172.17.0.1")
PORT = int(os.environ.get("HOOK_RELAY_PORT", "18790"))
TARGET_HOST = os.environ.get("HOOK_RELAY_TARGET_HOST", "127.0.0.1")
TARGET_PORT = int(os.environ.get("HOOK_RELAY_TARGET_PORT", str(PORT)))


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(ConnectionResetError, BrokenPipeError):
            await writer.wait_closed()


async def _handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    try:
        up_r, up_w = await asyncio.wait_for(
            asyncio.open_connection(TARGET_HOST, TARGET_PORT), timeout=5
        )
    except (OSError, TimeoutError):
        client_w.close()
        return
    await asyncio.gather(_pump(client_r, up_w), _pump(up_r, client_w))


async def main() -> None:
    server = await asyncio.start_server(_handle, BIND, PORT)
    print(f"wake-hook relay: {BIND}:{PORT} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
