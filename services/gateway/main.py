"""Gateway — HITL approval gate, SSE, notification inbox, activity feed. Implemented in M5."""

from fastapi import FastAPI

app = FastAPI(title="NemoClaw Gateway", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
