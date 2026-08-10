# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Plan or run the email-phishing custom-agent example."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from nemo_fabric import Fabric

from examples.langgraph_custom_agent.consumer.config import frontier_config
from examples.langgraph_custom_agent.consumer.config import public_config


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("public", "frontier"), default="public")
    parser.add_argument("--model")
    parser.add_argument("--base-dir", type=Path, default=Path.cwd())
    parser.add_argument("--plan", action="store_true")
    parser.add_argument(
        "--input",
        default=(
            "Urgent: your account is locked. Verify your password immediately at "
            "https://example.invalid."
        ),
    )
    args = parser.parse_args()

    config_factory = frontier_config if args.variant == "frontier" else public_config
    config = config_factory(args.model) if args.model else config_factory()
    fabric = Fabric()
    output = (
        fabric.plan(config, base_dir=args.base_dir)
        if args.plan
        else await fabric.run(config, base_dir=args.base_dir, input=args.input)
    )
    print(json.dumps(output.to_mapping(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
