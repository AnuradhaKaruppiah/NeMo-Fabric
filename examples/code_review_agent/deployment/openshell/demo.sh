#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Workload hooks used by examples/openshell/run-demo.sh.
WORKLOAD_NAME=code-review
SUPPORTED_MODES=(caller_owned)
CODE_REVIEW_HARNESS="${CODE_REVIEW_HARNESS:-openclaw}"
CODE_REVIEW_RELAY="${CODE_REVIEW_RELAY:-0}"
if [[ -z "${CODE_REVIEW_BASE_URL:-}" ]]; then
  echo "ERROR: set CODE_REVIEW_BASE_URL to a model endpoint reachable from the sandbox" >&2
  exit 2
fi
case "${CODE_REVIEW_HARNESS}" in
  openclaw)
    IMAGE_DOCKERFILE="${WORKLOAD_ROOT}/openclaw-runtime-image.Dockerfile"
    POLICY_FILE="${WORKLOAD_ROOT}/policy.yaml"
    ;;
  hermes)
    IMAGE_DOCKERFILE="${WORKLOAD_ROOT}/hermes-runtime-image.Dockerfile"
    POLICY_FILE="${WORKLOAD_ROOT}/hermes-policy.yaml"
    ;;
  *) echo "ERROR: CODE_REVIEW_HARNESS must be openclaw or hermes" >&2; exit 2 ;;
esac
if [[ "${CODE_REVIEW_HARNESS}" == "hermes" ]]; then
  SERVER_ENV_ARGS=(--env ADAPTER_PYTHON=/opt/hermes-venv/bin/python --env TMPDIR=/sandbox/tmp)
fi
if [[ "${CODE_REVIEW_RELAY}" != "0" && ( "${CODE_REVIEW_RELAY}" != "1" || "${CODE_REVIEW_HARNESS}" != "hermes" ) ]]; then
  echo "ERROR: CODE_REVIEW_RELAY=1 requires Hermes" >&2
  exit 2
fi
IMAGE_TAG="fabric-${CODE_REVIEW_HARNESS}-code-review:example"
RESULT_DIR="${FABRIC_ROOT}/.tmp/${CODE_REVIEW_HARNESS}-code-review"
MODEL_ENV_ARGS=()
if [[ "${CODE_REVIEW_API_KEY_ENV:-FABRIC_LOCAL_INFERENCE_KEY}" == "FABRIC_LOCAL_INFERENCE_KEY" ]]; then
  MODEL_ENV_ARGS=(--env FABRIC_LOCAL_INFERENCE_KEY=local-no-auth)
elif [[ -n "${CODE_REVIEW_API_KEY:-}" ]]; then
  MODEL_ENV_ARGS=(--env "${CODE_REVIEW_API_KEY_ENV}=${CODE_REVIEW_API_KEY}")
else
  echo "ERROR: set CODE_REVIEW_API_KEY for CODE_REVIEW_API_KEY_ENV=${CODE_REVIEW_API_KEY_ENV}" >&2
  exit 2
fi

stage_workload_image() {
  python3 "${WORKLOAD_ROOT}/model_access.py" \
    --harness "${CODE_REVIEW_HARNESS}" --output-dir "${RESULT_DIR}"
  POLICY_FILE="${RESULT_DIR}/policy.yaml"
}

smoke_workload_image() {
  docker run --rm "${RUNTIME_IMAGE}" /opt/fabric/bin/python -c \
    'import importlib,pathlib,subprocess,sys; importlib.import_module("nemo_fabric_adapters."+sys.argv[1]+".adapter"); assert pathlib.Path("/sandbox/code-review/repos/my-service/calculator.py").is_file(); assert pathlib.Path("/sandbox/code-review/skills/code-review/SKILL.md").is_file(); print(sys.version); print(subprocess.check_output(["openclaw", "--version"],text=True).strip() if sys.argv[1]=="openclaw" else pathlib.Path("/opt/hermes-agent/.fabric-source-revision").read_text().strip())' \
    "${CODE_REVIEW_HARNESS}"
}

