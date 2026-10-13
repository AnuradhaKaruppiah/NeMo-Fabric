#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

WORKLOAD_NAME=courier
IMAGE_TAG=fabric-portable-courier:example
RESULT_DIR="${FABRIC_ROOT}/.tmp/portable-courier"

stage_workload_image() {
  mkdir -p "${STAGE_DIR}/examples"
  cp "${FABRIC_ROOT}/examples/__init__.py" "${STAGE_DIR}/examples/"
  cp -R "${WORKLOAD_ROOT}" "${STAGE_DIR}/examples/langgraph_openshell"
}

run_workload_consumer() {
  uv run --no-sync python -m examples.langgraph_openshell.consumer \
    --gateway "http://127.0.0.1:${GATEWAY_PORT}" --image "${RUNTIME_IMAGE}" \
    --base-dir "${RESULT_DIR}" "$@"
}
