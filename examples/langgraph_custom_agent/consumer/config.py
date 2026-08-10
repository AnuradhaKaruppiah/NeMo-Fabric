# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Consumer-owned FabricConfig variants for the custom agent."""

from __future__ import annotations

import os

from nemo_fabric import FabricConfig
from nemo_fabric import HarnessConfig
from nemo_fabric import InstructionConfig
from nemo_fabric import InstructionsConfig
from nemo_fabric import MetadataConfig
from nemo_fabric import ModelConfig
from nemo_fabric import RuntimeConfig

ADAPTER_ID = "nvidia.fabric.example.langgraph.email-phishing"
PUBLIC_DEFAULT_MODEL = "nvidia/nemotron-3-nano-30b-a3b"
PUBLIC_BASE_URL = "https://integrate.api.nvidia.com/v1"


def _config(*, model: str, api_key_env: str, base_url: str) -> FabricConfig:
    return FabricConfig(
        metadata=MetadataConfig(
            name="langgraph-email-phishing",
            description="Classifies email risk with a dedicated LangGraph agent.",
        ),
        harness=HarnessConfig(
            adapter_id=ADAPTER_ID,
            resolution="preinstalled",
        ),
        models={
            "default": ModelConfig(
                provider="nvidia",
                model=model,
                api_key_env=api_key_env,
                base_url=base_url,
                temperature=0.0,
            )
        },
        instructions=InstructionsConfig(
            system=InstructionConfig(
                content=(
                    "Explain why the fixed classification follows from the detected "
                    "signals. Do not change the classification."
                )
            )
        ),
        runtime=RuntimeConfig(input_schema="text", output_schema="message"),
    )


def public_config(model: str = PUBLIC_DEFAULT_MODEL) -> FabricConfig:
    """Use the public NVIDIA API Catalog endpoint."""

    return _config(
        model=model,
        api_key_env="NVIDIA_API_KEY",
        base_url=PUBLIC_BASE_URL,
    )


def frontier_config(model: str) -> FabricConfig:
    """Use an internal NVIDIA Frontier OpenAI-compatible endpoint."""

    base_url = os.environ.get("NVIDIA_FRONTIER_BASE_URL")
    if not base_url:
        raise RuntimeError("NVIDIA_FRONTIER_BASE_URL is required for frontier testing")
    return _config(
        model=model,
        api_key_env="NVIDIA_FRONTIER_API_KEY",
        base_url=base_url,
    )
