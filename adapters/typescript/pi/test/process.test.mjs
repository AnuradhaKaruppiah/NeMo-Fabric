// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";

const [major, minor] = process.versions.node.split(".").map(Number);
const supportsPi = major > 22 || (major === 22 && minor >= 19);

function context(workspace, invocationId) {
  return {
    artifacts: {},
    environment: {
      control_location: "external_control",
      env: { POC_FAKE_KEY: "not-a-real-key" },
      environment_id: "environment-1",
      ownership: "caller_owned",
      provider: "local",
      workspace,
    },
    invocation_id: invocationId,
    request_id: `request-${invocationId}`,
    runtime_id: "runtime-1",
  };
}

async function exchange(workspace, requests) {
  const childEnv = { ...process.env };
  delete childEnv.NODE_TEST_CONTEXT;
  const child = spawn(process.execPath, [new URL("../dist/cli.js", import.meta.url).pathname], {
    cwd: workspace,
    env: childEnv,
    stdio: ["pipe", "pipe", "pipe"],
  });
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => {
    stdout += chunk;
  });
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  child.stdin.write(`${requests.map((request) => JSON.stringify(request)).join("\n")}\n`);

  const exitCode = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", resolve);
  });
  const responses = stdout.trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
  return { exitCode, responses, stderr, stdout };
}

test(
  "launches the Pi process host and loads an explicit extension tool",
  { skip: supportsPi ? false : "Pi 0.84.2 requires Node 22.19 or newer" },
  async () => {
    const baseDir = await mkdtemp(join(tmpdir(), "fabric-pi-process-"));
    const workspace = join(baseDir, "workspace");
    try {
      await mkdir(join(baseDir, "skills", "code-review"), { recursive: true });
      await mkdir(workspace);
      await writeFile(
        join(baseDir, "skills", "code-review", "SKILL.md"),
        `---
name: code-review
description: Review code for correctness risks.
---

# Code Review

Read the implementation before reporting findings.
`,
        "utf8",
      );
      await writeFile(
        join(workspace, "extension.js"),
        `export default function (pi) {
  console.log("extension-loaded");
  pi.registerTool({
    name: "poc_echo",
    label: "POC Echo",
    description: "Echo text for a process-host smoke test",
    parameters: {
      type: "object",
      properties: { text: { type: "string" } },
      required: ["text"],
      additionalProperties: false
    },
    async execute(_toolCallId, params) {
      return { content: [{ type: "text", text: params.text }], details: {} };
    }
  });
}
`,
        "utf8",
      );

      const start = {
        operation: "start",
        payload: {
          agent_name: "pi-process-test",
          base_dir: baseDir,
          config: {
            harness: { settings: { extensions: ["extension.js"] } },
            models: {
              default: {
                api_key_env: "POC_FAKE_KEY",
                model: "gpt-4.1-mini",
                provider: "openai",
              },
            },
            skills: { paths: ["skills/code-review"] },
            tools: { enabled: ["poc_echo"] },
          },
          runtime_context: context(workspace, "start"),
        },
      };
      const stop = { operation: "stop", payload: { runtime_id: "runtime-1" } };
      const { exitCode, responses, stderr, stdout } = await exchange(workspace, [start, stop]);

      assert.equal(exitCode, 0, stderr);
      assert.equal(responses.length, 2, `stdout:\n${stdout}\nstderr:\n${stderr}`);
      assert.equal(responses[0].operation, "start");
      assert.equal(responses[0].outcome.status, "succeeded");
      assert.equal(responses[1].operation, "stop");
      assert.equal(responses[1].outcome.status, "succeeded");
      assert.match(stderr, /extension-loaded/);
      assert.doesNotMatch(stdout, /extension-loaded/);
    } finally {
      await rm(baseDir, { recursive: true, force: true });
    }
  },
);

test(
  "rejects an explicitly configured missing skill before session creation",
  { skip: supportsPi ? false : "Pi 0.84.2 requires Node 22.19 or newer" },
  async () => {
    const baseDir = await mkdtemp(join(tmpdir(), "fabric-pi-missing-skill-"));
    const workspace = join(baseDir, "workspace");
    try {
      await mkdir(workspace);
      const start = {
        operation: "start",
        payload: {
          agent_name: "pi-missing-skill-test",
          base_dir: baseDir,
          config: {
            models: {
              default: {
                api_key_env: "POC_FAKE_KEY",
                model: "gpt-4.1-mini",
                provider: "openai",
              },
            },
            skills: { paths: ["skills/missing"] },
            tools: { enabled: [] },
          },
          runtime_context: context(workspace, "start"),
        },
      };
      const { exitCode, responses, stderr } = await exchange(workspace, [start]);

      assert.equal(exitCode, 0, stderr);
      assert.equal(responses.length, 1);
      assert.equal(responses[0].operation, "start");
      assert.equal(responses[0].outcome.status, "failed");
      assert.equal(responses[0].outcome.error.code, "pi_skill_not_found");
    } finally {
      await rm(baseDir, { recursive: true, force: true });
    }
  },
);
