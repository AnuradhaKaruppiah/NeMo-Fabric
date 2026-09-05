<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# LangGraph in an OpenShell Fabric Capsule

Portable Courier is the smallest complete OpenShell strategic-value POC for
NVIDIA NeMo Fabric. It is credential-free and exercises a real, non-local
environment instead of mocking the boundary.

One Fabric runtime owns one stateful LangGraph session inside one OpenShell
sandbox. On turn one, the graph tries `GET /priority-lane`; the OpenShell L7
policy permits only `GET /`, so the graph observes the denial and reaches the
allowed fallback route. On turn two, the retained graph state is used to write
`delivery-receipt.json` below the capsule artifact root. Fabric then collects
that declared file through a typed, traversal-safe, size-bounded provider
operation and returns a local artifact reference to the consumer.

```mermaid
sequenceDiagram
    participant C as Consumer
    participant F as Fabric
    participant O as OpenShell gateway
    participant S as OpenShell sandbox
    participant G as LangGraph courier

    C->>O: provision digest-pinned sandbox + policy
    O->>S: start capsule runner
    C->>F: attach_environment(config, sandbox reference)
    F->>O: verify identity, image, command, policy, readiness
    C->>F: start_runtime_in(environment)
    F->>S: adapter start
    C->>F: invoke("route")
    F->>G: retained session turn 1
    G->>O: GET /priority-lane
    O-->>G: denied by L7 path policy
    G->>O: GET /
    O-->>G: allowed fallback
    C->>F: invoke("deliver")
    F->>G: retained session turn 2
    G->>S: write delivery-receipt.json
    F->>S: collect declared artifact
    F-->>C: result + local receipt path
    C->>F: stop runtime
    C->>F: release environment
    F-->>C: detached; sandbox remains caller-owned
    C->>O: delete sandbox when deployment lifecycle is complete
```

## Run the vertical slice

The runner builds the capsule and Fabric's Rust-SDK-backed OpenShell provider,
starts a source-built Docker gateway on port `18080`, and runs the same agent in
two ownership modes. Deployment mode provisions as the consumer and proves that
Fabric detaches without deleting the caller-owned sandbox. Development mode
lets Fabric create the sandbox and proves that explicit release deletes it:

```bash
bash examples/langgraph_openshell_poc/run-demo.sh
```

The runner requires Docker, Rust/Cargo, CMake, Git, Python 3, `rustup`, `tar`,
and `uv`. Its first source build can take several minutes because the OpenShell
gateway is built with its portable bundled-Z3 feature.

Set `OPENSHELL_ROOT` when the repositories are not siblings, or
`OPENSHELL_POC_PORT` when port `18080` is unavailable. By default, the runner
exports the public OpenShell revision pinned by the provider into a temporary
build tree. It does not require or modify a Fabric-specific OpenShell branch.
The gateway log is kept at `.tmp/openshell-poc/gateway.log`; the collected
receipt is kept below `.tmp/portable-courier/artifacts/`.

The default `OPENSHELL_POC_MODE=both` runs both ownership modes. Set it to
`deployment` or `development` to run only one. The pinned OpenShell build is
cached below `.tmp/` by commit, so subsequent runs reuse the expensive first
build.

The demonstrated deployment path keeps environment provisioning and deletion
with the consumer. Fabric receives a typed resource reference, verifies the
existing sandbox, and returns a runtime-ready handle. Fabric also supports an
optional `prepare_environment()` path for self-contained development flows
where Fabric creates and later deletes the sandbox.

## What this proves

- An existing Fabric Python-adapter contract can run unchanged inside an
  OpenShell capsule.
- Fabric's OpenShell integration links directly to the public OpenShell Rust
  SDK; it does not shell out to the OpenShell CLI for runtime operations.
- A consumer can provision with unmodified OpenShell, attach by immutable
  identity, and retain deletion authority.
- A Fabric runtime remains a single-session, ordered invocation boundary; the
  consumer remains responsible for concurrency.
- Fabric can express the environment, ownership, connection, workspace,
  artifact roots, capsule image, and expected policy in one configuration.
- OpenShell, not Fabric, enforces filesystem, process, and L7 network policy.
- Adapter artifacts cross the sandbox boundary only when declared, and only
  through the bounded collection operation.
