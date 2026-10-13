#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

WORKLOAD_NAME=echo
SUPPORTED_MODES=(deployment)

stage_workload_image() {
  cp "${WORKLOAD_ROOT}/agent.py" "${STAGE_DIR}/echo_adapter.py"
}

run_workload_consumer() {
  uv run --no-sync python "${WORKLOAD_ROOT}/consumer.py" \
    --gateway "http://127.0.0.1:${GATEWAY_PORT}" --image "${RUNTIME_IMAGE}" \
    --base-dir "${RESULT_DIR}" "$@"
}
