#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

OPEN_SHELL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FABRIC_ROOT="$(cd "${OPEN_SHELL_ROOT}/../.." && pwd)"
OPENSHELL_VERSION="${OPENSHELL_VERSION:-v0.0.116}"
OPENSHELL_RELEASE_URL="https://github.com/NVIDIA/OpenShell/releases/download/${OPENSHELL_VERSION}"
GATEWAY_PORT="${OPENSHELL_EXAMPLE_PORT:-18080}"
GATEWAY_START_TIMEOUT="${OPENSHELL_EXAMPLE_GATEWAY_START_TIMEOUT:-120}"
EXAMPLE_MODE="${OPENSHELL_EXAMPLE_MODE:-deployment}"
if [[ $# -lt 1 || ! -f "$1" ]]; then
  echo "Usage: bash examples/openshell/run-demo.sh <workload/demo.sh> [consumer-options]" >&2
  exit 2
fi
WORKLOAD_ROOT="$(cd "$(dirname "$1")" && pwd)"
WORKLOAD_FILE="${WORKLOAD_ROOT}/$(basename "$1")"
shift
MODEL_ENV_ARGS=()
SUPPORTED_MODES=(deployment development)
IMAGE_DOCKERFILE="${WORKLOAD_ROOT}/runtime-image.Dockerfile"
POLICY_FILE="${WORKLOAD_ROOT}/policy.yaml"
source "${WORKLOAD_FILE}"
if [[ ! "${WORKLOAD_NAME:-}" =~ ^[a-z0-9-]+$ ]]; then
  echo "ERROR: workload must declare WORKLOAD_NAME using lowercase letters, digits, and hyphens" >&2
  exit 2
fi
for hook in stage_workload_image run_workload_consumer; do
  if ! declare -F "${hook}" >/dev/null; then
    echo "ERROR: workload must define ${hook}" >&2
    exit 2
  fi
done
IMAGE_TAG="${IMAGE_TAG:-fabric-${WORKLOAD_NAME}:example}"
RESULT_DIR="${OPENSHELL_EXAMPLE_RESULTS_DIR:-${RESULT_DIR:-${FABRIC_ROOT}/.tmp/${WORKLOAD_NAME}}}"
mkdir -p "${RESULT_DIR}"
RESULT_DIR="$(cd "${RESULT_DIR}" && pwd)"
export UV_CACHE_DIR="${FABRIC_ROOT}/.tmp/uv-cache"
DEMO_PYTHON_ENV="${FABRIC_ROOT}/.tmp/openshell-demo-venv"
GATEWAY_PID=""
SANDBOX_NAME="fab-$$"
FABRIC_SANDBOX_NAME="fab-dev-$$"
SANDBOX_CREATED=""
EVIDENCE_PID=""

for dependency in cargo rustc curl docker python3 tar uv; do
  if ! command -v "${dependency}" >/dev/null 2>&1; then
    echo "ERROR: ${dependency} is required to run the OpenShell example" >&2
    exit 2
  fi
done
if [[ "${EXAMPLE_MODE}" != "deployment" && "${EXAMPLE_MODE}" != "development" && "${EXAMPLE_MODE}" != "both" ]]; then
  echo "ERROR: OPENSHELL_EXAMPLE_MODE must be deployment, development, or both" >&2
  exit 2
fi

normalize_arch() {
  case "$1" in
    x86_64 | amd64) echo "x86_64" ;;
    aarch64 | arm64) echo "aarch64" ;;
    *) echo "ERROR: unsupported architecture: $1" >&2; exit 2 ;;
  esac
}

for requested_mode in deployment development; do
  if [[ "${EXAMPLE_MODE}" == "both" || "${EXAMPLE_MODE}" == "${requested_mode}" ]]; then
    if [[ " ${SUPPORTED_MODES[*]} " != *" ${requested_mode} "* ]]; then
      echo "ERROR: ${WORKLOAD_NAME} does not support ${requested_mode} mode" >&2
      exit 2
    fi
  fi
