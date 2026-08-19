<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Pi SDK Adapter POC

This package is a proof-of-concept Pi harness adapter for NVIDIA NeMo Fabric.
It embeds the Pi SDK in the adapter's Node process and maps one Fabric
runtime to one in-memory Pi session.

The POC supports:

- One explicit Pi-known model selected from the `default` role or the sole
  configured role
- Runtime API-key credentials named by `models.<role>.api_key_env`
- Optional `models.<role>.base_url`
- Optional replacement system instructions
- Tool allow and block policy
- Explicit local `.ts` or `.js` extension files contained by the Fabric
  workspace
- Ordered plain-text invocations with a `{ "response": "..." }` terminal
  output

Ambient Pi settings, context files, packages, extensions, skills, prompts,
themes, model files, credentials, and session files are disabled. Explicitly
configured extensions are trusted code for this POC; configuration precedence
after extension startup remains an open design decision.

Pi 0.84.2 requires Node.js 22.19.0 or newer. During source development, point
`discovery.local_paths` at `pi.fabric-adapter.json` after building this package.
