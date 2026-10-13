<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Direct OpenShell Command Bridge

This is the lower-level OpenShell example: a consumer process calls
`fabric-agent-ctl` directly through OpenShell exec. The agent loop, adapter, and
harness run inside the sandbox. This exercises the command bridge contract; it
does not exercise the OpenShell environment provider or the ordinary SDK
lifecycle shown in the [provider-backed example](../deployment/openshell/README.md).

## Run

From the repository root, build the image and install the matching OpenShell
Python SDK in the NeMo Fabric environment:

```bash
docker build -f examples/code_review_agent/command_bridge/Dockerfile.openshell \
  -t fabric-code-review:bridge .
uv pip install --python .venv/bin/python openshell==0.1.2
```

Use a registered OpenShell gateway and workspace. Import the endpoint profile
and create a provider. `OPENAI_API_KEY` is read from the local shell running the
provider command; the real key is stored by OpenShell, and the sandbox receives
a placeholder. On gateways where provider policy composition is disabled, turn
it on before creating the sandbox:

```bash
openshell settings set --global --key providers_v2_enabled --value true
openshell provider profile import \
  --file examples/code_review_agent/command_bridge/openshell-provider.yaml
openshell provider create --name hosted --type nvidia-code-review \
  --credential OPENAI_API_KEY
openshell sandbox create --name code-review --from fabric-code-review:bridge \
  --provider hosted \
  --policy examples/code_review_agent/command_bridge/openshell-policy.yaml \
  --env ADAPTER_PYTHON=/opt/fabric/bin/python --env TMPDIR=/sandbox/tmp \
  --no-tty --detach -- sleep infinity
```

Run two review turns through the direct bridge consumer:

```bash
.venv/bin/python -m examples.code_review_agent.command_bridge.openshell \
  --workspace "$OPENSHELL_WORKSPACE" --sandbox code-review \
  --output-dir ".tmp/code-review-$(date -u +%Y%m%dT%H%M%SZ)" \
  --input "Review calculator.py and remember review tag cedar-4827." \
  --input "Recall the review tag and summarize your finding."
```

The consumer starts `fabric-agent-ctl serve`, waits for its Unix socket, then
uses separate `start`, `invoke`, and `stop` exec calls. The report records the
prompts, normalized results, command timings, and stop outcome. This path is
useful for inspecting the lower-level command contract; use the provider-backed
example when demonstrating the normal NeMo Fabric SDK experience.

After collecting the report, remove the sandbox and provider:

```bash
openshell sandbox delete code-review
openshell provider delete hosted
```
