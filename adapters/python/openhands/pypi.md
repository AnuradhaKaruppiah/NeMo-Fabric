<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# OpenHands Adapter for NVIDIA NeMo Fabric

This package provides the OpenHands SDK adapter for NVIDIA NeMo Fabric. It maps
normalized NeMo Fabric configuration into one persistent OpenHands conversation
per runtime.

Install the tested OpenHands packages and the adapter:

```bash
pip install "openhands-sdk==1.49.4" "openhands-tools==1.49.4"
pip install nemo-fabric-adapters-openhands
```

The 1.49.4 package pair is validated with NVIDIA NIM. OpenHands has an
[upstream usage-telemetry issue](https://github.com/OpenHands/software-agent-sdk/issues/5268)
that can affect providers that explicitly report null `cache_creation_tokens`
details.

Refer to the [NVIDIA NeMo Fabric repository](https://github.com/NVIDIA/NeMo-Fabric/tree/main/adapters/python/openhands)
for configuration and usage instructions.
