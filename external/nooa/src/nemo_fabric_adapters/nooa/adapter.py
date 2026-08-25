#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run registered OO Agents ``InteractiveAgent`` targets through NeMo Fabric."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from nemo_fabric_adapter_contract.models import AgentConfig
from nemo_fabric_adapter_contract.models import AgentRunError
from nemo_fabric_adapter_contract.models import AgentRunRequest
from nemo_fabric_adapter_contract.models import AgentRunResult
from nemo_fabric_adapter_contract.models import AgentRunStatus
from nemo_fabric_adapter_contract.models import RuntimeContext
from nemo_fabric_adapters.common import lifecycle
import nemo_fabric_adapters.common.utils as common_utils
from nemo_fabric_adapters.nooa.telemetry import RelayReport
from nemo_fabric_adapters.nooa.telemetry import RelayTelemetry

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
    models: Mapping[str, Any]
    system_instruction: str | None
    settings: Mapping[str, Any]
    skill_paths: tuple[Path, ...]
    runtime_id: str
    base_dir: Path
    workspace: Path
    artifact_root: Path | None


@dataclass(slots=True)
class InteractiveAgentTarget:
    """An interactive agent with optional target-owned lifecycle policy."""

    agent: Any
    close: Callable[[], Awaitable[None] | None] | None = None
    continue_after: Callable[[Any, str, str], Awaitable[bool] | bool] | None = None


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