run_workload_consumer() {
  mkdir -p "${RESULT_DIR}"
  local probe_command=(/usr/local/bin/node /sandbox/code-review/deployment/openshell/preflight.mjs)
  if [[ "${CODE_REVIEW_HARNESS}" == "hermes" ]]; then
    probe_command=(/opt/hermes-venv/bin/python /sandbox/code-review/deployment/openshell/preflight.py)
  fi
  "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
    sandbox exec --name "${SANDBOX_NAME}" --no-tty --timeout 40 \
    -- "${probe_command[@]}" "$(cat "${RESULT_DIR}/model-access.json")" \
    >"${RESULT_DIR}/preflight.json"
  local relay_args=()
  if [[ "${CODE_REVIEW_RELAY}" == "1" ]]; then relay_args=(--relay); fi
  "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
    sandbox exec --name "${SANDBOX_NAME}" --no-tty --timeout 660 -- \
    /opt/fabric/bin/python /sandbox/code-review/deployment/openshell/capture_evidence.py \
    --harness "${CODE_REVIEW_HARNESS}" >"${RESULT_DIR}/task-evidence.json" &
  EVIDENCE_PID=$!
  local label="review-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
  uv run --no-sync python -m examples.code_review_agent \
    --variant "${CODE_REVIEW_HARNESS}" --environment openshell \
    --gateway "http://127.0.0.1:${GATEWAY_PORT}" --image "${RUNTIME_IMAGE}" \
    --model-access-file "${RESULT_DIR}/model-access.json" "${relay_args[@]}" \
    --input "Read calculator.py and the code-review skill. Review correctness and missing tests without inventing a required return value. Remember this review label: ${label}. Do not write it to a file." \
    --follow-up-input "What was the review label from my previous message? Then give one focused test recommendation for calculator.py." \
    --results-file "${RESULT_DIR}/results.json" "$@"
  wait "${EVIDENCE_PID}"
  EVIDENCE_PID=""
  PYTHONPATH="${FABRIC_ROOT}" UV_PROJECT_ENVIRONMENT="${DEMO_PYTHON_ENV}" \
    uv run --no-sync python -m examples.code_review_agent.deployment.openshell.verify_run \
    --harness "${CODE_REVIEW_HARNESS}" --results-file "${RESULT_DIR}/results.json" \
    --label "${label}" "${relay_args[@]}"
}

collect_workload_diagnostics() {
  mkdir -p "${RESULT_DIR}"
  "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
    sandbox exec --name "${SANDBOX_NAME}" --no-tty --timeout 15 -- \
    /opt/fabric/bin/python -c 'import json,pathlib; root=pathlib.Path("/sandbox/artifacts"); print(json.dumps([{ "path":str(p.relative_to(root)), "bytes":p.stat().st_size} for p in root.rglob("*") if p.is_file()]))' \
    >"${RESULT_DIR}/task-artifacts.json" 2>&1 || true
  "${OPENSHELL_CLI}" --gateway-endpoint "http://127.0.0.1:${GATEWAY_PORT}" \
    sandbox exec --name "${SANDBOX_NAME}" --no-tty --timeout 15 -- \
    /opt/fabric/bin/python -c 'import pathlib; artifacts=pathlib.Path("/sandbox/artifacts"); paths=sorted(set(artifacts.glob(".fabric/runtime*/invocation*/stderr.txt")) | set(artifacts.glob(".fabric/hermes/runtimes/*/logs/errors.log"))); [print("---", p.relative_to(artifacts), "---\n", p.read_text(errors="replace")[-12000:]) for p in paths]; roots=[pathlib.Path("/sandbox/tmp/nemo-fabric/hosts"),pathlib.Path("/tmp/nemo-fabric/hosts")]; hosts=sorted({p for root in roots if root.exists() for p in root.glob("*/host.stderr.log")}); [print("---", p.name, "---\n", p.read_text(errors="replace")[-12000:]) for p in hosts]; server=pathlib.Path("/sandbox/fabric-server.log"); print("--- fabric-server.log ---\n", server.read_text(errors="replace")[-12000:]) if server.is_file() else None' \
    >"${RESULT_DIR}/task-host-errors.txt" 2>&1 || true
}
