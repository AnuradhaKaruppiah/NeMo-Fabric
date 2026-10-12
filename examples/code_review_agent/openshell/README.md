<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Run a code-review agent inside OpenShell

This NVIDIA NeMo Fabric example runs the existing code-review agent example inside an OpenShell sandbox, with OpenClaw and Hermes as harness variations. The selected harness's agent loop, tools, and conversation state stay in the sandbox. The host uses Fabric to start the runtime, send a review and follow-up, and stop it. Inference runs on your external model server.

The default model ID is `nvidia/nemotron-3-ultra-550b-a55b`. Set
`CODE_REVIEW_BASE_URL` to an OpenAI-compatible endpoint reachable from the
sandbox before running the example. Replace the hostname below with your model
server:

```bash
export CODE_REVIEW_BASE_URL="http://model-server.example:8000/v1"
```

## Run it

From the repository root, with Docker running and Rust/Cargo, `uv`, Python, `curl`, and `tar` on `PATH`:

```bash
# OpenClaw (default)
bash examples/code_review_agent/openshell/run-demo.sh

# Hermes
CODE_REVIEW_HARNESS=hermes bash examples/code_review_agent/openshell/run-demo.sh

# Hermes with Relay model/tool telemetry
CODE_REVIEW_HARNESS=hermes CODE_REVIEW_RELAY=1 \
  bash examples/code_review_agent/openshell/run-demo.sh
```

The first run builds and downloads dependencies; Hermes source is staged under `.tmp/hermes-openshell`, leaving an existing Hermes checkout untouched. Later runs reuse cached files and Docker layers.

## How it works

1. **Prepare:** [The wrapper](run-demo.sh) selects code review and calls the [shared launcher](../../openshell/run-demo.sh). It builds the task image, starts OpenShell, and creates a sandbox with a policy generated from one [model declaration](model_access.py). A task-side preflight checks credential availability and inference access before the agent starts. The sandbox starts `fabric-runtime-server`; the agent starts in the next step.
2. **Run:** `run_consumer()` launches the [existing Python consumer](../__main__.py). Fabric attaches to the sandbox, starts the selected adapter, and invokes the same runtime twice. The selected harness reads `calculator.py` and the review skill. The follow-up asks for a label from the first turn, demonstrating conversation continuity without host replay. A test-only observer checks process identity; OpenClaw also uses it for read/session evidence. Hermes supplies read evidence through its returned tool transcript. With Relay enabled, Hermes also emits ATIF/ATOF inside the task; NeMo Fabric collects declared snapshots before returning each result.
3. **Clean up:** The Python consumer stops the agent and releases its attachment in `finally`. The launcher checks the saved results/evidence, verifies that the caller-owned sandbox still exists, then deletes the sandbox and stops its temporary gateway on exit. Cached images, packages, and result files remain.

Fabric supplies the agent lifecycle and normalized results. OpenShell supplies isolation and policy enforcement. The launcher supplies image building and sandbox provisioning. No OpenShell-specific logic was added to the agent loop.

## Check the result

The launcher checks both turns succeeded, shared one runtime, recalled the label, and used persistent task processes with a `calculator.py` read call. Inspect the files under `.tmp/openclaw-code-review/` or `.tmp/hermes-code-review/`:

| File | Contents |
| --- | --- |
| `results.json` | Both normalized results and the review responses |
| `task-evidence.json` | Process identities; OpenClaw also includes session counts and attempted reads |
| `verification.json` | Checks of runtime identity, label recall, process persistence, and file reads |
| `image-id.txt` | The exact task image used |
| `model-access.json`, `policy.yaml` | Model connection and matching creation-time network rule; no credential values |
| `preflight.json` | Successful task-side credential and inference check |

With Relay enabled, `results.json` includes host paths to each turn's gzip ATIF/ATOF snapshots under `examples/code_review_agent/artifacts/hermes/relay-invocations/`. Snapshots preserve complete telemetry within the provider's bounded transport. Use `gzip -dc <artifact-path>` to inspect a file. The launcher validates model/tool events, runtime/request correlation, and a successful `calculator.py` read from Relay. Process identity remains a separate observer check.

For failures, inspect the consumer error/result and `openshell-gateway.log`; OpenClaw also saves `gateway-diagnostics.log`. The sample returns `41`; that is not a bug without a specification. This demo verifies execution and session behavior, rather than review quality.

## Configuration notes

- Set `OPENSHELL_EXAMPLE_PORT` if host port `18080` is occupied.
- `CODE_REVIEW_BASE_URL` is required, and `CODE_REVIEW_MODEL` optionally changes the model ID. The launcher generates the matching network policy automatically. Only the selected harness's inference binary can POST to that endpoint's `/chat/completions` path. The preflight uses the same binary and rule.
- The launcher targets unauthenticated endpoints with the non-secret `local-no-auth` placeholder. Real credentials belong to the deployment: provision them in an existing sandbox, reference their environment variable with `--api-key-env`, and grant matching model access before attaching. Credential values are never written into model declarations or images.
- This POC supports OpenClaw and Hermes with one runtime per sandbox. Hermes supports collected Relay files; streaming remains unsupported. Its temporary gateway disables TLS/authentication, and Landlock uses `best_effort`; production deployment needs different settings.

See the [OpenClaw image](runtime-image.Dockerfile) and [Hermes image](hermes-runtime-image.Dockerfile) for dependencies. Hermes uses Python 3.14, the source revision pinned by the repository, and its frozen dependency lock. Its runtime configuration selects the image virtualenv explicitly. See the [OpenShell provider architecture](../../../environment-providers/openshell/README.md) for transport details.

<details>
<summary>Advanced: attach to an existing sandbox</summary>


Generate `model-access.json` and `policy.yaml` with `python3 examples/code_review_agent/openshell/model_access.py --harness openclaw --output-dir .tmp/openclaw-code-review` after setting `CODE_REVIEW_BASE_URL`. The checked-in policy files are filesystem templates. Provision the image, generated creation-time policy, and model access first, and start the sandbox with `fabric-runtime-server serve`. Then use the same entry point:

```bash
export NEMO_FABRIC_OPEN_SHELL_PROVIDER="$PWD/target/debug/fabric-environment-openshell"
.venv/bin/python -m examples.code_review_agent \
  --variant openclaw --environment openshell \
  --gateway http://127.0.0.1:18080 \
  --image '<immutable-image-id>' \
  --sandbox-name '<sandbox-name>' --sandbox-id '<sandbox-id>' \
  --model nvidia/nemotron-3-ultra-550b-a55b \
  --base-url "$CODE_REVIEW_BASE_URL" \
  --api-key-env FABRIC_LOCAL_INFERENCE_KEY \
  --input 'Read calculator.py and review its correctness and tests.' \
  --follow-up-input 'Recommend one focused regression test.' \
  --results-file .tmp/openclaw-code-review/results.json
```

Add `--relay` for collected Hermes telemetry. Use `--variant hermes` with the Hermes image/generated policy for an existing Hermes deployment. The consumer stops the runtime and releases its attachment; you remain responsible for deleting the sandbox. The image packages the workspace at `/sandbox/repos/my-service` and the review skill at `/opt/nemo-fabric/skills/code-review`. Custom `--skill-path` values must be absolute task paths. Use `--no-skills` to omit skills or `--plan` to inspect configuration without executing.

</details>
