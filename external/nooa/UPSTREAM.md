<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# OO Agents Adapter Upstream Handoff

## Status

Track A is complete as a source-only NVIDIA NeMo Fabric incubation adapter.
One shared `InteractiveAgent` runtime now hosts the registered `CodingAgent`
and markdown-backed ARC solver targets, preserves the live agent across ordered
invocations, and produces Relay telemetry plus Relay-backed ATOF streaming.

The implementation is intentionally not packaged in Fabric. The next change is
an ownership move to the OO Agents repository, not another Fabric-side adapter
abstraction.

## Stable Consumer Contract

| Surface | Stable value |
| --- | --- |
| Adapter ID | `nvidia.fabric.nooa` |
| Coding target ID | `nvidia.nooa.coding-agent` |
| ARC target ID | `nvidia.nooa.arc-solver` |
| Adapter contract | `fabric.adapter/v1alpha2` |
| Factory entry-point kind | `interactive_agent_factory` |
| Python range | `>=3.12,<3.14` (inherited from OO Agents) |
| Relay range when enabled | `>=0.7.2,<0.8` |
| Native OpenAI streaming | Unsupported; capability remains `false` |
| Relay ATOF streaming | Supported through ordinary `invoke` and `Runtime.invoke_stream()` |

The upstream move must preserve these IDs and descriptor shapes so existing
`FabricConfig` documents do not change.

## Implemented Ownership Boundaries

The shared adapter owns registered factory resolution, normalized model-client
construction, queue dispatch, `AgentMessage` capture, safe results, Relay
activation, runtime-local quarantine, and cleanup of adapter-created clients.

Target factories own the concrete agent class and construction policy. The
Coding target applies the runtime workspace, system instruction, and exact text
skills. The ARC target applies Fabric-owned paths, a fixed anonymous identity,
ARC settings, and the finite-session continuation predicate. The shared
dispatcher has no `CodingAgent`, ARC class, or custom-channel branches.

Relay instrumentation uses OO Agents' public `install_nemo_relay()` middleware.
Fabric owns the request Agent scope so it can attach request, invocation, and
runtime correlation IDs. Telemetry setup failure prevents target execution;
teardown or artifact failure after execution preserves the functional result
and marks telemetry degraded. A leaked scope quarantines later turns.

## Verification Record

The Fabric branch was verified on Python 3.13 against Fabric baseline
`758b6066504a724a6fc1941b8415b76ed31f0ab5`, OO Agents commit
`97f52dec84ed88ca3b202f91bee0bc0074626246`, and `nemo-relay==0.7.2`.

- Focused OO adapter, target, Relay, and example suite: 35 passed.
- Full Fabric Python suite after Track A: 1,250 passed and 17 skipped.
- Fabric documentation validation: passed; the only warning was the expected
  unauthenticated Fern redirect check.
- Real Relay plugin test: correlated Agent-scope ATOF and finalized ATIF were
  produced with current-invocation artifact filtering.
- Real persistent-host streaming E2E: two turns yielded nonempty, isolated ATOF
  streams containing Agent, LLM, and `execute_python` records; each target turn
  executed once and returned a separate successful terminal result.
- Both initial target factories also pass deterministic Relay wrapping tests.
- OO Agents' middleware suite was run directly against Relay 0.7.2: 27 of 36
  tests pass unchanged. All nine remaining failures are test-contract updates,
  not adapter or middleware execution failures: two sanitizer callbacks need
  the new `(payload, context)` signature, and seven async tests must await
  `subscribers.flush_async()` instead of calling blocking `flush()`.

The signed Fabric implementation stack preceding this handoff commit is:

1. `6cf2c0cc` — source-only shared adapter;
2. `d9c57371` — CodingAgent target and code-review variant;
3. `5ef28c9c` — concrete ARC solver target and completion policy; and
4. `64d16feb` — Relay telemetry and ATOF streaming.

## Review Findings

No Fabric-incubation release blocker remains in the shared lifecycle. The final
review confirmed closed descriptor schemas, explicit registered factories,
runtime and model isolation, invocation-local message subscriptions, cleanup on
partial construction, safe error text, current-turn Relay artifacts, and no
imports from private Fabric runtime modules.

The review also removed the ARC consumer-configurable alias. The target now
uses the neutral `the game` identity, so a caller cannot accidentally expose a
real game ID. ARC run paths remain derived from Fabric's artifact/workspace
context and cannot be supplied through target settings.

The following are upstream packaging blockers rather than defects in the
source incubation implementation:

1. OO Agents still declares `nemo-relay>=0.6,<0.7` in its optional extra.
2. `MdArcSolverAgent` lives in `examples/arc_agi_3/solver_agent.py`, so the
   incubation factory currently requires that directory on `PYTHONPATH`.
3. The source-only Fabric directory has no wheel metadata or installed
   descriptor data files.

## Upstream Migration Checklist

1. In OO Agents, change the optional Relay range to `>=0.7.2,<0.8`, update the
   two sanitizer test callbacks and seven async flush calls described above,
   then require all 36 middleware tests to pass.
2. Give the ARC solver a stable import path in an installable OO Agents package,
   or publish its Fabric target factory beside the ARC example package. Replace
   the incubation `import solver_agent` without changing the target ID.
3. Create an installable integration distribution in the OO Agents workspace.
   It should depend on the public `nemo-fabric-adapter-contract` and
   `nemo-fabric-adapters-common` packages, and expose its Relay dependency as an
   optional extra.
4. Install the adapter and both `*.fabric-target.json` files below the standard
   `share/nemo-fabric` discovery location. Verify planning and execution from a
   clean environment with no Fabric source `PYTHONPATH`.
5. Move adapter-owned tests with the package. Keep Fabric's consumer example and
   one cross-repository conformance E2E so contract drift remains visible.
6. Run one credentialed CodingAgent code-review smoke with `--relay --stream`
   and one full ARC harness smoke. Confirm nested real OO middleware records,
   current-turn ATIF, and optional OTel/OpenInference export.
7. Submit the OO Agents change first, then replace or remove Fabric's incubated
   source directory after the upstream package is available. Preserve all IDs.

## Deliberate Follow-Ups

These items are outside Track A and should not block the shared adapter move:

- add `MemArcSolverAgent` only after memory-store ownership, seeding, and cleanup
  are explicit;
- aggregate provider-reported invocation-local usage into `AgentUsage`,
  including child agents with independent event managers;
- prove Relay middleware propagation for child agents that do not share the
  root event manager;
- replace the deterministic ARC harness with an optional credentialed full-game
  CI profile when its external runtime is practical;
- add a dedicated plain-`Agent`/`BenchAgent` adapter as Track B rather than
  teaching arbitrary method dispatch to the shared `InteractiveAgent` adapter;
  and
- evaluate MCP and normalized tool policy only after OO Agents exposes a stable,
  target-independent capability registry.
