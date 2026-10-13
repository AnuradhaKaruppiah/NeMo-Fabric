# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise a third workload through the normal Fabric consumer lifecycle."""

import argparse
import asyncio
import json
from pathlib import Path

from nemo_fabric import (
    DiscoveryConfig,
    EnvironmentConfig,
    EnvironmentReference,
    Fabric,
    FabricConfig,
    HarnessConfig,
    MetadataConfig,
    RuntimeConfig,
)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--sandbox-name", required=True)
    parser.add_argument("--sandbox-id", required=True)
    parser.add_argument("--input", default="remember this input")
    args = parser.parse_args()
    config = FabricConfig(
        metadata=MetadataConfig(name="echo"),
        discovery=DiscoveryConfig(local_paths=[Path(__file__).parent]),
        harness=HarnessConfig(
            adapter_id="nvidia.fabric.test.openshell.echo", resolution="image_provided"
        ),
        runtime=RuntimeConfig(
            input_schema="text", output_schema="object", artifacts="./artifacts"
        ),
        environment=EnvironmentConfig(
            provider="openshell",
            control_location="in_env_control",
            ownership="caller_owned",
            workspace="/sandbox",
            artifacts="/sandbox/artifacts",
            env={"PYTHONPATH": "/opt/nemo-fabric"},
            connection={"gateway": args.gateway},
            settings={
                "image": args.image,
                "command": ["fabric-runtime-server", "serve"],
            },
        ),
    )
    fabric = Fabric()
    environment = await fabric.attach_environment(
        config,
        EnvironmentReference.from_mapping(
            {
                "provider": "openshell",
                "resource": {
                    "sandbox_name": args.sandbox_name,
                    "sandbox_id": args.sandbox_id,
                },
            }
        ),
        base_dir=args.base_dir,
    )
    try:
        async with await fabric.start_runtime_in(
            config, environment, base_dir=args.base_dir
        ) as runtime:
            first = await runtime.invoke(input=args.input)
            second = await runtime.invoke(input="recall the first input")
    finally:
        await fabric.release_environment(environment)
    if (
        first.status != "succeeded"
        or second.status != "succeeded"
        or first.runtime_id != second.runtime_id
    ):
        raise RuntimeError("both turns must succeed in one runtime")
    if second.output["first_input"] != args.input or second.output["turn"] != 2:
        raise RuntimeError("the third workload did not retain conversation state")
    receipt = Path(second.artifacts.artifacts[0].path)
    if json.loads(receipt.read_text())["first_input"] != args.input:
        raise RuntimeError("the third workload receipt was not collected")
    args.base_dir.joinpath("results.json").write_text(
        json.dumps({"results": [first.to_mapping(), second.to_mapping()]}, indent=2)
        + "\n"
    )
    print(
        "Verified third workload: two turns, retained input, and a collected receipt."
    )


if __name__ == "__main__":
    asyncio.run(main())
