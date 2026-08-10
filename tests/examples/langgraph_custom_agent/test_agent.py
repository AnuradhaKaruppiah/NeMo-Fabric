# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Fabric-independent email-phishing graph."""

from __future__ import annotations

import asyncio
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from examples.langgraph_custom_agent.agent.graph import build_email_phishing_graph

GRAPH_SOURCE = Path(__file__).parents[3] / "examples/langgraph_custom_agent/agent/graph.py"


def test_application_graph_does_not_depend_on_fabric_or_relay():
    source = GRAPH_SOURCE.read_text(encoding="utf-8")

    assert "nemo_fabric" not in source
    assert "nemo_relay" not in source


def test_graph_keeps_classification_deterministic_and_uses_model_for_explanation():
    graph = build_email_phishing_graph(
        FakeListChatModel(responses=["The email combines several phishing signals."]),
        "Explain the fixed assessment.",
    )

    result = asyncio.run(
        graph.ainvoke(
            {
                "email": (
                    "Urgent: your account is locked. Verify your password at "
                    "https://example.invalid."
                )
            }
        )
    )

    assert result["classification"] == "phishing"
    assert result["signals"] == [
        "urgency",
        "credential_request",
        "external_link",
    ]
    assert result["explanation"] == (
        "The email combines several phishing signals."
    )
