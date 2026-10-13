# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multiple runtime lifecycles per sandbox, using the existing public SDK."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from nemo_fabric import (
    Fabric,
    FabricConfig,
    FabricConfigError,
    RunRequest,
    Runtime,
    RuntimeStatus,
)
from nemo_fabric.host.protocol import (
    OPERATIONS,
    ProtocolError,
    envelope,
    rust_run_result_mapping,
    validate_request,
)


@dataclass
class OwnedRuntime:
    runtime: Runtime
    agent: str
    state: str = "running"


class RuntimeHost:
    """Serialize commands and route each command to its owned SDK runtime."""

    def __init__(
        self, *, directory: Path = Path("/sandbox"), fabric: Fabric | None = None
    ):
        self.directory = directory
        self.fabric = fabric if fabric is not None else Fabric()
        self.runtimes: dict[str, OwnedRuntime] = {}
        self.shutting_down = False
        # Sticky after any dispatched start so shutdown preserves the sandbox
        # ownership marker until its supervisor has cleaned up descendants.
        self.lifecycle_used = False
        # A start without a confirmed SDK result cannot be safely retried because
        # the SDK may have created a runtime whose ID was not returned.
        self.start_uncertain = False
        self._lock = asyncio.Lock()

    def _owned(self, request: dict[str, Any]) -> OwnedRuntime:
        owned = self.runtimes.get(request["runtime_id"])
        if owned is None:
            raise ProtocolError("runtime_unavailable")
        if owned.agent != request["agent"]:
            raise ProtocolError("wrong_agent")
        return owned

    async def _stop(self, owned: OwnedRuntime) -> None:
        owned.state = "unknown"
        await owned.runtime.stop()
        owned.state = "stopped"

    async def handle(self, request: Any) -> dict[str, Any]:
        operation = (
            request.get("operation", "unknown")
            if isinstance(request, dict)
            else "unknown"
        )
        if operation not in OPERATIONS:
            operation = "unknown"
        result = None
        changed, effects, stage = False, "none", "request"
        try:
            validate_request(request)
            if operation == "serve":
                raise ProtocolError()
            if self.shutting_down:
                raise ProtocolError("host_stopping")
            if operation == "start" and self.start_uncertain:
                raise ProtocolError("start_state_unknown", "start")
            async with self._lock:
                if self.shutting_down:
                    raise ProtocolError("host_stopping")
                if operation == "start":
                    if self.start_uncertain:
                        raise ProtocolError("start_state_unknown", "start")
                    stage = "validation"
                    config = FabricConfig.model_validate(request["config"])
                    changed, effects, stage = None, "unknown", "start"
                    previously_used = self.lifecycle_used
                    self.lifecycle_used = True
                    try:
                        runtime = await self.fabric.start_runtime(
                            config, base_dir=self.directory
                        )
                    except FabricConfigError:
                        self.lifecycle_used = previously_used
                        changed, effects = False, "none"
                        raise
                    except asyncio.CancelledError:
                        self.start_uncertain = True
                        raise
                    except Exception:
                        self.start_uncertain = True
                        raise
                    if runtime.runtime_id in self.runtimes:
                        self.start_uncertain = True
                        raise ProtocolError("start_failed", "start")
                    self.runtimes[runtime.runtime_id] = OwnedRuntime(
                        runtime, request["agent"], "unknown"
                    )
                    if runtime.status != RuntimeStatus.ACTIVE:
                        self.start_uncertain = True
                        raise ProtocolError("start_failed", "start")
                    self.runtimes[runtime.runtime_id].state = "running"
                    return envelope(
                        operation, runtime.handle.to_mapping(), changed=True
                    )
                owned = self._owned(request)
                result = {"runtime_id": request["runtime_id"]}
                if operation == "stop":
                    if owned.state == "stopped":
                        return envelope(operation, result)
                    changed, effects, stage = None, "unknown", "stop"
                    await self._stop(owned)
                    return envelope(operation, result, changed=True)
                stage = "invoke"
                if (
                    owned.state != "running"
                    or owned.runtime.status != RuntimeStatus.ACTIVE
                ):
                    raise ProtocolError("runtime_unavailable", stage)
                if "request" in request:
                    stage = "validation"
                    invocation = {
                        "request": RunRequest.model_validate(request["request"])
                    }
                else:
                    invocation = {"input": request["input"]}
                changed, effects, stage = None, "unknown", "invoke"
                task_environment = owned.runtime.handle.environment
                artifact_root = (
                    task_environment.get("artifacts")
                    if isinstance(task_environment, dict)
                    else getattr(task_environment, "artifacts", None)
                )
                native = rust_run_result_mapping(
                    (await owned.runtime.invoke(**invocation)).to_mapping(),
                    artifact_root=Path(artifact_root)
                    if isinstance(artifact_root, str)
                    else artifact_root,
                )
                result = {"runtime_id": request["runtime_id"], "fabric_result": native}
                return envelope(operation, result, changed=None)
        except Exception as error:
            if not isinstance(error, ProtocolError):
                if isinstance(error, (FabricConfigError, ValidationError)):
                    code = (
                        "validation_unavailable"
                        if getattr(error, "code", None)
                        == "adapter_capability_unverified"
                        else "validation_failed"
                    )
                    error = ProtocolError(code, "validation")
                else:
                    error = ProtocolError(f"{stage}_failed", stage)
            return envelope(
                operation, result, changed=changed, error=error, effects=effects
            )

    async def shutdown(self) -> None:
        """Stop an idle runtime. The enclosing sandbox owns descendant cleanup."""
        self.shutting_down = True
        if self._lock.locked():
            raise ProtocolError("cleanup_unconfirmed", "stop")
        async with self._lock:
            for owned in self.runtimes.values():
                if owned.state != "stopped":
                    await self._stop(owned)
