# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Relay-backed streaming E2E for the Remote Agent adapter."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import requests

from nemo_fabric import EnvironmentConfig
from nemo_fabric import Fabric
from nemo_fabric import FabricConfig
from nemo_fabric import HarnessConfig
from nemo_fabric import MetadataConfig
from nemo_fabric import ModelConfig
from nemo_fabric import RunRequest
from nemo_fabric import RuntimeConfig


async def test_remote_agent_streams_correlated_atof(
    api_server: str,
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    response = await asyncio.to_thread(
        requests.post,
        f"{api_server}/_scenario",
        json={"emit_atof": True},
        timeout=5,
    )
    response.raise_for_status()
    monkeypatch.setenv("ADAPTER_PYTHON", sys.executable)

    config = FabricConfig(
        metadata=MetadataConfig(name="remote-agent-streaming-e2e"),
        harness=HarnessConfig(
            adapter_id="nvidia.fabric.remote-agent",
            resolution="preinstalled",
            settings={
                "base_url": f"{api_server}/v1",
                "api_type": "openai-completions",
            },
        ),
        models={"default": ModelConfig(provider="test", model="fabric-echo")},
        runtime=RuntimeConfig(
            input_schema="text",
            output_schema="message",
            artifacts=tmp_path / "artifacts",
        ),
        environment=EnvironmentConfig(
            provider="local",
            workspace=tmp_path,
            artifacts=tmp_path / "artifacts",
        ),
    ).enable_relay()

    request = RunRequest(input="stream this", request_id="remote-request")
    async with await Fabric().start_runtime(
        config,
        base_dir=repo_root,
        streaming=True,
    ) as runtime:
        stream = runtime.invoke_stream(request=request)
        records = [record async for record in stream]
        result = await stream.result()

    assert result.status == "succeeded", result.to_mapping()
    assert result.output == {"response": "echo user_count=1 latest=stream this"}
    assert [record["uuid"] for record in records] == [
        "remote-agent-root",
        "remote-agent-event",
    ]
    assert records[0]["metadata"]["nemo_fabric_request_id"] == request.request_id
