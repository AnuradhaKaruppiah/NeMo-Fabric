<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Run a Stateful LangGraph Agent in OpenShell

Portable Courier is a credential-free end-to-end example for NVIDIA NeMo
Fabric and OpenShell. It runs a custom LangGraph agent in a real OpenShell
sandbox without adding OpenShell-specific logic to the agent or its Fabric
adapter.

For component placement, lifecycle, and ownership, refer to the
[OpenShell environment provider architecture](../../integrations/openshell/README.md).

## Scenario

The example sends two inputs to one Fabric runtime:

```mermaid
flowchart TB
    subgraph TURN1["Turn 1"]
        direction LR
        ROUTE["Route"] --> DENIED["Preferred path<br/>HTTP 403"]
        DENIED --> FALLBACK["Fallback path<br/>HTTP 200"]
        FALLBACK --> STATE["Retain route in<br/>LangGraph state"]
    end

    subgraph TURN2["Turn 2"]
        direction LR
        DELIVER["Deliver"] --> RECEIPT["Collect receipt<br/>on consumer side"]
    end

    TURN1 --> TURN2
```

The first turn tries `GET /priority-lane`. The OpenShell L7 policy permits only
`GET /`, so the agent observes the denial and selects the allowed fallback.

The second turn uses the retained LangGraph state to write
`delivery-receipt.json`. The adapter declares the file, and Fabric collects it
through a traversal-safe, size-bounded operation.

## Run the Example

The primary deployment demonstration uses a caller-owned sandbox:

```bash
OPENSHELL_POC_MODE=deployment \
  bash examples/langgraph_openshell_poc/run-demo.sh
```

The script performs the following actions:

1. Builds the agent runtime image and OpenShell environment provider.
2. Starts an unmodified, source-built OpenShell gateway.
3. Creates a digest-pinned, policy-configured sandbox as the consumer.
4. Attaches Fabric to the sandbox by name and immutable ID.
5. Runs the two-turn LangGraph session and collects the receipt.
6. Stops the Fabric runtime and verifies that Fabric did not delete the
   caller-owned sandbox.

The script deletes the caller-owned sandbox during its own final cleanup.

To run both deployment and optional Fabric-managed development modes, use the
default command:

```bash
bash examples/langgraph_openshell_poc/run-demo.sh
```

To run only the development mode, use the following command:

```bash
OPENSHELL_POC_MODE=development \
  bash examples/langgraph_openshell_poc/run-demo.sh
```

Development mode uses `prepare_environment` to create the sandbox. It verifies
that `release_environment` deletes the Fabric-owned sandbox after the runtime
stops.

## Requirements

Install the following tools before running the example:

- Docker
- Rust and Cargo
- CMake
- Git
- Python 3
- `rustup`
- `tar`
- `uv`

The first run can take several minutes because the script builds the pinned
OpenShell gateway with its bundled Z3 feature. Later runs reuse the build under
`.tmp/`.

Set `OPENSHELL_ROOT` if the Fabric and OpenShell repositories are not sibling
directories. Set `OPENSHELL_POC_PORT` if port `18080` is unavailable.

## Expected Evidence

A successful deployment run demonstrates:

- the same Fabric runtime ID for both turns;
- an HTTP 403 response for the preferred route;
- an HTTP 200 response for the allowed fallback;
- retained LangGraph state during the delivery turn;
- a receipt collected below `.tmp/portable-courier/artifacts/`; and
- a caller-owned sandbox that still exists after Fabric detaches.

The OpenShell gateway log is available at
`.tmp/openshell-poc/gateway.log`.

## What This Example Proves

- A custom agent and its Fabric adapter can run unchanged inside OpenShell.
- One Fabric runtime preserves one ordered, stateful agent session.
- OpenShell, not Fabric, enforces the sandbox policy.
- The deployment consumer retains sandbox lifecycle and concurrency control.
- Fabric normalizes invocation results and declared artifacts across the
  sandbox boundary.
