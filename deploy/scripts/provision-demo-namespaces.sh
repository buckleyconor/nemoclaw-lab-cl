#!/usr/bin/env bash
# provision-demo-namespaces.sh — Create N isolated NemoClaw demo namespaces on Charmed K8s.
#
# Each namespace gets:
#   - A full NemoClaw Helm release (gateway, orchestrator, mcp-tools, simulator, agent, redis)
#   - ResourceQuota + LimitRange (via the chart templates)
#   - NetworkPolicies per service (via the chart templates)
#
# Usage:
#   ./deploy/scripts/provision-demo-namespaces.sh [N] [TAG]
#   N    Number of demo namespaces to create (default: 30)
#   TAG  Image tag to deploy (default: v0.1.0)
#
# Prerequisites: kubectl + helm configured for the target cluster.

set -euo pipefail

CHART_DIR="$(cd "$(dirname "$0")/../helm/nemoclaw" && pwd)"
N="${1:-30}"
TAG="${2:-v0.1.0}"
REGISTRY="${REGISTRY:-registry.nemoclaw.lab}"
PACK_ID="${PACK_ID:-datacenter-xe9680}"

echo "==> Provisioning ${N} demo namespaces (tag=${TAG}, registry=${REGISTRY})"

for i in $(seq 1 "$N"); do
  NS="demo-$(printf '%02d' "$i")"
  RELEASE="nemoclaw"

  echo ""
  echo "--- Namespace: ${NS} ---"

  # Create namespace (idempotent)
  if ! kubectl get namespace "$NS" &>/dev/null; then
    kubectl create namespace "$NS"
    echo "    Created namespace ${NS}"
  else
    echo "    Namespace ${NS} already exists"
  fi

  # Label for network policy namespace selectors (future cross-ns policies)
  kubectl label namespace "$NS" \
    "nemoclaw.lab/demo-slot=${NS}" \
    --overwrite

  # Deploy the Helm release into this namespace
  helm upgrade --install "$RELEASE" "$CHART_DIR" \
    --namespace "$NS" \
    --values "${CHART_DIR}/values.prod.yaml" \
    --set "global.registry=${REGISTRY}" \
    --set "global.tag=${TAG}" \
    --set "global.packId=${PACK_ID}" \
    --set "ingress.host=nemoclaw-${NS}.dell-demo.lab" \
    --set "ingress.tls[0].secretName=nemoclaw-${NS}-tls" \
    --set "ingress.tls[0].hosts[0]=nemoclaw-${NS}.dell-demo.lab" \
    --wait \
    --timeout 5m

  echo "    Release ${RELEASE} deployed to ${NS}"
done

echo ""
echo "==> Done. ${N} demo namespaces provisioned."
echo ""
echo "Access URLs (add to /etc/hosts or configure DNS wildcard):"
for i in $(seq 1 "$N"); do
  NS="demo-$(printf '%02d' "$i")"
  echo "  https://nemoclaw-${NS}.dell-demo.lab"
done
