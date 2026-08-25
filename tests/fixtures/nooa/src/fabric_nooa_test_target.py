# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Finite InteractiveAgent-compatible target for subprocess E2E tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class _Channel:
    def __init__(self) -> None:
        self._items: list[Any] = []

    def put(self, item: Any) -> None:
        self._items.append(item)

    def drain(self) -> list[Any]:
        items = list(self._items)
        self._items.clear()
        return items


class _QueueManager:
    def __init__(self) -> None:
        self._channels = {"user_messages": _Channel()}

    def channels(self) -> dict[str, _Channel]:
        return self._channels

    def get_channel(self, name: str) -> _Channel:
        return self._channels[name]

    async def race(self) -> list[tuple[str, Any]]:
        for name, channel in self._channels.items():
            items = channel.drain()
            if items:
                for extra in items[1:]:
                    channel.put(extra)
                return [(name, items[0])]
        raise RuntimeError("test target has no pending queue item")

    async def shutdown(self) -> None:
        return None


class _EventManager:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event_type: str, handler: Any):
        handlers = self._handlers.setdefault(event_type, [])
        handlers.append(handler)

        def unsubscribe() -> None:
            handlers.remove(handler)

        return unsubscribe

    def intercept(self, _event_type: str, _handler: Any):
        return lambda: None

    def emit(self, event_type: str, event: Any) -> None:
        for handler in list(self._handlers.get(event_type, [])):
            handler(event)


class EchoInteractiveAgent:
    def __init__(self) -> None:
        self.queue_manager = _QueueManager()
        self.event_manager = _EventManager()
        self._invocations = 0

    async def handle(self, notification: dict[str, list[Any]]) -> Any:
        import nemo_relay

        self._invocations += 1
        message = notification["user_messages"][-1]

        async def complete_llm(_request: Any) -> dict[str, Any]:
            return {
                "id": f"response-{self._invocations}",
                "choices": [
                    {"message": {"role": "assistant", "content": str(message)}}
                ],
            }

        await nemo_relay.llm.execute(
            "fixture-model",
            nemo_relay.LLMRequest({}, {"messages": [{"content": str(message)}]}),
            complete_llm,
            model_name="fixture-model",
        )
        await nemo_relay.tools.execute(
            "execute_python",
            {"code": "result = 'fixture'"},
            lambda _args: {"result": "fixture"},
        )
        self.event_manager.emit(
            "AgentMessage",
            SimpleNamespace(content=f"reply-{self._invocations}: {message}"),
        )
        return SimpleNamespace(kind="DONE", explanation="echo complete")

    async def close(self) -> None:
        await self.queue_manager.shutdown()


def create_agent(_context: Any) -> EchoInteractiveAgent:
    return EchoInteractiveAgent()
