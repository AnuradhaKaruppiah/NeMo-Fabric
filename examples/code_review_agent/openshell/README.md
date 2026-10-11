<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# OpenClaw code review inside OpenShell

See the [launcher function flow](FLOW.md) for the shell functions, Python lifecycle, task execution path, and ownership boundaries.

This NVIDIA NeMo Fabric POC runs the existing code-review configuration and OpenClaw adapter with the agent loop, tools, and session inside an OpenShell sandbox (Model A). The host runs the consumer, SDK coordinator, and OpenShell provider. The model server remains external at `http://10.86.19.10:8000/v1`, serving `nvidia/nemotron-3-ultra-550b-a55b`.

The consumer attaches to a caller-owned sandbox, starts one managed runtime, invokes a review and a follow-up, stops the runtime, and releases its attachment. The deployment script then deletes the sandbox and stops its temporary OpenShell gateway. OpenClaw's loopback Gateway runs inside the sandbox and is separate from the OpenShell gateway. This experiment does not use `prepare_service()` or introduce a FabricService. One runtime is the experiment's scope; it is not a restriction on Model A.

## Run the complete POC

Run from the repository root on Linux or Apple Silicon macOS with Rust, `uv`, Python, `curl`, `tar`, and a running Docker daemon available on `PATH`:

```bash
bash examples/code_review_agent/openshell/run-demo.sh
```

The script reuses the [LangGraph OpenShell launcher](../../langgraph_openshell/run-demo.sh) for its checksummed OpenShell `v0.0.116` binaries and temporary gateway. It builds the Fabric runtime binaries, provider, and a host SDK environment, then builds and smoke-tests the task image. The image includes Python 3.12, Node.js 24.16.0, npm 11.16.0, and OpenClaw 2026.9.4. Package installation happens during image build. Base-image tags and transitive package resolution are not digest-locked; the exact built image ID is recorded with the evidence.

Set `OPENSHELL_EXAMPLE_PORT` if port `18080` is occupied. `CODE_REVIEW_MODEL` and `CODE_REVIEW_BASE_URL` override model configuration; changes to the destination must also be reflected in `policy.yaml`. The supplied policy permits only Node's `POST /v1/chat/completions` to `10.86.19.10:8000`. `FABRIC_LOCAL_INFERENCE_KEY=local-no-auth` is a non-secret placeholder for this unauthenticated local endpoint. Real model credentials must be provisioned by the deployment rather than embedded in the image or supplied as command-line values.

The sandbox uses the existing demo's `best_effort` Landlock setting. The temporary OpenShell gateway disables TLS and authentication on loopback for this functional experiment. These settings are not production deployment guidance. SQLite temporary files are directed to the policy-approved `/tmp` directory; without this, the pinned OpenClaw session database cannot initialize in the sandbox.

## Evidence and cleanup

Host evidence is saved under `.tmp/openclaw-code-review/`:

- `results.json`: both normalized invocation results, with the same runtime ID and sandbox runner metadata.
- `task-evidence.json`: one task-side adapter PID, one OpenClaw Gateway PID, two user turns in one session, and actual `read` tool calls. The observer reads the pinned harness's session database while it exists; it exports no configuration, tokens, or full transcripts.
- `gateway-diagnostics.log`: task-side model transport and warning/error messages.
- `openshell-gateway.log`: lifecycle diagnostics from the temporary OpenShell gateway.
- `image-id.txt`: the immutable image used for that run.

The follow-up must recall a unique first-turn label without host replay or a marker file. The first response must reference `calculator.py`; the observer must see a read call for that file. The sample returns `41`, which is not a bug without a specification. Review quality remains model-dependent, and the POC does not establish that every proposed finding is correct. Consumer-saved JSON is separate from Fabric's declared artifact-download mechanism.

The consumer stops and releases in `finally`, including after startup/invocation failure. The launcher checks that release preserves its caller-owned sandbox and deletes it on exit. Image layers, downloaded binaries, the host SDK environment, and evidence remain cached under the checkout or Docker; running sandboxes and the temporary gateway are cleaned up.

## Attach to an existing deployment

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

The standalone consumer releases its attachment; its caller remains responsible for deleting the sandbox. Use `--plan --image '<immutable-image-id>'` to inspect the configuration without an attachment. The packaged workspace is `/sandbox/repos/my-service`, and the default skill maps explicitly to `/opt/nemo-fabric/skills/code-review`. Custom `--skill-path` values must be absolute paths already provisioned in the task image. `--no-skills` remains supported. The remote runner uses task-side `python3`, ignoring host `ADAPTER_PYTHON`. Host result paths stay on the host.

For a local comparison, use `--environment local` with the same model flags and `--follow-up-input`. Install the pinned OpenClaw prerequisites described in the [adapter README](../../../adapters/python/openclaw/README.md) first. OpenShell execution currently supports only the OpenClaw variant, without shared-service mode, Relay, or streaming.
