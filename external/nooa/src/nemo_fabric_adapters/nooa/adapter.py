#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run registered OO Agents ``InteractiveAgent`` targets through NeMo Fabric."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nemo_fabric_adapter_contract.models import AgentConfig
from nemo_fabric_adapter_contract.models import AgentRunError
from nemo_fabric_adapter_contract.models import AgentRunRequest
from nemo_fabric_adapter_contract.models import AgentRunResult
from nemo_fabric_adapter_contract.models import AgentRunStatus
from nemo_fabric_adapter_contract.models import RuntimeContext
from nemo_fabric_adapters.common import lifecycle
import nemo_fabric_adapters.common.utils as common_utils

ADAPTER = "python"
HARNESS = "nooa"
MODE = "interactive_agent"
WORKFLOW_FACTORY_KIND = "interactive_agent_factory"
_TERMINAL_REASONS = frozenset({"DONE", "NEED_INPUT", "GET_USER_INPUT"})

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InteractiveAgentBuildContext:
    """Fabric-owned values supplied to one registered target factory."""

    config: AgentConfig
    runtime_id: str
    base_dir: Path
    workspace: Path
    artifact_root: Path | None


@dataclass(slots=True)
class InteractiveAgentTarget:
    """An interactive agent with an optional target-owned cleanup callback."""

    agent: Any
    close: Callable[[], Awaitable[None] | None] | None = None


class _InvalidRespondResult(Exception):
    """An OO Agents turn returned a value outside the dispatcher contract."""


def main() -> None:
    """Serve the persistent local-host lifecycle protocol."""

    lifecycle.serve(NooaRuntime, config_loader=AgentConfig.from_mapping)


def _config_error(code: str, message: str, **metadata: Any) -> lifecycle.LifecycleError:
    return lifecycle.LifecycleError(code, message, metadata=metadata or None)


def _agent_config(payload: dict[str, Any]) -> AgentConfig:
    config = payload.get("config")
    if not isinstance(config, AgentConfig):
        raise _config_error(
            "nooa_invalid_agent_config",
            "OO Agents requires a validated AgentConfig start payload",
        )
    return config


def _factory_ref(config: AgentConfig) -> str:
    workflow = config.workflow
    if workflow is None:
        raise _config_error(
            "nooa_invalid_workflow",
            "AgentConfig.workflow is required",
            field="workflow",
        )
    if workflow.entrypoint.kind != WORKFLOW_FACTORY_KIND:
        raise _config_error(
            "nooa_invalid_workflow",
            f"workflow.entrypoint.kind must equal {WORKFLOW_FACTORY_KIND!r}",
            field="workflow.entrypoint.kind",
        )
    return workflow.entrypoint.ref


def _load_factory(ref: str) -> Callable[[InteractiveAgentBuildContext], Any]:
    module_name, separator, attribute = ref.partition(":")
    if (
        not separator
        or not module_name
        or not attribute.isidentifier()
        or any(not part.isidentifier() for part in module_name.split("."))
    ):
        raise _config_error(
            "nooa_invalid_factory_ref",
            "OO Agents factory refs must use the form 'package.module:factory'",
            field="workflow.entrypoint.ref",
        )
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute)
    except Exception as error:
        raise _config_error(
            "nooa_factory_not_found",
            "The registered OO Agents target factory could not be loaded",
            field="workflow.entrypoint.ref",
        ) from error
    if not callable(factory):
        raise _config_error(
            "nooa_factory_not_callable",
            "The registered OO Agents target factory is not callable",
            field="workflow.entrypoint.ref",
        )
    return factory


def _path(value: Any, *, default: Path | None = None) -> Path | None:
    if value is None:
        return default
    if not isinstance(value, (str, Path)):
        raise _config_error(
            "nooa_invalid_runtime_context",
            "OO Agents received an invalid runtime filesystem path",
        )
    return Path(value)


