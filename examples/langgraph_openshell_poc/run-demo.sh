#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

EXAMPLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FABRIC_ROOT="$(cd "${EXAMPLE_ROOT}/../.." && pwd)"
OPENSHELL_ROOT="${OPENSHELL_ROOT:-$(cd "${FABRIC_ROOT}/../openshell" && pwd)}"
OPENSHELL_REV="${OPENSHELL_REV:-69a05ebb3b154e304a66fe80eed8504e889abc6d}"
GATEWAY_PORT="${OPENSHELL_POC_PORT:-18080}"
GATEWAY_START_TIMEOUT="${OPENSHELL_POC_GATEWAY_START_TIMEOUT:-900}"
POC_MODE="${OPENSHELL_POC_MODE:-both}"
export UV_CACHE_DIR="${FABRIC_ROOT}/.tmp/uv-cache"
GATEWAY_PID=""
SANDBOX_NAME="fab-courier-$$"
FABRIC_SANDBOX_NAME="fab-dev-$$"
SANDBOX_CREATED=""

for dependency in cargo cmake docker git python3 rustup tar uv; do
  if ! command -v "${dependency}" >/dev/null 2>&1; then
    echo "ERROR: ${dependency} is required to run the OpenShell POC" >&2
    exit 2
  fi
done
if [[ "${POC_MODE}" != "deployment" && "${POC_MODE}" != "development" && "${POC_MODE}" != "both" ]]; then
  echo "ERROR: OPENSHELL_POC_MODE must be deployment, development, or both" >&2
  exit 2
fi

mkdir -p "${FABRIC_ROOT}/.tmp"
STAGE_DIR="$(mktemp -d "${FABRIC_ROOT}/.tmp/portable-courier-image.XXXXXX")"
OPENSHELL_BUILD_DIR="${FABRIC_ROOT}/.tmp/openshell-baseline-${OPENSHELL_REV:0:12}"
OPENSHELL_CLI="${OPENSHELL_BUILD_DIR}/target/debug/openshell"

cleanup() {
  if [[ -n "${SANDBOX_CREATED}" ]] && [[ -x "${OPENSHELL_CLI}" ]]; then
    "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
      sandbox delete "${SANDBOX_NAME}" >/dev/null 2>&1 || true
  fi
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
cargo build -p nemo-fabric-openshell-provider
cp target/release/fabric-capsule-runner target/release/fabric-capsule-ctl "${STAGE_DIR}/"
cp -R adapter-contract/python "${STAGE_DIR}/adapter-contract"
cp -R adapters/python/common "${STAGE_DIR}/adapters-common"
mkdir -p "${STAGE_DIR}/examples"
cp examples/__init__.py "${STAGE_DIR}/examples/"
cp -R "${EXAMPLE_ROOT}" "${STAGE_DIR}/examples/langgraph_openshell_poc"
cp "${EXAMPLE_ROOT}/capsule.dockerignore" "${STAGE_DIR}/.dockerignore"
docker build -f "${EXAMPLE_ROOT}/capsule.Dockerfile" -t fabric-portable-courier:poc "${STAGE_DIR}"
CAPSULE_IMAGE="$(docker image inspect fabric-portable-courier:poc --format '{{.Id}}')"

mkdir -p "${OPENSHELL_BUILD_DIR}"
if [[ ! -f "${OPENSHELL_BUILD_DIR}/Cargo.toml" ]]; then
  git -C "${OPENSHELL_ROOT}" archive "${OPENSHELL_REV}" | tar -x -C "${OPENSHELL_BUILD_DIR}"
fi
cd "${OPENSHELL_BUILD_DIR}"
# The stock launcher has no feature override. Intercept only its gateway build
# so this portable demo can use OpenShell's own bundled-Z3 feature without
# patching or checking out a Fabric-specific OpenShell branch.
OPENSHELL_POC_CARGO="$(command -v cargo)"
cargo() {
  local argument
  for argument in "$@"; do
    if [[ "${argument}" == "openshell-server" ]]; then
      command "${OPENSHELL_POC_CARGO}" "$@" --features bundled-z3
      return
    fi
  done
  command "${OPENSHELL_POC_CARGO}" "$@"
}
export OPENSHELL_POC_CARGO
export -f cargo
mkdir -p "${FABRIC_ROOT}/.tmp/openshell-poc"
XDG_CONFIG_HOME="${FABRIC_ROOT}/.tmp/openshell-poc/config" \
OPENSHELL_SERVER_PORT="${GATEWAY_PORT}" \
OPENSHELL_DOCKER_GATEWAY_STATE_DIR="${FABRIC_ROOT}/.tmp/openshell-poc/gateway" \
OPENSHELL_DOCKER_GATEWAY_NAME="fabric-poc" \
OPENSHELL_SANDBOX_NAMESPACE="fabric-poc" \
OPENSHELL_SANDBOX_IMAGE_PULL_POLICY="Never" \
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

cd "${OPENSHELL_BUILD_DIR}"
cargo build -p openshell-cli --bin openshell

run_consumer() {
  cd "${FABRIC_ROOT}"
  NEMO_FABRIC_OPEN_SHELL_PROVIDER="${FABRIC_ROOT}/target/debug/fabric-environment-openshell" \
  PYTHONPATH="${FABRIC_ROOT}" \
  uv run --isolated --locked --no-default-groups \
    python -m examples.langgraph_openshell_poc.consumer \
    --gateway "http://127.0.0.1:${GATEWAY_PORT}" \
    --image "${CAPSULE_IMAGE}" \
    --base-dir "${FABRIC_ROOT}/.tmp/portable-courier" \
    "$@"
}

if [[ "${POC_MODE}" == "deployment" || "${POC_MODE}" == "both" ]]; then
  "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
    sandbox create \
    --name "${SANDBOX_NAME}" \
    --from "${CAPSULE_IMAGE}" \
    --policy "${EXAMPLE_ROOT}/policy.yaml" \
    --env PYTHONPATH=/opt/nemo-fabric \
    --detach \
    --no-tty \
    -- fabric-capsule-runner serve
  SANDBOX_CREATED="yes"
  SANDBOX_JSON="$(
    "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
      sandbox get "${SANDBOX_NAME}" --output json
  )"
  SANDBOX_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"${SANDBOX_JSON}")"

  run_consumer --sandbox-name "${SANDBOX_NAME}" --sandbox-id "${SANDBOX_ID}"
  "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
    sandbox get "${SANDBOX_NAME}" --output json >/dev/null
  echo "Verified deployment mode: Fabric detached; the caller-owned sandbox still exists."
fi

if [[ "${POC_MODE}" == "development" || "${POC_MODE}" == "both" ]]; then
  run_consumer --fabric-sandbox-name "${FABRIC_SANDBOX_NAME}"
  if "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
    sandbox get "${FABRIC_SANDBOX_NAME}" --output json >/dev/null 2>&1; then
    echo "ERROR: Fabric-owned sandbox still exists after release" >&2
    exit 1
  fi
  echo "Verified development mode: explicit Fabric release deleted the Fabric-owned sandbox."
fi
