# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest
from nemo_fabric import (
    Fabric,
    RunResult,
    Runtime,
    RuntimeStatus,
    RuntimeHandle,
    RunRequest,
)


@pytest.fixture(name="process_is_running")
def process_is_running_fixture():
    def process_is_running(pid):
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode in (0, 1), result.stderr
        state = result.stdout.strip()
        # A terminated orphan may remain a zombie until its parent reaps it.
        return bool(state) and not state.startswith("Z")

    return process_is_running


@pytest.fixture(name="config")
def config_fixture():
    return {"metadata": {"name": "researcher"}, "harness": {"adapter_id": "test.host"}}


@pytest.fixture(name="mock_runtime")
def mock_runtime_fixture():
    runtime = MagicMock(spec=Runtime)
    runtime.runtime_id = "runtime-1"
    runtime.status = RuntimeStatus.ACTIVE
    runtime.handle = RuntimeHandle.from_mapping(
        {
            "runtime_id": "runtime-1",
            "runtime_binding": "binding-1",
            "agent_name": "researcher",
            "harness": "test.host",
            "adapter_kind": "python",
            "environment": {"provider": "local"},
        }
    )
    runtime.stop = AsyncMock()
    runtime.invoke = AsyncMock(return_value=MagicMock(spec=RunResult))
    runtime.invoke.return_value.to_mapping.return_value = RunResult.from_mapping(
        {
            "agent_name": "researcher",
            "harness": "test.host",
            "adapter_kind": "python",
            "invocation_id": "invocation-1",
            "request_id": "request-1",
            "status": "succeeded",
            "runtime_id": "runtime-1",
            "output": {"response": "FOUR"},
        }
    ).to_mapping()
    return runtime


@pytest.fixture(name="sdk_request")
def sdk_request_fixture():
    return RunRequest(
        input=["review", {"stream": True}],
        request_id="host-request-1",
        relay_session_root="3b3cab40-6aca-4a63-b5ef-0e5e7a82d14d",
        context={"task": "review", "turn": 2},
        overrides={"max_iterations": 3},
        max_iterations=3,
    )


@pytest.fixture(name="mock_fabric")
def mock_fabric_fixture(mock_runtime):
    fabric = MagicMock(spec=Fabric)
    fabric.start_runtime = AsyncMock(return_value=mock_runtime)
    return fabric


@pytest.fixture(name="host")
def host_fixture(tmp_path, mock_fabric):
    from nemo_fabric.host.state import RuntimeHost

    return RuntimeHost(directory=tmp_path, fabric=mock_fabric)


@pytest.fixture(name="host_request")
def request_fixture(host, config):
    def host_request(operation, **fields):
        value = {"interface_version": 3, "operation": operation}
        if operation in ("start", "invoke", "stop") or "runtime_id" in fields:
            value["agent"] = "researcher"
        if operation in ("invoke", "stop"):
            value["runtime_id"] = "runtime-1"
        if operation == "start":
            value["config"] = config
        return {**value, **fields}

    return host_request
