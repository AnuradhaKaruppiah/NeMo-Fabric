<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# NVIDIA-labs Object Oriented Agents (NOOA) Adapter for NVIDIA NeMo Fabric

This directory provides two NOOA integrations for the NVIDIA NeMo Fabric
lifecycle contract:

- `nvidia.fabric.nooa` is the shared adapter for registered `InteractiveAgent`
  targets.
- `nvidia.fabric.nooa.bench-agent` is a dedicated harness adapter for
  `nooa_bench.BenchAgent` and Harbor tasks.

The shared adapter deliberately implements only the common interactive-agent
boundary:

- a target descriptor selects a Python factory;
- `start` calls that factory once and retains its agent;
- `invoke` submits one string to `user_messages` and runs the standard NOOA
  `race` / drain / `handle` dispatcher loop;
- `AgentMessage` events become the normalized response and message list;
- `stop` calls target-owned cleanup, `agent.close()`, or the queue manager's
  fallback shutdown.

The adapter translates normalized models into NOOA `UnifiedLLM` clients
and supplies resolved system instructions and exact skill paths through the
factory build context. The descriptor accepts `models`, model endpoint and
temperature fields, `instructions.system`, and `skills`.

`WAIT` resumes inside the same Fabric invocation when another NOOA channel
wakes. `DONE`, `NEED_INPUT`, and the legacy `GET_USER_INPUT` end the invocation.
The latter two are successful terminal adapter calls with `completed=false` and
their native reason retained in the output. `messages` contains ordered
`{"content": "..."}` records. `response` is the final message content, or the
terminal `RespondResult.explanation` when the agent emits no `AgentMessage`.

The adapter does not claim native OpenAI streaming. Relay-backed ATOF streaming
uses the ordinary `invoke` operation and does not require the adapter
descriptor's `capabilities.streaming` flag.

## Relay Telemetry and ATOF Streaming

The adapter declares Relay outputs for ATIF, OpenTelemetry, and OpenInference
and requires `nemo-relay>=0.7.2,<0.8` whenever Relay is enabled. Fabric's
invocation environment supplies the generated `FABRIC_RELAY_CONFIG_PATH`; the
adapter verifies that it matches `RuntimeContext.telemetry.config_path`, rejects
ambient user/project plugin configuration, and activates only the generated
plugin document.

Each Relay invocation installs NOOA's public `install_nemo_relay()`
middleware, opens one Agent scope carrying the Fabric request, invocation, and
runtime IDs, runs the selected agent operation exactly once, then uninstalls
the middleware and finalizes current-turn artifacts. Interactive targets use
the `nooa-interactive-agent-request` scope and BenchAgent uses
`nooa-bench-agent-request`. The middleware records nested agent-method, LLM,
and `execute_python` scopes. A telemetry setup failure before execution returns
a safe failed result without executing the agent. A teardown, flush, or
artifact failure after execution preserves the functional result and marks
telemetry degraded. A leaked scope quarantines telemetry on later turns instead
of nesting them below stale state.

`Runtime.invoke_stream()` is provided by Fabric. With Relay enabled and the
runtime started using `streaming=True`, it consumes matching raw ATOF records
while this adapter performs its single ordinary `invoke`; `stream.result()` is
the independent terminal result. The adapter does not implement
`invoke_openai_stream()` and keeps native streaming capability disabled.

Install NOOA core and CLI without their optional Relay extra, then install
`nemo-relay>=0.7.2,<0.8` in the adapter environment.

## Register a Target

A separately installed target publishes a `*.fabric-target.json` record:

```json
{
  "contract_version": "fabric.adapter/v1alpha2",
  "type": "workflow",
  "id": "com.example.nooa.my-agent",
  "adapter_id": "nvidia.fabric.nooa",
  "spec": {
    "entrypoint": {
      "kind": "interactive_agent_factory",
      "ref": "my_package.fabric_target:create_agent"
    },
    "settings_schema": {
      "type": "object",
      "properties": {},
      "additionalProperties": false
    }
  }
}
```

The reference uses `package.module:factory` syntax. The factory receives one
`InteractiveAgentBuildContext` and may return an `InteractiveAgent` directly or
an `InteractiveAgentTarget` with explicit cleanup. A target whose external
environment, rather than one agent turn, defines completion may also provide a
`continue_after(agent, reason, explanation)` predicate:

```python
from nemo_fabric_adapters.nooa import InteractiveAgentBuildContext
from nemo_fabric_adapters.nooa import InteractiveAgentTarget


async def create_agent(
    context: InteractiveAgentBuildContext,
) -> InteractiveAgentTarget:
    agent = MyInteractiveAgent(
        cwd=context.workspace,
        **context.config.workflow.settings,
    )
    return InteractiveAgentTarget(agent=agent, close=agent.close)
```

The predicate only decides whether another queue wake-up is required after a
non-`WAIT` result. Queue dispatch, terminal-result validation, message capture,
and Fabric result normalization remain shared adapter behavior. Most targets
should return the bare agent and use the default prompt-turn policy.

The factory owns target-specific construction, including model creation and
validation of dependencies that cannot be checked during Fabric planning. The
adapter validates the returned object's public interactive-agent surface; it
does not import target-specific agent classes.

