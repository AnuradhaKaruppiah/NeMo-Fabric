// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { AcpProcessRunner } from "../dist/prototype/src/acp-runner.js";

const fixture = fileURLToPath(new URL("./fixture-agent.mjs", import.meta.url));

async function withRunner(operation, options = {}) {
  const cwd = await mkdtemp(join(tmpdir(), "fabric-acp-runner-test-"));
  const runner = await AcpProcessRunner.start({
    command: process.execPath,
    args: [fixture],
    cwd,
    env: { ...process.env, ...(options.env ?? {}) },
    mcpServers: options.mcpServers,
    limits: options.limits,
  });
  try {
    return await operation(runner);
  } finally {
    await runner.close();
    await rm(cwd, { recursive: true, force: true });
  }
}

test("negotiates ACP v1 and keeps current-turn text isolated across a warm session", async () => {
  await withRunner(async (runner) => {
    const first = await runner.prompt("one");
    assert.deepEqual(first, {
      text: "history:one:permission=cancelled",
      stopReason: "end_turn",
      usage: { inputTokens: 1, outputTokens: 2, totalTokens: 3 },
      updateCounts: { agent_message_chunk: 2 },
      permissionRequests: 1,
    });
    const second = await runner.prompt("two");
    assert.equal(second.text, "history:one,two:permission=cancelled");
    assert.deepEqual(second.usage, { inputTokens: 2, outputTokens: 4, totalTokens: 6 });
  }, {
    mcpServers: [{ name: "fixture", command: process.execPath, args: ["--version"], env: [] }],
  });
});

test("routes session cancellation", async () => {
  await withRunner(async (runner) => {
    const prompt = runner.prompt("wait");
    setTimeout(() => {
      void runner.cancel().catch(() => undefined);
    }, 25);
    assert.equal((await prompt).stopReason, "cancelled");
  });
});

test("fails a pending turn when the ACP process exits", async () => {
  await assert.rejects(
    withRunner((runner) => runner.prompt("crash")),
    (error) => error.code === "acp_process_exited",
  );
});

test("bounds incoming ACP protocol frames", async () => {
  const cwd = await mkdtemp(join(tmpdir(), "fabric-acp-runner-frame-test-"));
  try {
    await assert.rejects(
      AcpProcessRunner.start({
        command: process.execPath,
        args: [fixture],
        cwd,
        env: { ...process.env, ACP_FIXTURE_MODE: "oversize" },
        limits: { maxFrameBytes: 256, startTimeoutMs: 2_000 },
      }),
      (error) => ["acp_frame_too_large", "acp_start_failed"].includes(error.code),
    );
  } finally {
    await rm(cwd, { recursive: true, force: true });
  }
});
