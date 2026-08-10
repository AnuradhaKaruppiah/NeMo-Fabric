# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Minimum Fabric lifecycle for the email-phishing custom agent."""

from __future__ import annotations

from typing import Any

from langgraph.graph.state import CompiledStateGraph
from nemo_fabric_adapter_contract.models import AgentConfig
from nemo_fabric_adapter_contract.models import RuntimeContext
from nemo_fabric_adapters.common import lifecycle

from examples.langgraph_custom_agent.adapter.configuration import (
    resolve_agent_dependencies,
)
from examples.langgraph_custom_agent.agent.graph import build_email_phishing_graph


def main() -> None:
    """Serve one persistent custom-agent runtime."""

    lifecycle.serve(EmailPhishingRuntime, config_loader=AgentConfig.from_mapping)


def _runtime_context(payload: dict[str, Any]) -> RuntimeContext:
    try:
        return RuntimeContext.from_mapping(payload.get("runtime_context"))
    except Exception as error:
        raise lifecycle.LifecycleError(
            "email_phishing_invalid_runtime_context",
            "The email-phishing adapter requires a valid RuntimeContext",
        ) from error


class EmailPhishingRuntime:
    """One compiled email-phishing graph owned by one Fabric runtime."""

    def __init__(self) -> None:
        self._runtime_id: str | None = None
        self._graph: CompiledStateGraph | None = None

    async def start(self, payload: dict[str, Any]) -> None:
        if self._graph is not None:
            raise lifecycle.LifecycleError(
                "email_phishing_runtime_already_started",
                "The email-phishing runtime is already started",
            )
        agent_config = payload.get("config")
        if not isinstance(agent_config, AgentConfig):
            raise lifecycle.LifecycleError(
                "email_phishing_invalid_config",
                "The email-phishing adapter requires a validated AgentConfig",
            )

        context = _runtime_context(payload)
        dependencies = resolve_agent_dependencies(agent_config)
        graph = build_email_phishing_graph(
            dependencies.model,
            dependencies.system_instruction,
        )
        self._runtime_id = context.runtime_id
        self._graph = graph

    async def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._graph is None or self._runtime_id is None:
            raise lifecycle.LifecycleError(
                "email_phishing_runtime_not_started",
                "The email-phishing runtime is not started",
            )
        context = _runtime_context(payload)
        if context.runtime_id != self._runtime_id:
            raise lifecycle.LifecycleError(
                "email_phishing_runtime_mismatch",
                "The invocation does not match the active email-phishing runtime",
            )
        request = payload.get("request")
        email = request.get("input") if isinstance(request, dict) else None
        if not isinstance(email, str) or not email.strip():
            raise lifecycle.LifecycleError(
                "email_phishing_invalid_request",
                "The email-phishing adapter requires a non-empty text input",
            )

        result = await self._graph.ainvoke({"email": email})
        return {
            "response": result["explanation"],
            "classification": result["classification"],
            "signals": result["signals"],
        }

    async def stop(self) -> None:
        self._runtime_id = None
        self._graph = None


if __name__ == "__main__":
    main()
