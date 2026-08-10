# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Application-defined LangGraph for the email-phishing example."""

from __future__ import annotations

from typing import Literal
from typing import NotRequired
from typing import TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

RiskClassification = Literal["benign", "phishing"]


class EmailAnalysisState(TypedDict):
    """State accumulated while analyzing one email."""

    email: str
    signals: NotRequired[list[str]]
    classification: NotRequired[RiskClassification]
    explanation: NotRequired[str]


def extract_signals(state: EmailAnalysisState) -> dict[str, list[str]]:
    """Extract a small deterministic set of common phishing signals."""

    email = state["email"].casefold()
    signals: list[str] = []
    if any(term in email for term in ("urgent", "immediately", "act now")):
        signals.append("urgency")
    if any(
        term in email
        for term in ("password", "sign in", "login", "verify your account")
    ):
        signals.append("credential_request")
    if "http://" in email or "https://" in email:
        signals.append("external_link")
    if any(term in email for term in ("account locked", "account suspended")):
        signals.append("account_threat")
    return {"signals": signals}


def classify_risk(
    state: EmailAnalysisState,
) -> dict[str, RiskClassification]:
    """Apply the example's stable, intentionally simple risk policy."""

    classification: RiskClassification = (
        "phishing" if len(state["signals"]) >= 2 else "benign"
    )
    return {"classification": classification}


def build_email_phishing_graph(
    model: BaseChatModel,
    system_instruction: str,
) -> CompiledStateGraph:
    """Build the custom agent from native LangChain dependencies."""

    async def explain_assessment(
        state: EmailAnalysisState,
        config: RunnableConfig,
    ) -> dict[str, str]:
        signals = ", ".join(state["signals"]) or "none"
        response = await model.ainvoke(
            [
                ("system", system_instruction),
                (
                    "user",
                    "Explain this fixed email-risk assessment concisely.\n"
                    f"Classification: {state['classification']}\n"
                    f"Signals: {signals}\n"
                    f"Email:\n{state['email']}",
                ),
            ],
            config=config,
        )
        if not isinstance(response.content, str):
            raise TypeError("the explanation model must return text content")
        return {"explanation": response.content}

    builder = StateGraph(EmailAnalysisState)
    builder.add_node("extract_signals", extract_signals)
    builder.add_node("classify_risk", classify_risk)
    builder.add_node("explain_assessment", explain_assessment)
    builder.add_edge(START, "extract_signals")
    builder.add_edge("extract_signals", "classify_risk")
    builder.add_edge("classify_risk", "explain_assessment")
    builder.add_edge("explain_assessment", END)
    return builder.compile()
