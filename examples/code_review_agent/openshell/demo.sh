#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Workload hooks sourced by the shared OpenShell provisioning launcher.
CODE_REVIEW_HARNESS="${CODE_REVIEW_HARNESS:-openclaw}"
case "${CODE_REVIEW_HARNESS}" in
  openclaw)
    IMAGE_DOCKERFILE="${WORKLOAD_ROOT}/runtime-image.Dockerfile"
    POLICY_FILE="${WORKLOAD_ROOT}/policy.yaml"
    ;;
  hermes)
    IMAGE_DOCKERFILE="${WORKLOAD_ROOT}/hermes-runtime-image.Dockerfile"
    POLICY_FILE="${WORKLOAD_ROOT}/hermes-policy.yaml"
    ;;
  *) echo "ERROR: CODE_REVIEW_HARNESS must be openclaw or hermes" >&2; exit 2 ;;
esac
IMAGE_TAG="fabric-${CODE_REVIEW_HARNESS}-code-review:example"
MODEL_ENV_ARGS=(--env FABRIC_LOCAL_INFERENCE_KEY=local-no-auth)
RESULT_DIR="${FABRIC_ROOT}/.tmp/${CODE_REVIEW_HARNESS}-code-review"

stage_code_review_image() {
  cp -R "${FABRIC_ROOT}/adapters/python/${CODE_REVIEW_HARNESS}" \
    "${STAGE_DIR}/adapters-${CODE_REVIEW_HARNESS}"
  mkdir -p "${STAGE_DIR}/examples/code_review_agent"
  cp -R "${FABRIC_ROOT}/examples/code_review_agent/repos" \
    "${FABRIC_ROOT}/examples/code_review_agent/skills" \
    "${WORKLOAD_ROOT}" "${STAGE_DIR}/examples/code_review_agent/"
  if [[ "${CODE_REVIEW_HARNESS}" == "hermes" ]]; then
    local revision="dccb84b92401234db294667ec203d3ac3dc1b87f"
    local cache="${FABRIC_ROOT}/.tmp/hermes-openshell"
    local source="${cache}/hermes-agent-${revision}"
    mkdir -p "${cache}"
    if [[ ! -f "${source}/pyproject.toml" ]]; then
      curl --fail --location --retry 3 --connect-timeout 15 --max-time 300 \
        --show-error --silent --output "${cache}/source.tar.gz.part" \
        "https://codeload.github.com/NousResearch/hermes-agent/tar.gz/${revision}"
      mv "${cache}/source.tar.gz.part" "${cache}/source.tar.gz"
      tar -xzf "${cache}/source.tar.gz" -C "${cache}"
    fi
    cp -R "${source}" "${STAGE_DIR}/hermes-agent"
    printf '%s\n' "${revision}" >"${STAGE_DIR}/hermes-agent/.fabric-source-revision"
  fi
}

smoke_code_review_image() {
  mkdir -p "${RESULT_DIR}"
  printf '%s\n' "${RUNTIME_IMAGE}" >"${RESULT_DIR}/image-id.txt"
  docker run --rm "${RUNTIME_IMAGE}" python3 -c 'import importlib,pathlib,subprocess,sys; importlib.import_module("nemo_fabric_adapters."+sys.argv[1]+".adapter"); assert pathlib.Path("/sandbox/repos/my-service/calculator.py").is_file(); assert pathlib.Path("/opt/nemo-fabric/skills/code-review/SKILL.md").is_file(); pathlib.Path("/sandbox/artifacts/image-smoke").touch(); print(sys.version); print(subprocess.check_output(["openclaw", "--version"],text=True).strip() if sys.argv[1]=="openclaw" else (importlib.import_module("run_agent").AIAgent.__name__ + " " + pathlib.Path("/opt/hermes-agent/.fabric-source-revision").read_text().strip()))' \
    "${CODE_REVIEW_HARNESS}"
}

run_code_review_consumer() {
  mkdir -p "${RESULT_DIR}"
  "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
    sandbox exec --name "${SANDBOX_NAME}" --no-tty --timeout 660 \
    -- python3 /opt/nemo-fabric/capture_evidence.py --harness "${CODE_REVIEW_HARNESS}" \
    >"${RESULT_DIR}/task-evidence.json" &
  EVIDENCE_PID=$!
  local label="review-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
  NEMO_FABRIC_OPEN_SHELL_PROVIDER="${FABRIC_ROOT}/target/debug/fabric-environment-openshell" \
  PYTHONPATH="${FABRIC_ROOT}" UV_PROJECT_ENVIRONMENT="${DEMO_PYTHON_ENV}" \
  uv run --no-sync python -m examples.code_review_agent \
    --variant "${CODE_REVIEW_HARNESS}" --environment openshell \
    --gateway "http://127.0.0.1:${GATEWAY_PORT}" --image "${RUNTIME_IMAGE}" \
    --model "${CODE_REVIEW_MODEL:-nvidia/nemotron-3-ultra-550b-a55b}" \
    --base-url "${CODE_REVIEW_BASE_URL:-http://10.86.19.10:8000/v1}" \
    --api-key-env FABRIC_LOCAL_INFERENCE_KEY \
    --input "Read calculator.py and /opt/nemo-fabric/skills/code-review/SKILL.md. Review correctness and missing tests without inventing a required return value. Remember the review label ${label} in this conversation; do not write it to a file." \
    --follow-up-input "What was the review label from my previous message? Then give one focused test recommendation for calculator.py." \
    --results-file "${RESULT_DIR}/results.json" "$@"
  wait "${EVIDENCE_PID}"
  EVIDENCE_PID=""
  PYTHONPATH="${FABRIC_ROOT}" UV_PROJECT_ENVIRONMENT="${DEMO_PYTHON_ENV}" \
    uv run --no-sync python -m examples.code_review_agent.openshell.verify_run \
    --harness "${CODE_REVIEW_HARNESS}" --results-file "${RESULT_DIR}/results.json" --label "${label}"
}

collect_code_review_diagnostics() {
  mkdir -p "${RESULT_DIR}"
  if [[ "${CODE_REVIEW_HARNESS}" == "openclaw" ]]; then
    "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
      sandbox exec --name "${SANDBOX_NAME}" --no-tty --timeout 15 -- python3 -c 'import glob,json; [print(json.loads(line).get("1", "")) for path in glob.glob("/tmp/openclaw/*.log") for line in open(path) if "model-fetch" in line or "ERROR" in line or "WARN" in line]' \
      >"${RESULT_DIR}/gateway-diagnostics.log" 2>&1 || true
  fi
  cp "${GATEWAY_STATE_DIR}/gateway.log" "${RESULT_DIR}/openshell-gateway.log" || true
}
