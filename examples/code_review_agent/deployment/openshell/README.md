<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Code Review in OpenShell

This deployment puts the code-review task, NeMo Fabric runtime, and agent
harness inside an OpenShell sandbox. The host-side example uses the ordinary
Fabric SDK to attach to a caller-owned sandbox, run two invocations in one
runtime, and stop it. The adapter and harness do not call OpenShell APIs.

The demo supports OpenClaw and Hermes. Hermes can additionally emit Relay
telemetry and trajectories. The image includes the selected adapter, harness,
code-review skill, and sample repository. A generated policy allows the chosen
harness to send chat-completion requests only to the configured model endpoint.

## Run

From the repository root, set a model endpoint reachable from the sandbox and
run the OpenClaw example:

```bash
CODE_REVIEW_BASE_URL=http://model-host.example:8000/v1 \
  bash examples/code_review_agent/deployment/openshell/run-demo.sh
```

The default model is `nvidia/nemotron-3-ultra-550b-a55b`. For Hermes, set
`CODE_REVIEW_HARNESS=hermes`. To include Relay, also set `CODE_REVIEW_RELAY=1`
with Hermes selected. For a secured endpoint, set `CODE_REVIEW_API_KEY` and,
if needed, `CODE_REVIEW_API_KEY_ENV` to the environment variable name expected
by the harness. The default local-inference key variable is populated with a
placeholder for endpoints that do not require authentication.

The demo builds a local Linux Docker image, launches a temporary OpenShell
gateway and caller-owned sandbox, checks model reachability, then runs the
review. It writes `results.json`, task-side evidence, verification output, and
logs under `.tmp/openclaw-code-review/` or `.tmp/hermes-code-review/`.
Set `UV_PROJECT_ENVIRONMENT` if the NeMo Fabric Python environment is not at
`.tmp/host-stack-tests`.

## Verify

The second prompt asks the agent to recall a random label supplied only in the
first prompt. The verifier checks both SDK results, process persistence,
evidence of reading `calculator.py`, and—when enabled—Relay request, trajectory,
and event records. The caller-owned sandbox is confirmed to remain available
after the Fabric runtime stops; the launcher then deletes the sandbox it
created.

For the shared workload contract and the LangGraph example, see
[Run a NeMo Fabric Agent in OpenShell](../../../openshell/README.md).
