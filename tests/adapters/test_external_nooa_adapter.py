# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Focused tests for the source-only OO Agents reference adapter."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from nemo_fabric import DiscoveryConfig
from nemo_fabric import Fabric
from nemo_fabric import FabricConfig
from nemo_fabric import FabricConfigError
from nemo_fabric import MetadataConfig
from nemo_fabric import WorkflowConfig
from nemo_fabric_adapter_contract.models import AgentConfig
from nemo_fabric_adapter_contract.models import AgentRunRequest
from nemo_fabric_adapter_contract.models import AgentRunStatus
from nemo_fabric_adapter_contract.models import RuntimeContext

ROOT = Path(__file__).parents[2]
NOOA_ADAPTER_SOURCE = ROOT / "external" / "nooa" / "src"
sys.path.insert(0, str(NOOA_ADAPTER_SOURCE))

from nemo_fabric_adapters.nooa import adapter  # noqa: E402


def _workflow(**settings: Any) -> dict[str, Any]:
    return {
        "entrypoint": {
            "kind": "interactive_agent_factory",
            "ref": "fabric_nooa_test_target:create_agent",
        },
        "settings": settings,
    }


def _start_payload(
    tmp_path: Path,
    *,
    runtime_id: str = "runtime-1",
    **settings: Any,
) -> dict[str, Any]:
    return {
        "base_dir": str(tmp_path),
        "config": AgentConfig.from_mapping({"workflow": _workflow(**settings)}),
        "runtime_context": {
            "runtime_id": runtime_id,
            "environment": {
                "workspace": str(tmp_path / "workspace"),
                "artifacts": str(tmp_path / "artifacts"),
            },
        },
    }


def _invocation(
    value: Any = "hello",
    *,
    runtime_id: str = "runtime-1",
    request_id: str = "request-1",
) -> tuple[AgentRunRequest, RuntimeContext]:
    return (
        AgentRunRequest.from_mapping({"input": value}),
        RuntimeContext.from_mapping(
            {
                "runtime_id": runtime_id,
                "invocation_id": f"invocation-{request_id}",
                "request_id": request_id,
                "environment": {
                    "environment_id": "environment-1",
                    "provider": "test",
                    "control_location": "in_env_control",
                    "ownership": "caller_owned",
                },
                "artifacts": {},
            }
        ),
    )


def _agent_double(*, channel_names: tuple[str, ...] = ("user_messages",)):
    buffers = {name: [] for name in channel_names}
    channels: dict[str, MagicMock] = {}
    for name in channel_names:
        channel = MagicMock(name=f"{name}_channel")
        channel.put.side_effect = buffers[name].append

        def drain(channel_name: str = name) -> list[Any]:
            items = list(buffers[channel_name])
            buffers[channel_name].clear()
            return items

        channel.drain.side_effect = drain
        channels[name] = channel

    queue_manager = MagicMock(name="queue_manager")
    queue_manager.channels.return_value = channels
    queue_manager.get_channel.side_effect = channels.__getitem__
    queue_manager.shutdown = AsyncMock()

    async def race() -> list[tuple[str, Any]]:
        for name in channel_names:
            if buffers[name]:
                return [(name, buffers[name].pop(0))]
        raise AssertionError("test agent raced without a pending channel item")

    queue_manager.race = AsyncMock(side_effect=race)

    handlers: list[Any] = []
    event_manager = MagicMock(name="event_manager")

    def on(event_type: str, handler: Any):
        assert event_type == "AgentMessage"
        handlers.append(handler)

        def unsubscribe() -> None:
            handlers.remove(handler)

        return unsubscribe

    event_manager.on.side_effect = on

    agent = MagicMock(name="interactive_agent")
    agent.queue_manager = queue_manager
    agent.event_manager = event_manager
    agent.handle = AsyncMock()
    agent.close = AsyncMock()
    return agent, channels, handlers


@pytest.fixture(name="install_target")
def install_target_fixture(monkeypatch: pytest.MonkeyPatch):
    def install(factory: Any) -> None:
        module = types.ModuleType("fabric_nooa_test_target")
        module.create_agent = factory
        monkeypatch.setitem(sys.modules, module.__name__, module)

    return install


