# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the minimum custom-agent lifecycle."""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from nemo_fabric_adapters.common import lifecycle
from nemo_fabric_adapter_contract.models import AgentConfig

from examples.langgraph_custom_agent.adapter.configuration import AgentDependencies
from examples.langgraph_custom_agent.adapter import runtime as runtime_module


def _context(runtime_id: str, invocation_id: str) -> dict[str, Any]:
    return {
        "runtime_id": runtime_id,
        "invocation_id": invocation_id,
        "request_id": f"request-{invocation_id}",
        "environment": {
            "environment_id": "environment-1",
            "provider": "local",
            "control_location": "in_env_control",
            "ownership": "caller_owned",
        },
        "artifacts": {},
    }


def _config() -> dict[str, Any]:
    return {
        "models": {
            "default": {
                "provider": "nvidia",
                "model": "nvidia/test-model",
                "api_key_env": "TEST_API_KEY",
                "base_url": "https://example.test/v1",
            }
        }
    }


def _request(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"operation": operation, "payload": payload}


def test_lifecycle_host_starts_once_invokes_repeatedly_and_stops(monkeypatch):
    model = FakeListChatModel(responses=["first explanation", "second explanation"])
    monkeypatch.setattr(
        runtime_module,
        "resolve_agent_dependencies",
        lambda _config: AgentDependencies(model, "Explain the assessment."),
    )
    runtime_id = "runtime-1"
    requests = [
        _request(
            "start",
            {
                "config": _config(),
                "runtime_context": _context(runtime_id, "runtime-start"),
            },
        ),
        _request(
            "invoke",
            {
                "runtime_context": _context(runtime_id, "invocation-1"),
                "request": {
                    "input": "Urgent: verify your password at https://one.invalid."
                },
            },
        ),
        _request(
            "invoke",
            {
                "runtime_context": _context(runtime_id, "invocation-2"),
                "request": {"input": "Team lunch is at noon."},
            },
        ),
        _request("stop", {"runtime_id": runtime_id}),
    ]
    input_stream = io.StringIO(
        "".join(f"{json.dumps(request)}\n" for request in requests)
    )
    output_stream = io.StringIO()

    lifecycle.serve(
        runtime_module.EmailPhishingRuntime,
        config_loader=AgentConfig.from_mapping,
        input_stream=input_stream,
        output_stream=output_stream,
    )

    responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
    assert [response["outcome"]["status"] for response in responses] == [
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert responses[1]["outcome"]["output"] == {
        "response": "first explanation",
        "classification": "phishing",
        "signals": ["urgency", "credential_request", "external_link"],
    }
    assert responses[2]["outcome"]["output"] == {
        "response": "second explanation",
        "classification": "benign",
        "signals": [],
    }


def test_runtime_rejects_invoke_before_start():
    runtime = runtime_module.EmailPhishingRuntime()

    with pytest.raises(lifecycle.LifecycleError) as error:
        asyncio.run(
            runtime.invoke(
                {
                    "runtime_context": _context("runtime-1", "invocation-1"),
                    "request": {"input": "hello"},
                }
            )
        )

    assert error.value.code == "email_phishing_runtime_not_started"


def test_runtime_rejects_runtime_mismatch(monkeypatch):
    monkeypatch.setattr(
        runtime_module,
        "resolve_agent_dependencies",
        lambda _config: AgentDependencies(
            FakeListChatModel(responses=["unused"]),
            "Explain the assessment.",
        ),
    )
    runtime = runtime_module.EmailPhishingRuntime()
    asyncio.run(
        runtime.start(
            {
                "config": AgentConfig.from_mapping(_config()),
                "runtime_context": _context("runtime-1", "runtime-start"),
            }
        )
    )

    with pytest.raises(lifecycle.LifecycleError) as error:
        asyncio.run(
            runtime.invoke(
                {
                    "runtime_context": _context("runtime-2", "invocation-1"),
                    "request": {"input": "hello"},
                }
            )
        )

    assert error.value.code == "email_phishing_runtime_mismatch"
    asyncio.run(runtime.stop())


def test_stop_is_safe_after_partial_start(monkeypatch):
    def fail_resolution(_config):
        raise lifecycle.LifecycleError("test_start_failure", "start failed")

    monkeypatch.setattr(
        runtime_module,
        "resolve_agent_dependencies",
        fail_resolution,
    )
    runtime = runtime_module.EmailPhishingRuntime()
    start_payload = {
        "config": AgentConfig.from_mapping(_config()),
        "runtime_context": _context("runtime-1", "runtime-start"),
    }

    with pytest.raises(lifecycle.LifecycleError, match="start failed"):
        asyncio.run(runtime.start(start_payload))

    asyncio.run(runtime.stop())
    assert runtime._graph is None
    assert runtime._runtime_id is None
