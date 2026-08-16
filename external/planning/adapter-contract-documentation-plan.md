<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Adapter Contract Documentation Plan

## Outcome

Make the repository documentation the implementation source of truth for the
NVIDIA NeMo Fabric v1alpha2 adapter contract. A new adapter author should be
able to understand the value of an adapter, choose the correct integration
shape, implement the minimum contract, add only the configuration and optional
capabilities they need, package the descriptor, and verify the result.

This is a documentation-only change. It does not change schemas, runtime
behavior, adapter code, or the negotiated contract.

## Source Hierarchy

Use sources in this order when wording conflicts:

1. Shipped Rust types, runtime behavior, and generated JSON Schemas.
2. Existing runnable adapter and custom-agent examples.
3. Repository adapter-contract documentation and public adapter-authoring
   skill.
4. The Google design document for rationale, terminology, and illustration
   ideas.

The Google document contains useful design context, but it also includes
superseded examples and future ideas. In particular, the current local host
does not negotiate `AgentRunRequest` or `AgentRunResult`, and the contract does
not define generic `python_entrypoint` or `python_module` resolution kinds.

## Reader Path

The documentation will guide readers through one incremental path:

1. **Understand the boundary.** Learn why adapters are valuable, what an
   Adapter Target is, and what NeMo Fabric, the adapter, and the target own.
2. **Choose an integration shape.** Select a shared harness adapter, a shared
   framework adapter with registered targets, or a dedicated custom-agent
   adapter.
3. **Declare the minimum adapter.** Publish an Adapter Descriptor and implement
   `start`, `invoke`, and `stop` for one isolated runtime.
4. **Add configuration deliberately.** Accept only the `AgentConfig` fields the
   adapter applies and publish schemas for adapter-owned settings.
5. **Add optional behavior only when needed.** Keep Relay-backed ATOF streaming
   outside the adapter API; isolate native OpenAI streaming and future
   capabilities from the minimum path.
6. **Register and package metadata.** Make Adapter Descriptors and optional
   Adapter Target Descriptors discoverable without importing adapter code.
7. **Verify the adapter.** Exercise planning rejection, lifecycle cleanup,
   runtime isolation, and every declared optional capability.

Each stage will end with a concise next step so readers can progress without
returning to the overview.

## Phase 1: Adapter Contract Entry Path

Rewrite `docs/adapter-contract/README.md` as the beginner entry point:

- Explain the benefit of one adapter boundary across harnesses and custom
  agents.
- Define Adapter Target and the three supported integration shapes.
- Show one small Mermaid diagram for the northbound, planning, southbound, and
  target boundaries.
- State the absolute minimum surface before introducing optional APIs.
- Provide a staged reading and implementation path.
- Link directly to the canonical schemas, language bindings, public authoring
  skill, and the three representative examples.

Add `docs/adapter-contract/examples.md` as a compact reference map:

- mini-SWE-agent for a small shared harness adapter;
- the NeMo Agent Toolkit reference for one shared adapter and multiple
  registered custom-agent workflows; and
- the LangGraph email-phishing analyzer for a dedicated custom-agent adapter.

## Phase 2: Incremental Contract Pages

Rewrite the existing pages around the reader path while preserving implemented
semantics:

- `adapter-descriptor.md`: minimum descriptor first, then accepted config,
  adapter-owned schemas, target descriptors, and schema rules.
- `normalized-configuration.md`: show `FabricConfig` resolution into
  `AgentConfig`, then explain projection, validation ownership, and extensions.
- `execution.md`: lead with `start`/`invoke`/`stop`, lifecycle order, runtime
  isolation, and `RuntimeContext`; move advanced streaming detail out of the
  minimum path.
- `openai-streaming.md`: retain the exact optional native OpenAI Chat
  Completions streaming contract and transport rules in a dedicated advanced
  page.
- `results.md`: distinguish current JSON-compatible host output from the
  preview typed result boundary, then explain errors, artifacts, enrichment,
  and telemetry.
- `registration-and-discovery.md`: explain package records, deterministic
  discovery, exact-ID selection, target-driven adapter selection, and planning
  order.
- `custom-agents.md`: remove unimplemented generic resolution-kind claims and
  explain shared compared with dedicated custom-agent adapters using the
  shipped NAT and LangGraph examples.
- `conformance.md`: turn the future-suite discussion into a short, actionable
  verification stage without implying NVIDIA certification.

Use diagrams only for relationships that are harder to explain linearly:

- the complete adapter boundary in the overview;
- configuration resolution and projection; and
- shared compared with dedicated custom-agent integration.

## Phase 3: Contract Distribution and Authoring Parity

Align adjacent documentation that restates the contract:

- Add the new pages to `docs/index.yml` in implementation order.
- Correct and sharpen `schemas/SCHEMA.md` as the canonical schema map.
- Update the Python and TypeScript adapter-contract package READMEs where their
  examples or entry links are stale.
- Update `skills/nemo-fabric-build-adapter/SKILL.md` to follow the same stages
  and remove future-only resolution semantics.
- Update `adapters/README.md` so its scope includes custom-agent integration
  references without expanding the bundled-harness compatibility tables.

## Phase 4: Fabric 0.2 Entry Points and Hero

Make targeted, nonstructural updates outside the adapter-contract section:

- Update the repository hero to add custom agents while preserving the current
  applications-to-NeMo-Fabric-to-targets composition and visual style.
- Update `README.md` to describe harnesses and custom agents, add a concise
  Fabric 0.2 highlight section, link the adapter contract and examples, and
  remove completed adapter-contract work from the roadmap.
- Update `docs/about-nemo-fabric/overview.mdx` and the 0.2 release notes only
  where needed to keep the documentation entry points consistent.
- Update hero references and alt text in package documentation that embeds the
  repository image.

Do not rewrite installation, SDK, harness compatibility, or deployment
documentation unless a changed entry point makes a surgical correction
necessary.

## Validation

Before pushing:

1. Verify every command, path, package name, descriptor field, and capability
   claim against the release branch.
2. Check internal documentation links, heading hierarchy, Mermaid syntax, and
   image alt text.
3. Run `git diff --check`.
4. Run `just docs` and distinguish local toolchain limitations from content
   failures.
5. Inspect the rendered hero at full resolution.
6. Review the final diff for scope, repetition, preview-status accuracy, and
   NVIDIA documentation style.

## Completion Criteria

- A new reader can identify the correct adapter integration shape and its
  starting example from the overview.
- The minimum contract is visible without reading optional streaming,
  registration internals, or preview result types.
- Every detailed page states what is required, what is optional, and what to do
  next.
- The repository docs and schemas, not the Google design document, are clearly
  identified as authoritative.
- No page claims unimplemented entry-point kinds, resume support, typed invoke
  negotiation, or automated conformance.
- README and hero language represent both agent harnesses and custom agents in
  Fabric 0.2.
