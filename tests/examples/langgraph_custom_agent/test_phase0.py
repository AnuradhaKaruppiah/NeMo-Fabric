# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 0 contract for the dedicated LangGraph custom-agent example."""

from __future__ import annotations

import json
from pathlib import Path

from nemo_fabric import Fabric
from nemo_fabric import FabricConfig
from nemo_fabric import HarnessConfig
from nemo_fabric import InstructionConfig
from nemo_fabric import InstructionsConfig
from nemo_fabric import MetadataConfig
from nemo_fabric import ModelConfig

ROOT = Path(__file__).parents[3]
ADAPTER_ID = "nvidia.fabric.example.langgraph.email-phishing"
DESCRIPTOR = (
    ROOT
    / "examples"
    / "langgraph_custom_agent"
    / "adapter"
    / "fabric-adapter.json"
)


def test_descriptor_freezes_the_minimum_custom_agent_contract():
    descriptor = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))

    assert descriptor == {
        "contract_version": "fabric.adapter/v1alpha2",
        "adapter_id": ADAPTER_ID,
        "harness": "langgraph-email-phishing",
        "adapter_kind": "python",
        "runner": {"module": "examples.langgraph_custom_agent.adapter.runtime"},
        "requirements": {},
        "config": {
            "input": "agent_config",
            "accepts": [
                "models",
                "models.base_url",
                "models.temperature",
                "instructions.system",
            ],
        },
        "capabilities": {
            "cancellation": False,
            "service": False,
            "streaming": False,
            "updates": False,
        },
    }


def test_plan_projects_only_the_advertised_agent_config(tmp_path: Path):
    staged = tmp_path / "adapters" / "langgraph-email-phishing"
    staged.mkdir(parents=True)
    (staged / "fabric-adapter.json").write_text(
        DESCRIPTOR.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = FabricConfig(
        metadata=MetadataConfig(name="langgraph-email-phishing"),
        harness=HarnessConfig(
            adapter_id=ADAPTER_ID,
            resolution="preinstalled",
        ),
        models={
            "default": ModelConfig(
                provider="nvidia",
                model="nvidia/test-model",
                api_key_env="NVIDIA_API_KEY",
                base_url="https://integrate.api.nvidia.com/v1",
                temperature=0.2,
            )
        },
        instructions=InstructionsConfig(
            system=InstructionConfig(content="Explain the email risk assessment.")
        ),
    )

    plan = Fabric().plan(config, base_dir=tmp_path)

    assert plan.adapter.adapter_id == ADAPTER_ID
    assert plan.to_mapping()["agent_config"] == {
        "models": {
            "default": {
                "provider": "nvidia",
                "model": "nvidia/test-model",
                "api_key_env": "NVIDIA_API_KEY",
                "temperature": 0.2,
                "base_url": "https://integrate.api.nvidia.com/v1",
            }
        },
        "instructions": {
            "system": {
                "content": "Explain the email risk assessment.",
                "mode": "replace",
            }
        },
    }
