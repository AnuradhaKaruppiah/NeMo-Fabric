// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { resolveCustomTools } from "../dist/pi-sdk.js";

test("resolves and executes a workspace TypeScript tool factory", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "fabric-pi-tool-"));
  try {
    await writeFile(
      join(workspace, "echo-tool.ts"),
      `export default function ({ name, settings }: { name: string; settings: { prefix?: string } }) {
  return {
    name,
    label: "Echo",
    description: "Echo configured text",
    parameters: {
      type: "object",
      properties: { text: { type: "string" } },
      required: ["text"],
      additionalProperties: false
    },
    async execute(_toolCallId: string, params: { text: string }) {
      return {
        content: [{ type: "text", text: (settings.prefix ?? "") + params.text }],
        details: {}
      };
    }
  };
}
`,
      "utf8",
    );

    const [tool] = await resolveCustomTools(workspace, {
      echo: {
        kind: "module",
        ref: "echo-tool.ts",
        settings: { prefix: "configured: " },
      },
    });

    assert.equal(tool.name, "echo");
    const result = await tool.execute("call-1", { text: "hello" }, undefined, undefined, undefined);
    assert.equal(result.content[0].text, "configured: hello");
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});

test("rejects custom tools that collide with Pi built-ins", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "fabric-pi-tool-collision-"));
  try {
    await assert.rejects(
      resolveCustomTools(workspace, {
        read: { kind: "module", ref: "unused.js" },
      }),
      (error) => error.code === "pi_tool_collision",
    );
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});

test("requires the factory result name to match the normalized definition name", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "fabric-pi-tool-name-"));
  try {
    await writeFile(
      join(workspace, "wrong-name.js"),
      `export default function () {
  return {
    name: "other",
    label: "Other",
    description: "Wrong name",
    parameters: { type: "object", properties: {} },
    async execute() { return { content: [], details: {} }; }
  };
}
`,
      "utf8",
    );

    await assert.rejects(
      resolveCustomTools(workspace, {
        expected: { kind: "module", ref: "wrong-name.js" },
      }),
      (error) => error.code === "pi_tool_factory_invalid",
    );
  } finally {
    await rm(workspace, { recursive: true, force: true });
  }
});
