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

BACKEND_IMAGE := $(REGISTRY)/nemoclaw-backend:$(TAG)
GATEWAY_IMAGE := $(REGISTRY)/nemoclaw-gateway:$(TAG)

.PHONY: help build push push-multiarch deploy undeploy up down logs terminal hook-relay test lint

help:
	@echo "Targets:"
	@echo "  build          Build images for PLATFORM=$(PLATFORM)"
	@echo "  push           Build + push to REGISTRY=$(REGISTRY)"
	@echo "  push-multiarch Build linux/arm64,linux/amd64 and push"
	@echo "  deploy         helm upgrade --install to NAMESPACE=$(NAMESPACE)"
	@echo "  undeploy       helm uninstall"
	@echo "  up             docker compose up --build"
	@echo "  down           docker compose down"
	@echo "  logs           docker compose logs -f"
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

# ── Host processes (not Compose services — ADR-011/ADR-012) ──────────────────

terminal:
	./deploy/scripts/run-terminal.sh

hook-relay:
	./deploy/scripts/hook-relay.py

# ── Dev quality targets ───────────────────────────────────────────────────────

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