def _build_context(
    payload: dict[str, Any], config: AgentConfig
) -> InteractiveAgentBuildContext:
    try:
        runtime_id = common_utils.runtime_id(payload)
        base_dir = Path(common_utils.base_dir(payload))
    except ValueError as error:
        raise _config_error(
            "nooa_invalid_runtime_context",
            "OO Agents lifecycle payload is missing required runtime context",
        ) from error

    environment = common_utils.environment_payload(payload)
    workspace = _path(environment.get("workspace"), default=base_dir)
    artifact_root = _path(environment.get("artifacts"))
    assert workspace is not None
    return InteractiveAgentBuildContext(
        config=config,
        runtime_id=runtime_id,
        base_dir=base_dir,
        workspace=workspace,
        artifact_root=artifact_root,
    )


async def _await_if_needed(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _unwrap_target(value: Any) -> InteractiveAgentTarget:
    if isinstance(value, InteractiveAgentTarget):
        return value
    return InteractiveAgentTarget(agent=value)


def _validate_agent(agent: Any) -> None:
    queue_manager = getattr(agent, "queue_manager", None)
    event_manager = getattr(agent, "event_manager", None)
    required = {
        "agent.handle": getattr(agent, "handle", None),
        "agent.event_manager.on": getattr(event_manager, "on", None),
        "agent.queue_manager.channels": getattr(queue_manager, "channels", None),
        "agent.queue_manager.get_channel": getattr(queue_manager, "get_channel", None),
        "agent.queue_manager.race": getattr(queue_manager, "race", None),
        "agent.queue_manager.shutdown": getattr(queue_manager, "shutdown", None),
    }
    missing = [name for name, value in required.items() if not callable(value)]
    if missing:
        raise _config_error(
            "nooa_invalid_interactive_agent",
            "The registered target factory did not return a compatible InteractiveAgent",
            missing=missing,
        )


async def _close_target(target: InteractiveAgentTarget) -> None:
    if target.close is not None:
        await _await_if_needed(target.close())
        return

    close = getattr(target.agent, "close", None)
    if callable(close):
        await _await_if_needed(close())
        return

    queue_manager = getattr(target.agent, "queue_manager", None)
    shutdown = getattr(queue_manager, "shutdown", None)
    if callable(shutdown):
        await _await_if_needed(shutdown())


async def _close_after_failed_start(target: InteractiveAgentTarget) -> None:
    try:
        await _close_target(target)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        LOGGER.error(
            "OO Agents cleanup failed after start error (error_type=%s)",
            type(error).__name__,
        )


def _reason(result: Any) -> tuple[str, str]:
    kind = getattr(result, "kind", None)
    value = getattr(kind, "value", kind)
    explanation = getattr(result, "explanation", None)
    if value not in _TERMINAL_REASONS | {"WAIT"}:
        raise _InvalidRespondResult("unsupported response reason")
    if not isinstance(explanation, str) or not explanation.strip():
        raise _InvalidRespondResult("missing response explanation")
    return value, explanation


async def dispatch(agent: Any, text: str) -> tuple[str, str]:
    """Submit one user message and run the standard InteractiveAgent wake loop."""

    queue_manager = agent.queue_manager
    queue_manager.get_channel("user_messages").put(text)
    while True:
        wins = await queue_manager.race()
        notification: dict[str, list[Any]] = {}
        for name, item in wins:
            notification.setdefault(name, []).append(item)
        for name, channel in queue_manager.channels().items():
            drained = channel.drain()
            if drained:
                notification.setdefault(name, []).extend(drained)

        result = await agent.handle(notification)
        reason, explanation = _reason(result)
        if reason != "WAIT":
            return reason, explanation


def _failure_output(code: str, message: str) -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.FAILED,
        output={
            "harness": HARNESS,
            "adapter": ADAPTER,
            "mode": MODE,
            "response": None,
            "messages": [],
            "completed": False,
        },
        error=AgentRunError(code=code, message=message),
    )