done
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: this source-building launcher requires a Linux host for Linux task binaries" >&2
  exit 2
fi
HOST_ARCH="$(normalize_arch "$(uname -m)")"
DAEMON_ARCH="$(normalize_arch "$(docker info --format '{{.Architecture}}')")"
if [[ "$(docker info --format '{{.OSType}}')" != "linux" || "${HOST_ARCH}" != "${DAEMON_ARCH}" ]]; then
  echo "ERROR: Docker must run Linux containers with the same architecture as the host" >&2
  exit 2
fi
RUST_HOST_TARGET="$(rustc -vV | sed -n 's/^host: //p')"
CLI_ASSET="openshell-${HOST_ARCH}-unknown-linux-musl.tar.gz"
GATEWAY_ASSET="openshell-gateway-${HOST_ARCH}-unknown-linux-gnu.tar.gz"
mkdir -p "${FABRIC_ROOT}/.tmp"
STAGE_DIR="$(mktemp -d "${FABRIC_ROOT}/.tmp/openshell-image.XXXXXX")"
OPENSHELL_CACHE_DIR="${FABRIC_ROOT}/.tmp/openshell-releases/${OPENSHELL_VERSION}"
OPENSHELL_BIN_DIR="${OPENSHELL_CACHE_DIR}/bin"
GATEWAY_STATE_DIR="$(mktemp -d "${FABRIC_ROOT}/.tmp/openshell-demo.XXXXXX")"

SUPERVISOR_ASSET="openshell-sandbox-${DAEMON_ARCH}-unknown-linux-musl.tar.gz"
OPENSHELL_CLI="${OPENSHELL_BIN_DIR}/openshell"
OPENSHELL_GATEWAY="${OPENSHELL_BIN_DIR}/openshell-gateway"
OPENSHELL_SUPERVISOR="${OPENSHELL_BIN_DIR}/openshell-sandbox"

download_release_asset() {
  local asset="$1"
  local destination="${OPENSHELL_CACHE_DIR}/${asset}"
  if [[ ! -f "${destination}" ]]; then
    echo "Downloading OpenShell ${OPENSHELL_VERSION} ${asset}..."
    curl --fail --location --retry 3 --show-error --silent \
      --output "${destination}.part" \
      "${OPENSHELL_RELEASE_URL}/${asset}"
    mv "${destination}.part" "${destination}"
  fi
}

verify_release_asset() {
  python3 - "$1" "$2" <<'PY'
import hashlib
from pathlib import Path
import sys

asset = Path(sys.argv[1])
checksums = Path(sys.argv[2])
expected = {
    name: digest
    for digest, name in (line.split() for line in checksums.read_text().splitlines())
}.get(asset.name)
if expected is None:
    raise SystemExit(f"ERROR: no checksum published for {asset.name}")
actual = hashlib.sha256(asset.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f"ERROR: checksum mismatch for {asset.name}")
PY
}

ensure_release_asset() {
  local asset="$1"
  local checksums="$2"
  download_release_asset "${asset}"
  if ! verify_release_asset "${OPENSHELL_CACHE_DIR}/${asset}" \
    "${OPENSHELL_CACHE_DIR}/${checksums}"; then
    echo "Cached ${asset} is invalid; downloading it again..." >&2
    rm -f -- "${OPENSHELL_CACHE_DIR}/${asset}"
    download_release_asset "${asset}"
    verify_release_asset "${OPENSHELL_CACHE_DIR}/${asset}" \
      "${OPENSHELL_CACHE_DIR}/${checksums}"
  fi
}

