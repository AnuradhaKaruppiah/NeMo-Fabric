# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Host ownership and routing using the real SDK and a controlled adapter."""

from unittest.mock import MagicMock

import json

import pytest
from nemo_fabric import _native
from nemo_fabric.host.state import RuntimeHost
from nemo_fabric.host.protocol import ProtocolError
from _utils.configs import hermes_shim_config
import asyncio
from contextlib import suppress


async def test_complete_request_reaches_real_sdk_and_preserves_correlation(
    hermes_shim_agent_dir, sdk_request, monkeypatch
):
    config = hermes_shim_config()
    host = RuntimeHost(directory=hermes_shim_agent_dir)
    mock_invoke = MagicMock(wraps=_native.invoke_runtime)
    monkeypatch.setattr(_native, "invoke_runtime", mock_invoke)
    try:
        started = await host.handle(
            {
                "interface_version": 3,
                "operation": "start",
                "agent": config.metadata.name,
                "config": config.to_mapping(),
            }
        )
        assert started["status"] == "succeeded", started
        runtime_id = started["result"]["runtime_id"]
        invoked = await host.handle(
            {
                "interface_version": 3,
                "operation": "invoke",
                "agent": config.metadata.name,
                "runtime_id": runtime_id,
                "request": sdk_request.to_mapping(),
            }
        )
        assert invoked["status"] == "succeeded", invoked
        native = invoked["result"]["fabric_result"]
        assert native["runtime_id"] == runtime_id
        assert native["request_id"] == sdk_request.request_id
        assert native["output"]["received"] == sdk_request.input
        mock_invoke.assert_called_once()
        assert json.loads(mock_invoke.call_args.args[2]) == sdk_request.to_mapping()
    finally:
        await host.shutdown()


async def test_real_sdk_busy_shutdown_requires_sandbox_cleanup(
    blocking_adapter_config, tmp_path
):
    host = RuntimeHost(directory=tmp_path)
    config = blocking_adapter_config.to_mapping()
    started = await host.handle(
        {
            "interface_version": 3,
            "operation": "start",
            "agent": "blocking",
            "config": config,
        }
    )
    assert started["status"] == "succeeded"
    runtime_id = started["result"]["runtime_id"]
    owned = host.runtimes[runtime_id].runtime
    invoked = asyncio.create_task(
        host.handle(
            {
                "interface_version": 3,
                "operation": "invoke",
                "agent": "blocking",
                "runtime_id": runtime_id,
                "input": {"message": "slow"},
            }
        )
    )
    try:
        async with asyncio.timeout(5):
            while not (tmp_path / "entered").exists():
                await asyncio.sleep(0.01)
        with pytest.raises(ProtocolError) as caught:
            await asyncio.wait_for(host.shutdown(), 3.5)
        assert caught.value.code == "cleanup_unconfirmed"
        assert host.lifecycle_used
        assert host.runtimes[runtime_id].runtime is owned
        assert not invoked.done()
    finally:
        (tmp_path / "release").write_text("release")
        with suppress(Exception):
            await invoked
        await owned.stop()


async def test_real_sdk_supports_multiple_lifecycles_per_sandbox(
    hermes_shim_agent_dir,
):
    config = hermes_shim_config()
    name = config.metadata.name
    host = RuntimeHost(directory=hermes_shim_agent_dir)

    def command(operation, **fields):
        request = {"interface_version": 3, "operation": operation, "agent": name}
        if operation == "start":
            request["config"] = config.to_mapping()
        return {**request, **fields}

    try:
        first = await host.handle(command("start"))
        assert first["status"] == "succeeded", first
        first_id = first["result"]["runtime_id"]
        second = await host.handle(command("start"))
        assert second["status"] == "succeeded", second
        second_id = second["result"]["runtime_id"]
        assert second_id != first_id
        assert len(host.runtimes) == 2
        for runtime_id, message in (
            (first_id, "first review"),
            (second_id, "isolated review"),
            (first_id, "follow-up review"),
        ):
            invoked = await host.handle(
                command("invoke", runtime_id=runtime_id, input={"message": message})
            )
            assert invoked["status"] == "succeeded", invoked
            assert invoked["result"]["fabric_result"]["runtime_id"] == runtime_id
            assert invoked["result"]["fabric_result"]["output"]["received"] == {
                "message": message
            }
        stopped = await host.handle(command("stop", runtime_id=first_id))
        assert stopped["status"] == "succeeded", stopped
        third = await host.handle(command("start"))
        assert third["status"] == "succeeded", third
        assert third["result"]["runtime_id"] not in {first_id, second_id}
        assert len(host.runtimes) == 3
        for runtime_id in (second_id, third["result"]["runtime_id"]):
            stopped = await host.handle(command("stop", runtime_id=runtime_id))
            assert stopped["status"] == "succeeded", stopped
    finally:
        await host.shutdown()


@pytest.mark.parametrize("invalid_config", ["missing_adapter", "unsupported_setting"])
async def test_real_sdk_rejects_start_before_native_runtime_creation(
    hermes_shim_agent_dir, monkeypatch, invalid_config
):
    config = hermes_shim_config().to_mapping()
    host = RuntimeHost(directory=hermes_shim_agent_dir)
    request = {
        "interface_version": 3,
        "operation": "start",
        "agent": config["metadata"]["name"],
        "config": config,
    }
    if invalid_config == "missing_adapter":
        config["harness"]["adapter_id"] = "test.missing"
    else:
        config["harness"]["settings"] = {"unsupported_setting": True}
    mock_start = MagicMock()
    monkeypatch.setattr(_native, "start_runtime", mock_start)
    try:
        response = await host.handle(request)
        assert response["status"] == "failed", response
        assert response["error"]["code"] == "validation_failed"
        assert response["error"]["effects"] == "none"
        assert response["changed"] is False
        assert host.runtimes == {}
        assert not host.lifecycle_used
        mock_start.assert_not_called()
    finally:
        await host.shutdown()
