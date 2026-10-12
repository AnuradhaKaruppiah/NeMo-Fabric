<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Run a code-review agent inside OpenShell

This NVIDIA NeMo Fabric example runs the existing OpenClaw agent inside an OpenShell sandbox (Model A). Its agent loop, tools, and conversation state stay in the sandbox. The host uses Fabric to start the agent, send a review and follow-up, and stop it. Inference runs on your external model server.

## Run it

From the repository root, with Docker running and Rust/Cargo, `uv`, Python, `curl`, and `tar` on `PATH`:

```bash
bash examples/code_review_agent/openshell/run-demo.sh
```

The default model is `nvidia/nemotron-3-ultra-550b-a55b` at `http://10.86.19.10:8000/v1`; that endpoint must be reachable. The first run builds and downloads dependencies. Later runs reuse cached files and Docker layers.

## How it works

1. **Prepare:** [The wrapper](run-demo.sh) selects code review and calls the [shared launcher](../../langgraph_openshell/run-demo.sh). It builds the task image, starts OpenShell, and creates a sandbox with the [policy](policy.yaml). The sandbox starts `fabric-runtime-server`; the agent starts in the next step.
2. **Run:** `run_consumer()` launches the [existing Python consumer](../__main__.py). Fabric attaches to the sandbox, starts the OpenClaw adapter, and invokes the same runtime twice. OpenClaw reads `calculator.py` and the review skill. The follow-up asks for a label from the first turn, demonstrating conversation continuity without host replay. A separate observer captures task-side process and read-call evidence.
3. **Clean up:** The Python consumer stops the agent and releases its attachment in `finally`. The launcher checks the saved results/evidence, verifies that the caller-owned sandbox still exists, then deletes the sandbox and stops its temporary gateway on exit. Cached images, packages, and result files remain.

Fabric supplies the agent lifecycle and normalized results. OpenShell supplies isolation and policy enforcement. The launcher supplies image building and sandbox provisioning. No OpenShell-specific logic was added to the agent loop.

## Check the result

The launcher checks both turns succeeded, shared one runtime, recalled the label, and used persistent task processes with a `calculator.py` read call. Inspect the files under `.tmp/openclaw-code-review/`:

| File | Contents |
| --- | --- |
| `results.json` | Both normalized results and the review responses |
| `task-evidence.json` | Adapter/Gateway process identities, session turn count, and attempted read calls |
| `image-id.txt` | The exact task image used |

For failures, inspect `gateway-diagnostics.log` and `openshell-gateway.log` in the same directory. The sample returns `41`; that is not a bug without a specification. This demo verifies execution and session behavior, rather than review quality.

## Configuration notes

- Set `OPENSHELL_EXAMPLE_PORT` if host port `18080` is occupied.
- `CODE_REVIEW_MODEL` and `CODE_REVIEW_BASE_URL` override the model connection. If the destination changes, update `policy.yaml` too; it currently permits Node's inference calls to `10.86.19.10:8000`.
- The default endpoint is unauthenticated; `local-no-auth` is a placeholder, not a secret. Deployments using real credentials must provision them inside the sandbox.
- This POC supports OpenClaw with one runtime per sandbox. Its temporary gateway disables TLS/authentication, and Landlock uses `best_effort`; production deployment needs different settings.

See the [Dockerfile](runtime-image.Dockerfile) for pinned harness dependencies and the [OpenShell provider architecture](../../../environment-providers/openshell/README.md) for transport details.

<details>
<summary>Advanced: attach to an existing sandbox</summary>


Provision the image, creation-time policy, and model access first, and start the sandbox with `fabric-runtime-server serve`. Then use the same entry point:

```bash
export NEMO_FABRIC_OPEN_SHELL_PROVIDER="$PWD/target/debug/fabric-environment-openshell"
.venv/bin/python -m examples.code_review_agent \
  --variant openclaw --environment openshell \
  --gateway http://127.0.0.1:18080 \
  --image '<immutable-image-id>' \
  --sandbox-name '<sandbox-name>' --sandbox-id '<sandbox-id>' \
  --model nvidia/nemotron-3-ultra-550b-a55b \
  --base-url http://10.86.19.10:8000/v1 \
  --api-key-env FABRIC_LOCAL_INFERENCE_KEY \
  --input 'Read calculator.py and review its correctness and tests.' \
  --follow-up-input 'Recommend one focused regression test.' \
  --results-file .tmp/openclaw-code-review/results.json
```

The consumer stops the runtime and releases its attachment; you remain responsible for deleting the sandbox. The image packages the workspace at `/sandbox/repos/my-service` and the review skill at `/opt/nemo-fabric/skills/code-review`. Custom `--skill-path` values must be absolute task paths. Use `--no-skills` to omit skills or `--plan` to inspect configuration without executing.

</details>