def _success_output(
    messages: list[dict[str, str]],
    reason: str,
    explanation: str,
) -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.SUCCEEDED,
        output={
            "harness": HARNESS,
            "adapter": ADAPTER,
            "mode": MODE,
            "response": messages[-1]["content"] if messages else None,
            "messages": messages,
            "reason": reason,
            "explanation": explanation,
            "completed": reason == "DONE",
        },
    )


class NooaRuntime:
    """One registered OO Agents target owned by one Fabric runtime."""

    def __init__(self) -> None:
        self._runtime_id: str | None = None
        self._target: InteractiveAgentTarget | None = None

    async def start(self, payload: dict[str, Any]) -> None:
        if self._target is not None:
            raise lifecycle.LifecycleError(
                "nooa_runtime_already_started",
                "OO Agents runtime is already started",
            )

        config = _agent_config(payload)
        context = _build_context(payload, config)
        factory = _load_factory(_factory_ref(config))
        target: InteractiveAgentTarget | None = None
        try:
            target = _unwrap_target(await _await_if_needed(factory(context)))
            _validate_agent(target.agent)
        except asyncio.CancelledError:
            if target is not None:
                await _close_after_failed_start(target)
            raise
        except lifecycle.LifecycleError:
            if target is not None:
                await _close_after_failed_start(target)
            raise
        except Exception as error:
            if target is not None:
                await _close_after_failed_start(target)
            raise lifecycle.LifecycleError(
                "nooa_target_start_failed",
                "The registered OO Agents target failed to start",
            ) from error

        self._runtime_id = context.runtime_id
        self._target = target

    async def invoke(
        self,
        request: AgentRunRequest,
        runtime_context: RuntimeContext,
    ) -> AgentRunResult:
        if self._target is None or self._runtime_id is None:
            raise lifecycle.LifecycleError(
                "nooa_runtime_not_started",
                "OO Agents runtime is not started",
            )
        if runtime_context.runtime_id != self._runtime_id:
            raise lifecycle.LifecycleError(
                "nooa_runtime_mismatch",
                "OO Agents invocation does not match the active runtime",
            )
        if not isinstance(request.input, str):
            return _failure_output(
                "nooa_invalid_request",
                "OO Agents InteractiveAgent input must be a string",
            )

        messages: list[dict[str, str]] = []

        def capture_message(event: Any) -> None:
            content = getattr(event, "content", None)
            if isinstance(content, str):
                messages.append({"content": content})

        agent = self._target.agent
        unsubscribe: Callable[[], None] | None = None
        try:
            unsubscribe = agent.event_manager.on("AgentMessage", capture_message)
            reason, explanation = await dispatch(agent, request.input)
        except asyncio.CancelledError:
            raise
        except _InvalidRespondResult:
            return _failure_output(
                "nooa_invalid_respond_result",
                "OO Agents InteractiveAgent returned an invalid RespondResult",
            )
        except Exception as error:
            LOGGER.error(
                "OO Agents invocation failed (error_type=%s)",
                type(error).__name__,
            )
            return _failure_output(
                "nooa_target_invoke_failed",
                "OO Agents target invocation failed; inspect adapter stderr for details",
            )
        finally:
            if unsubscribe is not None:
                try:
                    unsubscribe()
                except Exception as error:
                    LOGGER.error(
                        "OO Agents event unsubscribe failed (error_type=%s)",
                        type(error).__name__,
                    )

        return _success_output(messages, reason, explanation)

    async def stop(self) -> None:
        target = self._target
        self._runtime_id = None
        self._target = None
        if target is None:
            return
        try:
            await _close_target(target)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise lifecycle.LifecycleError(
                "nooa_runtime_stop_failed",
                "OO Agents runtime failed to stop cleanly",
            ) from error


if __name__ == "__main__":
    main()
