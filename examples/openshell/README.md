<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Run a Fabric-Ready Agent in OpenShell

NVIDIA NeMo Fabric runs an existing agent inside OpenShell through its adapter. The agent loop needs no OpenShell-specific code. Package the agent and its dependencies, describe its deployment requirements, and use the normal NeMo Fabric lifecycle.

The [code-review example](../code_review_agent/openshell/README.md) runs OpenClaw or Hermes. The [Courier example](../langgraph_openshell/README.md) runs a custom LangGraph agent. Both use this shared deployment launcher.

## Agent Readiness

Provide these pieces to deploy another agent:

| Piece | What Is Needed |
| --- | --- |
| Existing agent and adapter | A registered descriptor/target and runnable entry point implementing persistent `start`, ordered `invoke`, and `stop`. This POC supports Python and process adapters with buffered invocations. |
| Runtime image | The agent/harness, adapter, dependencies, a task-side interpreter/executable, and `fabric-runtime-server` / `fabric-runtime-ctl`. The image starts `fabric-runtime-server serve`. |
| Task files | Provision the workspace, skills, and any MCP executables/configuration at task-side paths. Provide a writable artifact root. Declare exported artifacts relative to that root. |
| Access configuration | Provision credentials inside the task and allow the model/tool endpoints for the binaries that actually call them. Configure task environment variables and paths explicitly. |
| Consumer | Select `image_provided` and the OpenShell environment in `FabricConfig`, then attach or prepare the environment and use `start_runtime_in`, `invoke`, `stop`, and `release_environment`. |

NeMo Fabric handles adapter lifecycle, correlation, normalized results, and declared artifact collection. OpenShell supplies isolation and policy enforcement. The deployment supplies the image, resources, policy, and credentials. Process observers and demo acceptance checks are optional instrumentation.

The current POC uses one sequential runtime per sandbox. Streaming, production gateway configuration, published harness images, and a standalone deployment package remain follow-up work.

## Shared Deployment Setup

Run from the repository root on a Linux host with Linux Docker containers of the same architecture. Install Docker, Rust/Cargo, `uv`, Python 3, `curl`, and `tar`. The launcher rejects unsupported host/container combinations before building or provisioning. It builds task binaries explicitly for the host's Linux target and builds the provider for the host.

The launcher stages runtime-control binaries and the common Python packages, downloads verified OpenShell releases, builds the workload image, starts a temporary gateway, and manages deployment cleanup. Caller-owned mode attaches to a provisioned sandbox; development mode lets the consumer prepare and release a Fabric-owned sandbox.

Set `OPENSHELL_EXAMPLE_PORT` if port `18080` is occupied. Set `OPENSHELL_EXAMPLE_RESULTS_DIR` to keep a run's results separate. Cached releases and build outputs remain under `.tmp/` and `target/`.

## Workload Interface

Keep deployment-specific packaging and invocation in a workload hook file. It declares `WORKLOAD_NAME`, `IMAGE_DOCKERFILE`, and `POLICY_FILE` (the last two default to files beside the hooks). Define `stage_workload_image` and `run_workload_consumer`; optional `smoke_workload_image` and `collect_workload_diagnostics` supply workload checks. The shared launcher contains no example-name dispatch.

For example, a caller-owned workload can provide:

```bash
WORKLOAD_NAME=my-agent
SUPPORTED_MODES=(deployment)

stage_workload_image() {
  cp -R "${WORKLOAD_ROOT}/agent" "${STAGE_DIR}/agent"
}

run_workload_consumer() {
  uv run --no-sync python "${WORKLOAD_ROOT}/consumer.py" \
    --gateway "http://127.0.0.1:${GATEWAY_PORT}" --image "${RUNTIME_IMAGE}" \
    --base-dir "${RESULT_DIR}" "$@"
}
```

Use the image recipe to install its dependencies and copy the staged runtime binaries and agent. The consumer accepts `--sandbox-name` / `--sandbox-id` for deployment mode, or `--fabric-sandbox-name` when supporting development mode. Consumer options pass through unchanged. Supported modes default to both; narrow `SUPPORTED_MODES` when needed. Hooks can set `IMAGE_TAG`, `RESULT_DIR`, and `MODEL_ENV_ARGS` for workload-specific image naming, output paths, and sandbox environment declarations. `NEMO_FABRIC_OPEN_SHELL_PROVIDER`, `PYTHONPATH`, and `UV_PROJECT_ENVIRONMENT` are supplied to the consumer hook.

Select a workload without editing the shared launcher:

```bash
bash examples/openshell/run-demo.sh path/to/workload/demo.sh [consumer-options]
```

The [minimal Echo fixture](../../tests/fixtures/openshell_echo/demo.sh) exercises a third Fabric-ready adapter, state retention, argument forwarding, and artifact collection without model access:

```bash
bash examples/openshell/run-demo.sh tests/fixtures/openshell_echo/demo.sh \
  --input 'Remember this input'
```

Refer to the [provider architecture](../../environment-providers/openshell/README.md) for transport details and the [Courier consumer](../langgraph_openshell/consumer/__main__.py) for the normal SDK calls.