def test_descriptor_and_registered_target_declare_the_shared_boundary():
    descriptor = json.loads(
        (ROOT / "external" / "nooa" / "nooa.fabric-adapter.json").read_text(
            encoding="utf-8"
        )
    )
    target = json.loads(
        (ROOT / "tests" / "fixtures" / "nooa" / "echo.fabric-target.json").read_text(
            encoding="utf-8"
        )
    )

    assert descriptor["adapter_id"] == "nvidia.fabric.nooa"
    assert descriptor["adapter_kind"] == "python"
    assert descriptor["target_types"] == ["workflow"]
    assert descriptor["runner"] == {"module": "nemo_fabric_adapters.nooa.adapter"}
    assert descriptor["config"]["accepts"] == []
    assert descriptor["capabilities"] == {
        "cancellation": False,
        "service": False,
        "streaming": False,
        "updates": False,
    }
    assert target["adapter_id"] == descriptor["adapter_id"]
    assert target["spec"]["entrypoint"] == {
        "kind": "interactive_agent_factory",
        "ref": "fabric_nooa_test_target:create_agent",
    }


def test_registered_target_projects_the_factory_and_settings(tmp_path: Path):
    config = FabricConfig(
        metadata=MetadataConfig(name="nooa-test"),
        discovery=DiscoveryConfig(
            local_paths=[
                ROOT / "external" / "nooa",
                ROOT / "tests" / "fixtures" / "nooa",
            ]
        ),
        workflow=WorkflowConfig(
            target_id="nvidia.tests.nooa.echo",
            settings={"prefix": "test"},
        ),
    )

    plan = Fabric().plan(config, base_dir=tmp_path)

    assert (
        plan["adapter_descriptor"]["descriptor"]["adapter_id"] == "nvidia.fabric.nooa"
    )
    assert (
        plan["adapter_target_descriptor"]["descriptor"]["id"]
        == "nvidia.tests.nooa.echo"
    )
    assert plan.to_mapping()["agent_config"]["workflow"] == {
        "entrypoint": {
            "kind": "interactive_agent_factory",
            "ref": "fabric_nooa_test_target:create_agent",
        },
        "settings": {"prefix": "test"},
    }


def test_registered_target_rejects_unknown_settings(tmp_path: Path):
    config = FabricConfig(
        metadata=MetadataConfig(name="nooa-test"),
        discovery=DiscoveryConfig(
            local_paths=[
                ROOT / "external" / "nooa",
                ROOT / "tests" / "fixtures" / "nooa",
            ]
        ),
        workflow=WorkflowConfig(
            target_id="nvidia.tests.nooa.echo",
            settings={"unknown": True},
        ),
    )

    with pytest.raises(FabricConfigError, match="workflow.settings"):
        Fabric().plan(config, base_dir=tmp_path)


def test_main_opts_into_typed_agent_config(monkeypatch: pytest.MonkeyPatch):
    serve = MagicMock()
    monkeypatch.setattr(adapter.lifecycle, "serve", serve)

    adapter.main()

    serve.assert_called_once_with(
        adapter.NooaRuntime, config_loader=AgentConfig.from_mapping
    )


