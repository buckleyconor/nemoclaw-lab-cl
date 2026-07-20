# NemoClaw Infrastructure Sentinel — build, push, deploy targets.
#
# Quick start (MicroK8s / GB10):
#   make push REGISTRY=localhost:32000     # build arm64 + push to MicroK8s registry
#   make deploy                            # helm upgrade --install
#
# Multi-arch (push to Docker Hub or GHCR before prod deploy):
#   make push-multiarch REGISTRY=docker.io/youruser
#   make deploy REGISTRY=docker.io/youruser TAG=v0.1.0
#
# Charmed K8s prod (Intel/amd64 workers — images MUST be amd64):
#   make push PLATFORM=linux/amd64 REGISTRY=registry.nemoclaw.lab TAG=v0.1.0
#   make deploy REGISTRY=registry.nemoclaw.lab TAG=v0.1.0 \
#     VALUES=deploy/helm/nemoclaw/values.prod.yaml
#   (Building amd64 on the arm64 GB10 runs under QEMU emulation — slow,
#   especially the fastembed warmup step. Prefer an amd64 builder or CI.)
#
# Local dev (docker compose):
#   make up        # build + start full stack
#   make down      # stop and remove containers
#   make logs      # tail all service logs

REGISTRY    ?= localhost:32000
TAG         ?= latest
NAMESPACE   ?= nemoclaw
RELEASE     ?= nemoclaw
CHART       := deploy/helm/nemoclaw
PLATFORM    ?= linux/arm64   # override: linux/amd64 or linux/arm64,linux/amd64
VALUES      ?=               # optional extra helm values file, e.g. values.prod.yaml

BACKEND_IMAGE := $(REGISTRY)/nemoclaw-backend:$(TAG)
GATEWAY_IMAGE := $(REGISTRY)/nemoclaw-gateway:$(TAG)

.PHONY: help build push push-multiarch deploy undeploy up down logs switch-pack terminal hook-relay demo-up doctor doctor-fix test lint

help:
	@echo "Targets:"
	@echo "  demo-up        Bring up EVERYTHING the demo needs (stack + host daemons), then doctor"
	@echo "  doctor         Preflight: red/green check of all demo dependencies with fixes"
	@echo "  doctor-fix     Preflight + apply the fixes automatically (what the self-heal timer runs)"
	@echo "  build          Build images for PLATFORM=$(PLATFORM)"
	@echo "  push           Build + push to REGISTRY=$(REGISTRY)"
	@echo "  push-multiarch Build linux/arm64,linux/amd64 and push"
	@echo "  deploy         helm upgrade --install to NAMESPACE=$(NAMESPACE)"
	@echo "  undeploy       helm uninstall"
	@echo "  up             docker compose up --build"
	@echo "  down           docker compose down"
	@echo "  logs           docker compose logs -f"
	@echo "  switch-pack    Restart the stack bound to PACK_ID=<id> (e.g. make switch-pack PACK_ID=laptop-fleet)"
	@echo "  terminal       Run the embedded-terminal daemon on the host (ADR-012)"
	@echo "  hook-relay     Relay the OpenClaw wake hook onto the docker bridge (ADR-011)"
	@echo "  test           uv run pytest"
	@echo "  lint           uv run ruff check . && uv run ruff format --check ."

# ── Docker image targets ──────────────────────────────────────────────────────

build:
	docker buildx build \
	  --platform $(PLATFORM) \
	  --file docker/Dockerfile.backend \
	  --tag $(BACKEND_IMAGE) \
	  --load \
	  .
	docker buildx build \
	  --platform $(PLATFORM) \
	  --file docker/Dockerfile.gateway \
	  --tag $(GATEWAY_IMAGE) \
	  --load \
	  .

push:
	docker buildx build \
	  --platform $(PLATFORM) \
	  --file docker/Dockerfile.backend \
	  --tag $(BACKEND_IMAGE) \
	  --push \
	  .
	docker buildx build \
	  --platform $(PLATFORM) \
	  --file docker/Dockerfile.gateway \
	  --tag $(GATEWAY_IMAGE) \
	  --push \
	  .

push-multiarch:
	$(MAKE) push PLATFORM=linux/arm64,linux/amd64

# ── Helm targets ──────────────────────────────────────────────────────────────

deploy:
	helm upgrade --install $(RELEASE) $(CHART) \
	  --namespace $(NAMESPACE) \
	  --create-namespace \
	  $(if $(VALUES),-f $(VALUES)) \
	  --set global.registry=$(REGISTRY) \
	  --set global.tag=$(TAG) \
	  --wait

undeploy:
	helm uninstall $(RELEASE) --namespace $(NAMESPACE)

# ── Docker Compose targets ────────────────────────────────────────────────────

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

switch-pack:
	@test -n "$(PACK_ID)" || (echo "Usage: make switch-pack PACK_ID=<pack-id>"; exit 1)
	deploy/scripts/switch-pack.sh $(PACK_ID)

# ── Host processes (not Compose services — ADR-011/ADR-012/ADR-013) ──────────

terminal:
	./deploy/scripts/run-terminal.sh

# M9 (Kubernetes, 30 tenants): one restricted-console terminal daemon per
# tenant. Usage: make terminal-tenant TENANT=acme SANDBOX_NAME=acme-sandbox PORT=8006
terminal-tenant:
	./deploy/scripts/run-terminal-tenant.sh "$(TENANT)" "$(SANDBOX_NAME)" "$(PORT)"

hook-relay:
	./deploy/scripts/hook-relay.py

# One command to a working demo: compose stack + terminal daemon + hook-relay
# (started in the background if not already running), then the doctor preflight.
demo-up:
	./deploy/scripts/demo-up.sh

# Red/green preflight of every demo dependency, with the fix for anything down.
doctor:
	./deploy/scripts/doctor.sh

# Same preflight, but applies each fix automatically instead of just printing
# it. This is what deploy/systemd/nemoclaw-doctor.timer runs on a schedule so
# a dead terminal daemon/hook-relay/wake-hook forward self-heals.
doctor-fix:
	./deploy/scripts/doctor.sh --fix

# ── Dev quality targets ───────────────────────────────────────────────────────

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