install_openshell_release() {
  mkdir -p "${OPENSHELL_CACHE_DIR}" "${OPENSHELL_BIN_DIR}"
  download_release_asset "openshell-checksums-sha256.txt"
  download_release_asset "openshell-gateway-checksums-sha256.txt"
  download_release_asset "openshell-sandbox-checksums-sha256.txt"
  ensure_release_asset "${CLI_ASSET}" "openshell-checksums-sha256.txt"
  ensure_release_asset "${GATEWAY_ASSET}" \
    "openshell-gateway-checksums-sha256.txt"
  ensure_release_asset "${SUPERVISOR_ASSET}" \
    "openshell-sandbox-checksums-sha256.txt"
  tar -xzf "${OPENSHELL_CACHE_DIR}/${CLI_ASSET}" -C "${OPENSHELL_BIN_DIR}"
  tar -xzf "${OPENSHELL_CACHE_DIR}/${GATEWAY_ASSET}" -C "${OPENSHELL_BIN_DIR}"
  tar -xzf "${OPENSHELL_CACHE_DIR}/${SUPERVISOR_ASSET}" -C "${OPENSHELL_BIN_DIR}"
}

cleanup() {
  local status=$?
  if [[ -n "${SANDBOX_CREATED}" ]]; then
    if declare -F collect_workload_diagnostics >/dev/null; then
      collect_workload_diagnostics || true
    fi
    cp "${GATEWAY_STATE_DIR}/gateway.log" "${RESULT_DIR}/openshell-gateway.log" 2>/dev/null || true
  fi
  if (( status != 0 )); then
    tail -n 30 "${GATEWAY_STATE_DIR}/gateway.log" >&2 || true
  fi
  if [[ -n "${SANDBOX_CREATED}" ]] && [[ -x "${OPENSHELL_CLI}" ]]; then
    "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
      sandbox delete "${SANDBOX_NAME}" >/dev/null 2>&1 || {
        echo "ERROR: failed to delete caller-owned sandbox ${SANDBOX_NAME}" >&2
        status=1
      }
    if (( status == 0 )); then
      echo "Deleted caller-owned sandbox ${SANDBOX_NAME}."
    fi
  fi
  if [[ -n "${EVIDENCE_PID}" ]]; then
    kill "${EVIDENCE_PID}" 2>/dev/null || true
    wait "${EVIDENCE_PID}" 2>/dev/null || true
  fi
  if [[ -n "${GATEWAY_PID}" ]]; then
    kill "${GATEWAY_PID}" 2>/dev/null || true
    wait "${GATEWAY_PID}" 2>/dev/null || true
  fi
  rm -rf -- "${STAGE_DIR}"
  rm -rf -- "${GATEWAY_STATE_DIR}"
  trap - EXIT
  exit "${status}"
}
trap cleanup EXIT

gateway_ready() {
  { true >/dev/tcp/127.0.0.1/"${GATEWAY_PORT}"; } 2>/dev/null
}

gateway_failure() {
  echo "$1; gateway log follows" >&2
  tail -n 80 "${GATEWAY_STATE_DIR}/gateway.log" >&2 || true
  exit 1
}

cd "${FABRIC_ROOT}"
cargo build --release --target "${RUST_HOST_TARGET}" --target-dir "${FABRIC_ROOT}/target" -p nemo-fabric-runtime-control --bins
cargo build --target "${RUST_HOST_TARGET}" --target-dir "${FABRIC_ROOT}/target" -p nemo-fabric-openshell-provider
UV_PROJECT_ENVIRONMENT="${DEMO_PYTHON_ENV}" \
  uv sync --locked --no-default-groups --reinstall-package nemo-fabric-runtime
PROVIDER_BINARY="${FABRIC_ROOT}/target/${RUST_HOST_TARGET}/debug/fabric-environment-openshell"
cp "target/${RUST_HOST_TARGET}/release/fabric-runtime-server" "target/${RUST_HOST_TARGET}/release/fabric-runtime-ctl" "${STAGE_DIR}/"
cp -R adapter-contract/python "${STAGE_DIR}/adapter-contract"
cp -R adapters/python/common "${STAGE_DIR}/adapters-common"
cp "${OPEN_SHELL_ROOT}/runtime-image.dockerignore" "${STAGE_DIR}/.dockerignore"
stage_workload_image
docker build -f "${IMAGE_DOCKERFILE}" -t "${IMAGE_TAG}" "${STAGE_DIR}"
RUNTIME_IMAGE="$(docker image inspect "${IMAGE_TAG}" --format '{{.Id}}')"
printf '%s\n' "${RUNTIME_IMAGE}" >"${RESULT_DIR}/image-id.txt"
if declare -F smoke_workload_image >/dev/null; then
  smoke_workload_image