async def test_runtime_builds_once_and_preserves_agent_state(
    tmp_path: Path,
    install_target,
):
    agent, _channels, handlers = _agent_double()
    call_count = 0

    async def handle(notification: dict[str, list[Any]]):
        nonlocal call_count
        call_count += 1
        handlers[-1](SimpleNamespace(content=f"reply-{call_count}"))
        return SimpleNamespace(
            kind=SimpleNamespace(value="DONE"),
            explanation="request complete",
        )

    agent.handle.side_effect = handle
    factory = MagicMock(return_value=agent)
    install_target(factory)
    runtime = adapter.NooaRuntime()

    await runtime.start(_start_payload(tmp_path, prefix="test"))
    first = await runtime.invoke(*_invocation("one", request_id="one"))
    second = await runtime.invoke(*_invocation("two", request_id="two"))
    await runtime.stop()

    factory.assert_called_once()
    build_context = factory.call_args.args[0]
    assert build_context.runtime_id == "runtime-1"
    assert build_context.workspace == tmp_path / "workspace"
    assert build_context.artifact_root == tmp_path / "artifacts"
    assert build_context.config.workflow.settings == {"prefix": "test"}
    assert first.status is AgentRunStatus.SUCCEEDED
    assert first.output["response"] == "reply-1"
    assert first.output["messages"] == [{"content": "reply-1"}]
    assert first.output["completed"] is True
    assert second.output["response"] == "reply-2"
    assert call_count == 2
    assert agent.event_manager.on.call_count == 2
    agent.close.assert_awaited_once_with()


async def test_wait_resumes_on_a_background_channel(
    tmp_path: Path,
    install_target,
):
    agent, channels, handlers = _agent_double(channel_names=("user_messages", "jobs"))
    notifications: list[dict[str, list[Any]]] = []

    async def handle(notification: dict[str, list[Any]]):
        notifications.append(notification)
        if len(notifications) == 1:
            channels["jobs"].put("finished")
            return SimpleNamespace(
                kind=SimpleNamespace(value="WAIT"),
                explanation="waiting for the job",
            )
        handlers[-1](SimpleNamespace(content="job complete"))
        return SimpleNamespace(
            kind=SimpleNamespace(value="DONE"),
            explanation="job completed",
        )

    agent.handle.side_effect = handle
    install_target(MagicMock(return_value=agent))
    runtime = adapter.NooaRuntime()

    await runtime.start(_start_payload(tmp_path))
    try:
        result = await runtime.invoke(*_invocation())
    finally:
        await runtime.stop()

    assert notifications == [
        {"user_messages": ["hello"]},
        {"jobs": ["finished"]},
    ]
    assert result.output["response"] == "job complete"
    assert result.output["reason"] == "DONE"


@pytest.mark.parametrize("reason", ["NEED_INPUT", "GET_USER_INPUT"])
async def test_human_input_reasons_complete_without_marking_work_done(
    tmp_path: Path,
    install_target,
    reason: str,
):
    agent, _channels, handlers = _agent_double()

    async def handle(_notification: dict[str, list[Any]]):
        handlers[-1](SimpleNamespace(content="Which branch should I use?"))
        return SimpleNamespace(
            kind=SimpleNamespace(value=reason),
            explanation="a branch name is required",
        )

    agent.handle.side_effect = handle
    install_target(MagicMock(return_value=agent))
    runtime = adapter.NooaRuntime()

    await runtime.start(_start_payload(tmp_path))
    try:
        result = await runtime.invoke(*_invocation())
    finally:
        await runtime.stop()

    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.output["reason"] == reason
    assert result.output["completed"] is False


async def test_invalid_input_returns_a_safe_target_failure(
    tmp_path: Path,
    install_target,
):
    agent, _channels, _handlers = _agent_double()
    install_target(MagicMock(return_value=agent))
    runtime = adapter.NooaRuntime()

    await runtime.start(_start_payload(tmp_path))
    try:
        result = await runtime.invoke(*_invocation({"prompt": "hello"}))
    finally:
        await runtime.stop()

    assert result.status is AgentRunStatus.FAILED
    assert result.error.code == "nooa_invalid_request"
    agent.handle.assert_not_awaited()


async def test_invalid_respond_result_returns_a_safe_target_failure(
    tmp_path: Path,
    install_target,
):
    agent, _channels, _handlers = _agent_double()
    agent.handle.return_value = SimpleNamespace(kind="UNKNOWN", explanation="bad")
    install_target(MagicMock(return_value=agent))
    runtime = adapter.NooaRuntime()

    await runtime.start(_start_payload(tmp_path))
    try:
        result = await runtime.invoke(*_invocation())
    finally:
        await runtime.stop()

    assert result.status is AgentRunStatus.FAILED
    assert result.error.code == "nooa_invalid_respond_result"


