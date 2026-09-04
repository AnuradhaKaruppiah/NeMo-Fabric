#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

EXAMPLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FABRIC_ROOT="$(cd "${EXAMPLE_ROOT}/../.." && pwd)"
OPENSHELL_ROOT="${OPENSHELL_ROOT:-$(cd "${FABRIC_ROOT}/../openshell" && pwd)}"
GATEWAY_PORT="${OPENSHELL_POC_PORT:-18080}"
GATEWAY_START_TIMEOUT="${OPENSHELL_POC_GATEWAY_START_TIMEOUT:-900}"
export UV_CACHE_DIR="${FABRIC_ROOT}/.tmp/uv-cache"
GATEWAY_PID=""

for dependency in cargo cmake docker rustup uv; do
  if ! command -v "${dependency}" >/dev/null 2>&1; then
    echo "ERROR: ${dependency} is required to run the OpenShell POC" >&2
    exit 2
  fi
done

mkdir -p "${FABRIC_ROOT}/.tmp"
STAGE_DIR="$(mktemp -d "${FABRIC_ROOT}/.tmp/portable-courier-image.XXXXXX")"

cleanup() {
  if [[ -n "${GATEWAY_PID}" ]]; then
    kill "${GATEWAY_PID}" 2>/dev/null || true
    wait "${GATEWAY_PID}" 2>/dev/null || true
  fi
  rm -rf -- "${STAGE_DIR}"
}
trap cleanup EXIT

gateway_ready() {
  { true >/dev/tcp/127.0.0.1/"${GATEWAY_PORT}"; } 2>/dev/null
}

gateway_failure() {
  echo "$1; gateway log follows" >&2
  tail -n 80 "${FABRIC_ROOT}/.tmp/openshell-poc/gateway.log" >&2 || true
  exit 1
}

cd "${FABRIC_ROOT}"
cargo build --release -p nemo-fabric-capsule --bins
cp target/release/fabric-capsule-runner target/release/fabric-capsule-ctl "${STAGE_DIR}/"
cp -R adapter-contract/python "${STAGE_DIR}/adapter-contract"
cp -R adapters/python/common "${STAGE_DIR}/adapters-common"
mkdir -p "${STAGE_DIR}/examples"
cp examples/__init__.py "${STAGE_DIR}/examples/"
cp -R "${EXAMPLE_ROOT}" "${STAGE_DIR}/examples/langgraph_openshell_poc"
cp "${EXAMPLE_ROOT}/capsule.dockerignore" "${STAGE_DIR}/.dockerignore"
docker build -f "${EXAMPLE_ROOT}/capsule.Dockerfile" -t fabric-portable-courier:poc "${STAGE_DIR}"
CAPSULE_IMAGE="$(docker image inspect fabric-portable-courier:poc --format '{{.Id}}')"

cd "${OPENSHELL_ROOT}"
cargo build -p openshell-fabric-provider
mkdir -p "${FABRIC_ROOT}/.tmp/openshell-poc"
XDG_CONFIG_HOME="${FABRIC_ROOT}/.tmp/openshell-poc/config" \
OPENSHELL_SERVER_PORT="${GATEWAY_PORT}" \
OPENSHELL_DOCKER_GATEWAY_STATE_DIR="${FABRIC_ROOT}/.tmp/openshell-poc/gateway" \
OPENSHELL_DOCKER_GATEWAY_NAME="fabric-poc" \
OPENSHELL_SANDBOX_NAMESPACE="fabric-poc" \
OPENSHELL_SANDBOX_IMAGE_PULL_POLICY="Never" \
OPENSHELL_GATEWAY_FEATURES="bundled-z3" \
bash tasks/scripts/gateway-docker.sh \
  >"${FABRIC_ROOT}/.tmp/openshell-poc/gateway.log" 2>&1 &
GATEWAY_PID=$!

gateway_deadline=$((SECONDS + GATEWAY_START_TIMEOUT))
until gateway_ready; do
  if ! kill -0 "${GATEWAY_PID}" 2>/dev/null; then
    gateway_failure "OpenShell gateway exited during startup"
  fi
  if (( SECONDS >= gateway_deadline )); then
    gateway_failure "OpenShell gateway did not become ready within ${GATEWAY_START_TIMEOUT}s"
  fi
  sleep 1
done

cd "${FABRIC_ROOT}"
NEMO_FABRIC_OPEN_SHELL_PROVIDER="${OPENSHELL_ROOT}/target/debug/fabric-environment-openshell" \
PYTHONPATH="${FABRIC_ROOT}" \
uv run --isolated --locked --no-default-groups \
  python -m examples.langgraph_openshell_poc.consumer \
  --gateway "http://127.0.0.1:${GATEWAY_PORT}" \
  --image "${CAPSULE_IMAGE}" \
  --base-dir "${FABRIC_ROOT}/.tmp/portable-courier"