def _credential_env(provider: str, configured: str | None) -> str:
    if configured is not None:
        return configured
    defaults = {
        "anthropic": "ANTHROPIC_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    try:
        return defaults[provider]
    except KeyError as error:
        raise _config_error(
            "nooa_model_credential_required",
            f"OO Agents model provider {provider!r} requires api_key_env",
            field="models",
        ) from error


def _native_model_name(provider: str, model: str) -> str:
    if provider == "nvidia":
        if model.startswith("nvidia_nim/"):
            return model
        return f"nvidia_nim/{model}"
    if model.startswith(f"{provider}/"):
        return model
    return f"{provider}/{model}"


async def _close_models(models: Mapping[str, Any]) -> None:
    closed: set[int] = set()
    for model in models.values():
        if id(model) in closed:
            continue
        closed.add(id(model))
        close = getattr(model, "aclose", None)
        if callable(close):
            await _await_if_needed(close())
            continue
        close = getattr(model, "close", None)
        if callable(close):
            await _await_if_needed(close())


async def _build_models(config: AgentConfig) -> dict[str, Any]:
    if not config.models:
        return {}
    try:
        from nooa.unifiedllm import get_llm_client
    except Exception as error:
        raise _config_error(
            "nooa_dependency_missing",
            "OO Agents model support is not available in the adapter environment",
        ) from error

    result: dict[str, Any] = {}
    try:
        for role, model in config.models.items():
            credential_env = _credential_env(model.provider, model.api_key_env)
            api_key = os.environ.get(credential_env)
            if not api_key:
                raise _config_error(
                    "nooa_model_credential_missing",
                    f"OO Agents model credential environment variable {credential_env!r} is not set",
                    field=f"models.{role}.api_key_env",
                )
            settings = dict(model.settings)
            client_type = settings.pop("client_type", None)
            overrides: dict[str, Any] = {"api_key": api_key, **settings}
            if model.base_url is not None:
                overrides["api_base"] = model.base_url
            if model.temperature is not None:
                overrides["temperature"] = model.temperature
            result[role] = get_llm_client(
                _native_model_name(model.provider, model.model),
                client_type=client_type,
                **overrides,
            )
    except BaseException:
        await _close_models(result)
        raise
    return result


def _resolved_skill_paths(config: AgentConfig, base_dir: Path) -> tuple[Path, ...]:
    values = config.skills.paths if config.skills is not None else []
    paths = []
    for value in values:
        path = Path(value)
        paths.append(path if path.is_absolute() else base_dir / path)
    return tuple(paths)


def _build_context(
    payload: dict[str, Any],
    config: AgentConfig,
    models: Mapping[str, Any],
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
    workflow = config.workflow
    assert workflow is not None
    system_instruction = (
        config.instructions.system.content
        if config.instructions is not None and config.instructions.system is not None
        else None
    )
    return InteractiveAgentBuildContext(
        config=config,
        models=MappingProxyType(dict(models)),
        system_instruction=system_instruction,
        settings=MappingProxyType(dict(workflow.settings)),
        skill_paths=_resolved_skill_paths(config, base_dir),
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


def _validate_target(target: InteractiveAgentTarget) -> None:
    _validate_agent(target.agent)
    if target.close is not None and not callable(target.close):
        raise _config_error(
            "nooa_invalid_interactive_agent",
            "The registered target cleanup callback is not callable",
        )
    if target.continue_after is not None and not callable(target.continue_after):
        raise _config_error(
            "nooa_invalid_interactive_agent",
            "The registered target continuation predicate is not callable",
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


async def _close_resources(
    target: InteractiveAgentTarget | None,
    models: Mapping[str, Any],
) -> None:
    primary: BaseException | None = None
    if target is not None:
        try:
            await _close_target(target)
        except BaseException as error:
            primary = error
    try:
        await _close_models(models)
    except BaseException as error:
        if primary is None:
            primary = error
        else:
            LOGGER.error(
                "OO Agents model cleanup failed while preserving target cleanup failure "
                "(error_type=%s)",
                type(error).__name__,
            )
    if primary is not None:
        raise primary


def _reason(result: Any) -> tuple[str, str]:
    kind = getattr(result, "kind", None)
    value = getattr(kind, "value", kind)
    explanation = getattr(result, "explanation", None)
    if value not in _TERMINAL_REASONS | {"WAIT"}:
        raise _InvalidRespondResult("unsupported response reason")
    if not isinstance(explanation, str) or not explanation.strip():
        raise _InvalidRespondResult("missing response explanation")
    return value, explanation


async def dispatch(target: InteractiveAgentTarget, text: str) -> tuple[str, str]:
    """Submit one user message and run the standard InteractiveAgent wake loop."""

    agent = target.agent
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
        continue_after = False
        if reason != "WAIT" and target.continue_after is not None:
            continue_after = bool(
                await _await_if_needed(
                    target.continue_after(agent, reason, explanation)
                )
            )
        if reason != "WAIT" and not continue_after:
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
    response = messages[-1]["content"] if messages else explanation
    return AgentRunResult(
        status=AgentRunStatus.SUCCEEDED,
        output={
            "harness": HARNESS,
            "adapter": ADAPTER,
            "mode": MODE,
            "response": response,
            "messages": messages,
            "reason": reason,
            "explanation": explanation,
            "completed": reason == "DONE",
        },
    )


def _with_telemetry(
    result: AgentRunResult,
    report: RelayReport | None,
) -> AgentRunResult:
    if report is None:
        return result
    output = dict(result.output)
    telemetry: dict[str, Any] = {
        "enabled": report.enabled,
        "provider": "relay",
        "emitter": "nooa.nemo_relay_middleware",
    }
    if report.error is not None:
        telemetry["degraded"] = True
        telemetry["error"] = report.error
    if report.quarantine_cause is not None:
        telemetry["quarantine_cause"] = report.quarantine_cause
    output["telemetry"] = telemetry
    output["relay_artifacts"] = list(report.artifacts)
    result.output = output
    return result


class NooaRuntime:
    """One registered OO Agents target owned by one Fabric runtime."""

    def __init__(self) -> None:
        self._runtime_id: str | None = None
        self._target: InteractiveAgentTarget | None = None
        self._models: dict[str, Any] = {}
        self._telemetry: RelayTelemetry | None = None

    async def start(self, payload: dict[str, Any]) -> None:
        if self._target is not None:
            raise lifecycle.LifecycleError(
                "nooa_runtime_already_started",
                "OO Agents runtime is already started",
            )

        config = _agent_config(payload)
        factory = _load_factory(_factory_ref(config))
        models: dict[str, Any] = {}
        target: InteractiveAgentTarget | None = None
        telemetry: RelayTelemetry | None = None
        try:
            models = await _build_models(config)
            context = _build_context(payload, config, models)
            telemetry = RelayTelemetry(
                agent_name=common_utils.agent_name(payload),
                base_dir=context.base_dir,
                config=config,
            )
            target = _unwrap_target(await _await_if_needed(factory(context)))
            _validate_target(target)
        except asyncio.CancelledError:
            await _close_resources(target, models)
            if telemetry is not None:
                await telemetry.close()
            raise
        except lifecycle.LifecycleError:
            try:
                await _close_resources(target, models)
            except Exception as error:
                LOGGER.error(
                    "OO Agents cleanup failed after start error (error_type=%s)",
                    type(error).__name__,
                )
            if telemetry is not None:
                await telemetry.close()
            raise
        except Exception as error:
            try:
                await _close_resources(target, models)
            except Exception as cleanup_error:
                LOGGER.error(
                    "OO Agents cleanup failed after start error (error_type=%s)",
                    type(cleanup_error).__name__,
                )
            if telemetry is not None:
                await telemetry.close()
            raise lifecycle.LifecycleError(
                "nooa_target_start_failed",
                "The registered OO Agents target failed to start",
            ) from error

        self._runtime_id = context.runtime_id
        self._target = target
        self._models = models
        self._telemetry = telemetry

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

        agent = self._target.agent
        telemetry = self._telemetry
        assert telemetry is not None

        async def invoke_target() -> AgentRunResult:
            messages: list[dict[str, str]] = []

            def capture_message(event: Any) -> None:
                content = getattr(event, "content", None)
                if isinstance(content, str):
                    messages.append({"content": content})

            unsubscribe: Callable[[], None] | None = None
            try:
                unsubscribe = agent.event_manager.on("AgentMessage", capture_message)
                reason, explanation = await dispatch(self._target, request.input)
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

        relay_invocation = await telemetry.invoke(
            agent=agent,
            runtime_context=runtime_context,
            call=invoke_target,
        )
        if relay_invocation.result is None:
            result = _failure_output(
                "nooa_telemetry_setup_failed",
                "OO Agents Relay telemetry setup failed before target execution",
            )
        else:
            result = relay_invocation.result
        return _with_telemetry(result, relay_invocation.report)

    async def stop(self) -> None:
        target = self._target
        models = self._models
        telemetry = self._telemetry
        self._runtime_id = None
        self._target = None
        self._models = {}
        self._telemetry = None
        if target is None and not models and telemetry is None:
            return
        telemetry_error: Exception | None = None
        if telemetry is not None:
            try:
                await telemetry.close()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                telemetry_error = error
        try:
            await _close_resources(target, models)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise lifecycle.LifecycleError(
                "nooa_runtime_stop_failed",
                "OO Agents runtime failed to stop cleanly",
            ) from error
        if telemetry_error is not None:
            raise lifecycle.LifecycleError(
                "nooa_runtime_stop_failed",
                "OO Agents runtime failed to stop cleanly",
            ) from telemetry_error


if __name__ == "__main__":
    main()
