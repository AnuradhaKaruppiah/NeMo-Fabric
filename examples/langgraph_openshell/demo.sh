#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

WORKLOAD_NAME=courier
SUPPORTED_MODES=(caller_owned fabric_owned)
IMAGE_TAG=fabric-portable-courier:example
RESULT_DIR="${FABRIC_ROOT}/.tmp/portable-courier"

stage_workload_image() {
  test -f "${WORKLOAD_ROOT}/adapter/portable-courier.fabric-adapter.json"
}

run_workload_consumer() {
  uv run --no-sync python -m examples.langgraph_openshell.consumer \
    --gateway "http://127.0.0.1:${GATEWAY_PORT}" --image "${RUNTIME_IMAGE}" \
    --base-dir "${RESULT_DIR}" "$@"
}