## Run from Source

This directory intentionally has no package metadata. Use one Python
environment containing NeMo Fabric, the common adapter host, NOOA, and the
registered target package, then expose the source adapter:

```bash
export PYTHONPATH="$PWD/external/nooa/src${PYTHONPATH:+:$PYTHONPATH}"
```

During source development, include this directory and the target descriptor's
directory in `FabricConfig.discovery.local_paths`.

## BenchAgent Harness Adapter

`nooa-bench.fabric-adapter.json` exposes `nooa_bench.BenchAgent` directly as a
harness adapter. It is intentionally separate from the shared
`InteractiveAgent` dispatcher: BenchAgent derives from NOOA's base `Agent`
and implements the benchmark-specific `_run_evaluation(task_input)` contract.

One Fabric invocation maps to one BenchAgent task:

- the Fabric string input becomes `task_input.user_message`;
- the Fabric workspace becomes `task_input.working_dir`;
- `instructions.system` becomes `task_input.instructions`;
- the native `response`, `success`, and structured `result` fields become the
  normalized Fabric result;
- NOOA's task token accumulator becomes normalized Fabric usage.

The adapter accepts exactly one model, closes the shell replaced by
`_run_evaluation()` before each task, and closes the active shell and model at
runtime shutdown. Native errors are not copied into the public result because
they can contain model, tool, or environment details.

Harbor selects this adapter through `FabricAgent` using
`fabric_adapter_id=nvidia.fabric.nooa.bench-agent`. The task environment must
contain NeMo Fabric, this adapter source, NOOA core, and `nooa-bench`; its
Fabric configuration bundle must expose the adapter descriptor beneath the
bundle's `adapters/` directory. The resulting path is:

```text
Harbor task -> FabricAgent -> Fabric runner -> BenchAgent adapter -> BenchAgent
```

Harbor retains ownership of task materialization, the `/testbed` workspace,
verification, reward calculation, retries, and job artifacts. The BenchAgent
adapter owns only model construction, task execution, result normalization,
Relay telemetry, and runtime cleanup.

See the runnable [BenchAgent Harbor walkthrough](../../examples/harbor/nooa_bench/README.md)
for task-image preparation, baseline reward verification, and Relay ATOF/ATIF
validation.

## CodingAgent Target

`targets/coding-agent.fabric-target.json` registers
`nvidia.nooa.coding-agent`. Its factory constructs the host-neutral
`nooa_cli.coding.CodingAgent` directly; it does not import ACP. The factory uses
the selected `default` model, the resolved runtime workspace, the portable
system instruction, and configured text-skill directories. `CodingAgent.close()`
owns its shell, skill registry, queue jobs, and model shutdown; the adapter also
finalizes its model clients idempotently during runtime cleanup.

The maintained code-review example exposes this target as `--variant nooa`.
See [the example README](../../examples/code_review_agent/README.md) for its
source bootstrap and live command.

## ARC Solver Target

`targets/arc-solver.fabric-target.json` registers
`nvidia.nooa.arc-solver`. The factory constructs the concrete markdown-backed
`MdArcSolverAgent`, which derives from `ArcSolverBase`, directly from the OO
Agents ARC example. The memory-backed variant is not registered by this
adapter.

The target derives its run directory as `<artifact-root>/nooa-arc` (or
`<workspace>/nooa-arc` when no artifact root is supplied). Consumers cannot
inject a run directory. An external ARC harness must use that same directory
for its `states.jsonl` and `actions.jsonl` IPC. The factory fixes the
agent-visible identity to the neutral `the game` value and passes it as both
`game_id` and `alias`; consumers cannot accidentally configure a real game ID
and defeat the ARC launcher's identity-redaction boundary. The closed target
settings are `reflect_every`, `visual`, `png_scale`, and
`max_actions_per_turn`. At most one configured skill directory is accepted,
and it must contain `SKILL.md`.

The ARC headless launcher treats an agent `DONE` as an end-of-turn signal while
the harness decides when the game session is finished. The target therefore
continues after `DONE` until its latest state is `WIN` or `GAME_OVER`, or the
harness publishes a `harness stopped:` note. This is target-supplied completion
policy over the shared queue dispatcher; the adapter contains no ARC class or
channel-name branch.

To run the ARC target from source, expose both repositories before starting
Fabric:

```bash
export PYTHONPATH="$PWD/external/nooa/src:$PWD/../labs-OO-Agents/examples/arc_agi_3${PYTHONPATH:+:$PYTHONPATH}"
```

The deterministic Fabric test uses a finite fake harness with the same
`user_messages` / `game_states` / `WAIT` / action / terminal sequence. A manual
full-game smoke uses the NOOA ARC harness against the derived run
directory and requires its `arc` optional dependencies and external game
service configuration.

NOOA declares Python `>=3.12,<3.14`; the adapter does not claim a broader
version range.

The adapter is an execution bridge, not a sandbox. A CodeAct target such as
`CodingAgent` can run generated Python and shell commands with the permissions
of its Fabric environment. Consumers must select an environment provider with
the required operating-system isolation and must not treat the in-process
adapter boundary as a security boundary.
