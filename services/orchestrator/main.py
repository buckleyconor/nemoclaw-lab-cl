"""Scenario Orchestrator — selection, rotation, fault injection. Implemented in M3."""

from fastapi import FastAPI

app = FastAPI(title="NemoClaw Orchestrator", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