fi

install_openshell_release
"${OPENSHELL_GATEWAY}" generate-certs \
  --output-dir "${GATEWAY_STATE_DIR}/tls" \
  --server-san "127.0.0.1" \
  --server-san "host.openshell.internal"
cat >"${GATEWAY_STATE_DIR}/gateway.toml" <<EOF
[openshell]
version = 1

[openshell.gateway]
name = "fabric-example"
compute_drivers = ["docker"]
disable_tls = true

[openshell.gateway.auth]
allow_unauthenticated_users = true

[openshell.gateway.gateway_jwt]
signing_key_path = "${GATEWAY_STATE_DIR}/tls/jwt/signing.pem"
public_key_path = "${GATEWAY_STATE_DIR}/tls/jwt/public.pem"
kid_path = "${GATEWAY_STATE_DIR}/tls/jwt/kid"
gateway_id = "fabric-example"
ttl_secs = 3600

[openshell.drivers.docker]
default_image = "${RUNTIME_IMAGE}"
image_pull_policy = "Never"
sandbox_namespace = "fabric-example"
grpc_endpoint = "http://host.openshell.internal:${GATEWAY_PORT}"
supervisor_bin = "${OPENSHELL_SUPERVISOR}"
EOF
"${OPENSHELL_GATEWAY}" \
  --config "${GATEWAY_STATE_DIR}/gateway.toml" \
  --bind-address 127.0.0.1 \
  --port "${GATEWAY_PORT}" \
  --drivers docker \
  --disable-tls \
  --db-url "sqlite:${GATEWAY_STATE_DIR}/gateway.db?mode=rwc" \
  >"${GATEWAY_STATE_DIR}/gateway.log" 2>&1 &
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

run_consumer() {
  cd "${FABRIC_ROOT}"
  NEMO_FABRIC_OPEN_SHELL_PROVIDER="${PROVIDER_BINARY}" \
  PYTHONPATH="${FABRIC_ROOT}" \
  UV_PROJECT_ENVIRONMENT="${DEMO_PYTHON_ENV}" \
    run_workload_consumer "$@"
}

if [[ "${EXAMPLE_MODE}" == "deployment" || "${EXAMPLE_MODE}" == "both" ]]; then
  "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
    sandbox create \
    --name "${SANDBOX_NAME}" \
    --from "${RUNTIME_IMAGE}" \
    --policy "${POLICY_FILE}" \
    --env PYTHONPATH=/opt/nemo-fabric \
    "${MODEL_ENV_ARGS[@]}" \
    --detach \
    --no-tty \
    -- fabric-runtime-server serve
  SANDBOX_CREATED="yes"
  SANDBOX_JSON="$(
    "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
      sandbox get "${SANDBOX_NAME}" --output json
  )"
  SANDBOX_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"${SANDBOX_JSON}")"

  run_consumer --sandbox-name "${SANDBOX_NAME}" --sandbox-id "${SANDBOX_ID}" "$@"
  "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
    sandbox get "${SANDBOX_NAME}" --output json >/dev/null
  echo "Verified deployment mode: Fabric detached; the caller-owned sandbox still exists."
fi

if [[ "${EXAMPLE_MODE}" == "development" || "${EXAMPLE_MODE}" == "both" ]]; then
  run_consumer --fabric-sandbox-name "${FABRIC_SANDBOX_NAME}" "$@"
  if "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
    sandbox get "${FABRIC_SANDBOX_NAME}" --output json >/dev/null 2>&1; then
    echo "ERROR: Fabric-owned sandbox still exists after release" >&2
    exit 1
  fi
  echo "Verified development mode: explicit Fabric release deleted the Fabric-owned sandbox."
fi
