"""Simulator service — asset state engine + inject/clear control. Implemented in M2."""

from fastapi import FastAPI

app = FastAPI(title="NemoClaw Simulator", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
