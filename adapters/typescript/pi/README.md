<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Pi SDK Adapter

This package provides a Pi SDK harness adapter for NVIDIA NeMo Fabric. It
embeds the Pi SDK in the adapter's Node process and maps one Fabric runtime to
one in-memory Pi session.

The current adapter supports:

- One explicit Pi-known model selected from the `default` role or the sole
  configured role
- Runtime API-key credentials named by `models.<role>.api_key_env`
- Optional `models.<role>.base_url`
- Optional replacement system instructions
- Tool allow and block policy
- Fabric custom tools loaded through normalized `tools.definitions`
- Explicit normalized `skills.paths`
- Explicit local `.ts` or `.js` extension files contained by the Fabric
  workspace
- Slash commands registered by those explicit extensions
- Ordered plain-text invocations with a `{ "response": "..." }` terminal
  output

Ambient Pi settings, context files, packages, extensions, skills, prompts,
themes, model files, credentials, and session files are disabled. Explicitly
configured extensions are trusted code; configuration precedence after
extension startup remains an open design decision.

## Custom tool modules

The adapter accepts custom tools with `kind: "module"`. The `ref` is a
workspace-relative JavaScript or TypeScript file with an optional named export,
for example `tools/review.ts#createTool`. Without a fragment, the adapter uses
the default export.

The export is called with `{ name, settings, workspace }` and must return a Pi
`ToolDefinition` whose name matches the normalized `tools.definitions` key.
Tool modules are trusted executable code. Their real paths must remain inside
the Fabric workspace, and their names may not replace Pi built-in or extension
tools.

```json
{
  "tools": {
    "definitions": {
      "review_context": {
        "kind": "module",
        "ref": "tools/review-context.ts#createTool",
        "settings": {"format": "brief"}
      }
    },
    "enabled": ["read", "review_context"]
  }
}
```

Pi 0.84.2 requires Node.js 22.19.0 or newer. Install the adapter into the project
that owns the Fabric configuration:

```bash
npm install nemo-fabric-adapters-pi
```

The npm package exports its descriptor as `nemo-fabric-adapters-pi/descriptor`.
Fabric descriptor discovery is path-based, so a project-local installation can
configure:

```yaml
discovery:
  local_paths:
    - ./node_modules/nemo-fabric-adapters-pi/pi.fabric-adapter.json
```

During source development, point `discovery.local_paths` at
`adapters/typescript/pi/pi.fabric-adapter.json` after building the TypeScript
adapter workspace.

The maintained code-review example exercises the controlled Pi profile with an
explicit Fabric skill and only Pi's built-in `read` tool:

```bash
npm run build --prefix adapters/typescript
.venv/bin/python -m examples.code_review_agent --variant pi --plan
```

See the [code-review example](../../../examples/code_review_agent/README.md) for
the live NVIDIA-backed run command. Relay and MCP are not currently supported.
