# NemoClaw Infrastructure Sentinel

Autonomous AIOps demo lab: NemoClaw agents that monitor GPU server infrastructure,
detect hardware faults, analyse logs, match Dell KB articles, and remediate — with
human approval — on Dell + NVIDIA hardware.

See [`docs/`](docs/) for the full architecture, ADRs, lab guide, and pack authoring guide.

## Architecture

```
Pack (content) → Framework (agent + services) → Demo
```

One agent loop, one set of services. Swap the **Domain Pack** to switch verticals
(datacenter servers → laptop fleet → oil & gas rigs) with no code change.

## Quick start (dev / GB10)

```bash
# Prerequisites: uv, Docker buildx, Helm, kubectl, MicroK8s on arm64
cp .env.example .env          # fill in VLLM_BASE_URL / VLLM_API_KEY
helm install nemoclaw deploy/helm -f deploy/helm/values-gb10.yaml
kubectl port-forward svc/gateway 8080:80
# open http://localhost:8080
```

## Run tests

```bash
uv run pytest                 # unit + integration (stub LLM)
```

## Project layout

```
services/     FastAPI services: simulator, orchestrator, gateway, mcp_tools
libs/common/  Shared Pydantic models, pack loader, scenario loader
agent/        NemoClaw v0.0.56 config, prompt, tool wiring
packs/        Domain Pack content per vertical
ui/           React (Vite) dashboard
deploy/helm/  Helm chart — values-gb10.yaml (arm64) / values-prod.yaml (amd64)
docs/         Lab guide, pack authoring guide, ADRs, API docs
tests/        unit / integration / e2e
```

## Build milestones

| Milestone | Status |
|-----------|--------|
| M0 Repo & scaffolding | ✅ |
| M1 Pack contract + data model + flagship pack | ⬜ |
| M2 Simulator engine + Redfish surface | ⬜ |
| M3 Scenario Orchestrator | ⬜ |
| M4 MCP tool servers + semantic KB | ⬜ |
| M5 Gateway + React dashboard + approval gate | ⬜ |
| M6 NemoClaw agent integration | ⬜ |
| M7 Package & deploy on GB10 | ⬜ |
| M8 Extensibility: second pack + scaffolds | ⬜ |
| M9 Prod hardening (Charmed K8s, 30 users) | ⬜ |
