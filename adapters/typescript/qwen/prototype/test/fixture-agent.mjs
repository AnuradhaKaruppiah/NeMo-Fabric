// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import * as acp from "@agentclientprotocol/sdk";

if (process.env.ACP_FIXTURE_MODE === "oversize") {
  process.stdin.once("data", () => {
    process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: 0, result: { padding: "x".repeat(4096) } })}\n`);
  });
  process.stdin.resume();
} else {
  const histories = new Map();
  const cancelled = new Set();
  const cancelWaiters = new Map();
  let nextSession = 1;

  const app = acp.agent({ name: "fabric-acp-fixture" })
    .onRequest(acp.methods.agent.initialize, (context) => ({
      protocolVersion: context.params.protocolVersion,
      agentCapabilities: {
        mcpCapabilities: { http: true, sse: true },
        sessionCapabilities: { close: {} },
      },
      agentInfo: { name: "Fabric ACP fixture", version: "1.0.0" },
      authMethods: [],
    }))
    .onRequest(acp.methods.agent.session.new, (context) => {
      for (const server of context.params.mcpServers) {
        if (!("type" in server) && !server.command.startsWith("/")) throw new Error("stdio command must be absolute");
      }
      const sessionId = `fixture-${nextSession++}`;
      histories.set(sessionId, []);
      return { sessionId };
    })
    .onRequest(acp.methods.agent.session.close, () => ({}))
    .onRequest(acp.methods.agent.session.prompt, async (context) => {
      const sessionId = context.params.sessionId;
      const prompt = context.params.prompt
        .filter((part) => part.type === "text")
        .map((part) => part.text)
        .join("");
      if (prompt === "crash") process.exit(7);
      if (prompt === "wait") {
        if (!cancelled.has(sessionId)) await new Promise((resolve) => cancelWaiters.set(sessionId, resolve));
        return { stopReason: "cancelled" };
      }
      const permission = await context.client.request(acp.methods.client.session.requestPermission, {
        sessionId,
        toolCall: { toolCallId: "fixture-tool", title: "Fixture tool" },
        options: [{ optionId: "allow", name: "Allow", kind: "allow_once" }],
      });
      const history = histories.get(sessionId) ?? [];
      history.push(prompt);
      histories.set(sessionId, history);
      const response = `history:${history.join(",")}:permission=${permission.outcome.outcome}`;
      const middle = Math.floor(response.length / 2);
      for (const chunk of [response.slice(0, middle), response.slice(middle)]) {
        await context.client.notify(acp.methods.client.session.update, {
          sessionId,
          update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text: chunk } },
        });
      }
      return {
        stopReason: "end_turn",
        usage: {
          inputTokens: history.length,
          outputTokens: history.length * 2,
          totalTokens: history.length * 3,
        },
      };
    })
    .onNotification(acp.methods.agent.session.cancel, (context) => {
      cancelled.add(context.params.sessionId);
      cancelWaiters.get(context.params.sessionId)?.();
      cancelWaiters.delete(context.params.sessionId);
    });

  const output = new WritableStream({
    write(chunk) {
      return new Promise((resolve, reject) => {
        process.stdout.write(chunk, (error) => error ? reject(error) : resolve());
      });
    },
  });
  const input = new ReadableStream({
    start(controller) {
      process.stdin.on("data", (chunk) => controller.enqueue(new Uint8Array(chunk)));
      process.stdin.on("end", () => controller.close());
      process.stdin.on("error", (error) => controller.error(error));
    },
  });
  app.connect(acp.ndJsonStream(output, input));
  process.stdin.resume();
  await new Promise((resolve, reject) => {
    process.stdin.on("end", resolve);
    process.stdin.on("error", reject);
  });
}