async def test_custom_target_cleanup_takes_precedence(
    tmp_path: Path,
    install_target,
):
    agent, _channels, _handlers = _agent_double()
    cleanup = AsyncMock()
    install_target(
        MagicMock(
            return_value=adapter.InteractiveAgentTarget(agent=agent, close=cleanup)
        )
    )
    runtime = adapter.NooaRuntime()

    await runtime.start(_start_payload(tmp_path))
    await runtime.stop()

    cleanup.assert_awaited_once_with()
    agent.close.assert_not_awaited()


async def test_partial_start_failure_closes_the_factory_result(
    tmp_path: Path,
    install_target,
):
    invalid_agent, _channels, _handlers = _agent_double()
    invalid_agent.handle = None
    cleanup = AsyncMock()
    install_target(
        MagicMock(
            return_value=adapter.InteractiveAgentTarget(
                agent=invalid_agent,
                close=cleanup,
            )
        )
    )
    runtime = adapter.NooaRuntime()

    with pytest.raises(adapter.lifecycle.LifecycleError) as error:
        await runtime.start(_start_payload(tmp_path))

    assert error.value.code == "nooa_invalid_interactive_agent"
    cleanup.assert_awaited_once_with()
    await runtime.stop()
    cleanup.assert_awaited_once_with()


async def test_independent_runtimes_do_not_share_agents(
    tmp_path: Path,
    install_target,
):
    first_agent, _first_channels, first_handlers = _agent_double()
    second_agent, _second_channels, second_handlers = _agent_double()

    async def first_handle(_notification: dict[str, list[Any]]):
        first_handlers[-1](SimpleNamespace(content="first runtime"))
        return SimpleNamespace(kind="DONE", explanation="first complete")

    async def second_handle(_notification: dict[str, list[Any]]):
        second_handlers[-1](SimpleNamespace(content="second runtime"))
        return SimpleNamespace(kind="DONE", explanation="second complete")

    first_agent.handle.side_effect = first_handle
    second_agent.handle.side_effect = second_handle
    factory = MagicMock(side_effect=[first_agent, second_agent])
    install_target(factory)
    first_runtime = adapter.NooaRuntime()
    second_runtime = adapter.NooaRuntime()

    await first_runtime.start(_start_payload(tmp_path, runtime_id="runtime-1"))
    await second_runtime.start(_start_payload(tmp_path, runtime_id="runtime-2"))
    try:
        first_result = await first_runtime.invoke(
            *_invocation(runtime_id="runtime-1", request_id="first")
        )
        second_result = await second_runtime.invoke(
            *_invocation(runtime_id="runtime-2", request_id="second")
        )
    finally:
        await first_runtime.stop()
        await second_runtime.stop()

    assert factory.call_count == 2
    assert first_result.output["response"] == "first runtime"
    assert second_result.output["response"] == "second runtime"
    first_agent.close.assert_awaited_once_with()
    second_agent.close.assert_awaited_once_with()


async def test_runtime_mismatch_is_a_lifecycle_failure(
    tmp_path: Path,
    install_target,
):
    agent, _channels, _handlers = _agent_double()
    install_target(MagicMock(return_value=agent))
    runtime = adapter.NooaRuntime()

    await runtime.start(_start_payload(tmp_path))
    try:
        with pytest.raises(adapter.lifecycle.LifecycleError) as error:
            await runtime.invoke(*_invocation(runtime_id="runtime-2"))
    finally:
        await runtime.stop()

    assert error.value.code == "nooa_runtime_mismatch"
    agent.handle.assert_not_awaited()


async def test_factory_failure_is_redacted(
    tmp_path: Path,
    install_target,
):
    install_target(MagicMock(side_effect=RuntimeError("api-key=super-secret")))
    runtime = adapter.NooaRuntime()

    with pytest.raises(adapter.lifecycle.LifecycleError) as error:
        await runtime.start(_start_payload(tmp_path))

    assert error.value.code == "nooa_target_start_failed"
    assert "super-secret" not in str(error.value)
    await runtime.stop()
