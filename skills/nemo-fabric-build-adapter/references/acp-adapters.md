<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# ACP-backed adapters

Use Agent Client Protocol (ACP) to share transport and session mechanics across
harnesses. Do not use it to hide target-specific configuration.

## Proven boundary

Kilo Code and Qwen Code prototypes reused one TypeScript process runner for:

- explicit process launch with no shell;
- newline-delimited ACP framing;
- protocol negotiation and capability checks;
- session creation, prompt turns, cancellation, and shutdown;
- current-turn assistant text collection;
- fail-closed client permission requests; and
- limits on frames, updates, text, and deadlines.

Their projectors remained different. Each projector owned model providers,
credentials, instructions, tool policy, skills, MCP projection, environment
isolation, errors, results, and any native modes.

An opaque diagnostic callback is an acceptable runner seam. Parsing a
harness-specific warning in the shared runner is not.

## Decide between ACP and a native SDK

Compare both paths before implementation:

| Question | Why it matters |
| --- | --- |
| Can the harness select the normalized model and credentials through ACP startup or session options? | ACP does not define provider setup. |
| Can replace and append instructions be enforced? | Many agents require harness settings or CLI flags outside ACP. |
| Can tool allow/block policy be enforced at execution time? | Permission requests and model tool schemas are not sufficient proof. |
| Can skills and turn limits be configured deterministically? | These are harness semantics, not ACP fields. |
| Which MCP transports, headers, authentication, and filters survive projection? | ACP carries server connections but not every harness policy. |
| Are MCP startup failures structural protocol errors? | Some harnesses report them only through diagnostics. |
| Does the prompt response include per-turn usage and cost? | ACP usage is optional and may be cumulative or absent. |
| Does the harness expose required telemetry? | ACP notifications are not a Relay integration. |
| Can installation and state be isolated? | The projector still owns executable and environment policy. |

Choose ACP when the retained capability set satisfies the descriptor. Keep the
native SDK when the missing semantics are required.

## Projector contract

A harness projector should return explicit runner inputs:

- executable and argument array;
- absolute workspace;
- restricted environment;
- ACP MCP server list;
- protocol and resource limits; and
- callbacks for opaque diagnostics.

It should reject every accepted Fabric field that cannot be enforced. It must
also map ACP stop reasons, optional usage, generic protocol failures, and
harness diagnostics into stable adapter results.

Do not add provider names, model routing, instruction flags, tool names, skill
paths, or harness warning strings to the common runner.

## Verification

Test the common runner with a deterministic ACP fixture, then test the real
harness process.

The deterministic suite should cover negotiation, capability mismatch, warm
turns, current-turn-only text, cancellation, permission requests, process exit,
timeouts, oversized frames, excessive updates, excessive text, and idempotent
shutdown.

The real-process suite should cover:

- model and credential projection;
- replace and append instructions;
- actual denied-tool execution;
- skill loading;
- discovery and execution for every claimed MCP transport;
- MCP headers and secrets without logging them;
- unavailable MCP behavior;
- zero-invocation start and stop;
- warm continuation and independent runtime isolation;
- usage and cost behavior; and
- process-tree cleanup on every supported platform.

Document unsupported fields in the adapter capability matrix. Do not infer
support from an advertised ACP capability or a status API when an end-to-end
behavioral test is possible.
