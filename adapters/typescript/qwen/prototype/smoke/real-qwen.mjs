// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { QwenAcpSessionFactory } from "../dist/prototype/src/qwen-acp-session.js";

const mcpServerPath = fileURLToPath(new URL("../../test/fixtures/mcp-server.mjs", import.meta.url));
const instruction = "Fabric Qwen ACP instruction probe";

function streamChunk(response, id, delta, finishReason = null, usage) {
  response.write(`data: ${JSON.stringify({
    id,
    object: "chat.completion.chunk",
    created: 0,
    model: "fabric-test-model",
    choices: [{ index: 0, delta, finish_reason: finishReason }],
    ...(usage === undefined ? {} : { usage }),
  })}\n\n`);
}

async function startModelServer() {
  const requests = [];
  const server = createServer(async (request, response) => {
    if (request.method !== "POST") {
      response.writeHead(405).end();
      return;
    }
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    requests.push(body);
    const serialized = JSON.stringify(body.messages);
    const secondTurn = serialized.includes("Which token did I ask");
    const appendTurn = serialized.includes("Check the appended instruction");
    const hasEcho = serialized.includes("echo:hello");
    const hasMcpTool = serialized.includes("mcp__probe__echo");
    response.writeHead(200, { "content-type": "text/event-stream" });
    const id = `chatcmpl-acp-${requests.length}`;
    if (appendTurn) {
      streamChunk(response, id, { role: "assistant", content: "qwen-acp-append-ready" });
      streamChunk(response, id, {}, "stop", { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 });
    } else if (!secondTurn && !hasMcpTool) {
      streamChunk(response, id, {
        role: "assistant",
        tool_calls: [{
          index: 0,
          id: "call-search",
          type: "function",
          function: { name: "tool_search", arguments: JSON.stringify({ query: "select:mcp__probe__echo" }) },
        }],
      });
      streamChunk(response, id, {}, "tool_calls", { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 });
    } else if (!secondTurn && !hasEcho) {
      streamChunk(response, id, {
        role: "assistant",
        tool_calls: [{
          index: 0,
          id: "call-echo",
          type: "function",
          function: {
            name: "tool_call",
            arguments: JSON.stringify({ name: "mcp__probe__echo", arguments: { text: "hello" } }),
          },
        }],
      });
      streamChunk(response, id, {}, "tool_calls", { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 });
    } else {
      streamChunk(response, id, {
        role: "assistant",
        content: secondTurn ? "qwen-acp-turn-2" : "qwen-acp-mcp-ready",
      });
      streamChunk(response, id, {}, "stop", { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 });
    }
    response.end("data: [DONE]\n\n");
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
    requests,
    close: async () => {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

async function startHttpMcpServer() {
  const requests = [];
  const server = createServer(async (request, response) => {
    if (request.method !== "POST") {
      response.writeHead(405).end();
      return;
    }
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    requests.push({ method: payload.method, authorization: request.headers.authorization });
    if (payload.id === undefined) {
      response.writeHead(202).end();
      return;
    }
    const result = payload.method === "initialize"
      ? {
        protocolVersion: payload.params.protocolVersion,
        capabilities: { tools: {} },
        serverInfo: { name: "fabric-qwen-acp-http-test", version: "1.0.0" },
      }
      : payload.method === "tools/list"
        ? {
          tools: [{
            name: "remote_echo",
            description: "Remote echo for the Fabric Qwen ACP test",
            inputSchema: { type: "object", properties: {}, additionalProperties: false },
          }],
        }
        : undefined;
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(
      result === undefined
        ? { jsonrpc: "2.0", id: payload.id, error: { code: -32601, message: "Method not found" } }
        : { jsonrpc: "2.0", id: payload.id, result },
    ));
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  return {
    url: `http://127.0.0.1:${server.address().port}/mcp`,
    requests,
    close: async () => {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

const workspace = await mkdtemp(join(tmpdir(), "nemo-fabric-qwen-acp-smoke-"));
const skill = join(workspace, "fabric-acp-proof");
await mkdir(skill);
await writeFile(
  join(skill, "SKILL.md"),
  "---\nname: fabric-acp-proof\ndescription: ACP projection proof\n---\n\nUse the configured MCP echo tool.\n",
);
const modelServer = await startModelServer();
const httpMcpServer = await startHttpMcpServer();
let session;
try {
  const factory = new QwenAcpSessionFactory({
    startTimeoutMs: 45_000,
    promptTimeoutMs: 45_000,
    stopTimeoutMs: 5_000,
  });
  session = await factory.create({
    agentName: "qwen-acp-smoke",
    baseDir: workspace,
    config: {
      models: {
        default: {
          provider: "openai",
          model: "fabric-test-model",
          api_key_env: "FABRIC_QWEN_TEST_KEY",
          base_url: modelServer.baseUrl,
        },
      },
      instructions: { system: { content: instruction, mode: "replace" } },
      harness: { settings: { permission_mode: "yolo" } },
      tools: { blocked: ["run_shell_command"] },
      skills: { paths: [skill] },
      mcp: {
        servers: {
          probe: { transport: "stdio", url: process.execPath, args: [mcpServerPath] },
          remote: {
            transport: "streamable-http",
            url: httpMcpServer.url,
            custom_headers: { Authorization: "Bearer ${MCP_TOKEN}" },
          },
        },
      },
    },
    runtimeContext: {
      runtime_id: "qwen-acp-smoke",
      invocation_id: "qwen-acp-smoke",
      request_id: "qwen-acp-smoke",
      artifacts: {},
      environment: {
        environment_id: "local",
        provider: "local",
        ownership: "caller_owned",
        control_location: "external_control",
        workspace,
        env: {
          FABRIC_QWEN_TEST_KEY: "local-test-key",
          MCP_TOKEN: "mcp-test-token",
        },
      },
    },
  });
  const first = await session.prompt("Remember token cobalt and use the MCP echo tool.");
  const second = await session.prompt("Which token did I ask you to remember?");
  assert.equal(first.text, "qwen-acp-mcp-ready", JSON.stringify(first));
  assert.equal(second.text, "qwen-acp-turn-2", JSON.stringify(second));
  const serialized = JSON.stringify(modelServer.requests);
  assert.match(serialized, /Fabric Qwen ACP instruction probe/);
  assert.match(serialized, /fabric-acp-proof/);
  assert.match(serialized, /mcp__probe__echo/);
  assert.match(serialized, /echo:hello/);
  assert.ok(modelServer.requests.some((request) => (request.tools ?? []).length > 0));
  const toolNames = modelServer.requests.map((request) =>
    (request.tools ?? []).map((tool) => tool.function?.name).filter(Boolean));
  const promptRequestIndexes = toolNames
    .map((names, index) => names.includes("tool_search") ? index : -1)
    .filter((index) => index >= 0);
  assert.ok(promptRequestIndexes.length > 0);
  assert.ok(
    promptRequestIndexes.every((index) => !toolNames[index].includes("run_shell_command")),
    JSON.stringify(toolNames),
  );
  assert.match(JSON.stringify(modelServer.requests.at(-1).messages), /Remember token cobalt/);
  assert.ok(httpMcpServer.requests.some((request) => request.method === "initialize"));
  assert.ok(httpMcpServer.requests.some((request) => request.method === "tools/list"));
  assert.ok(httpMcpServer.requests.every((request) => request.authorization === "Bearer mcp-test-token"));
  await session.close();
  session = undefined;

  const minimalStartInput = {
    agentName: "qwen-acp-lifecycle",
    baseDir: workspace,
    config: {
      models: {
        default: {
          provider: "openai",
          model: "fabric-test-model",
          api_key_env: "FABRIC_QWEN_TEST_KEY",
          base_url: modelServer.baseUrl,
        },
      },
      harness: { settings: { permission_mode: "yolo" } },
    },
    runtimeContext: {
      runtime_id: "qwen-acp-lifecycle",
      invocation_id: "qwen-acp-lifecycle",
      request_id: "qwen-acp-lifecycle",
      artifacts: {},
      environment: {
        environment_id: "local",
        provider: "local",
        ownership: "caller_owned",
        control_location: "external_control",
        workspace,
        env: { FABRIC_QWEN_TEST_KEY: "local-test-key" },
      },
    },
  };
  const idleSession = await factory.create(minimalStartInput);
  await idleSession.close();

  const appendInstruction = "Fabric Qwen ACP appended instruction probe";
  const appendSession = await factory.create({
    ...minimalStartInput,
    agentName: "qwen-acp-append",
    config: {
      ...minimalStartInput.config,
      instructions: { system: { content: appendInstruction, mode: "append" } },
    },
  });
  const appendTurn = await appendSession.prompt("Check the appended instruction.");
  await appendSession.close();
  assert.equal(appendTurn.text, "qwen-acp-append-ready", JSON.stringify(appendTurn));
  const appendRequest = modelServer.requests.find((request) =>
    JSON.stringify(request.messages).includes("Check the appended instruction"));
  assert.match(String(appendRequest.messages[0].content), /Fabric Qwen ACP appended instruction probe/);
  assert.match(String(appendRequest.messages[0].content), /Qwen Code/);

  let unavailableMcpSession;
  unavailableMcpSession = await factory.create({
    ...minimalStartInput,
    agentName: "qwen-acp-mcp-failure",
    config: {
      ...minimalStartInput.config,
      mcp: {
        servers: {
          unavailable: {
            transport: "stdio",
            url: process.execPath,
            args: ["-e", "process.exit(1)"],
          },
        },
      },
    },
  });
  const unavailableMcpTurn = await unavailableMcpSession.prompt("Use the unavailable MCP server.");
  await unavailableMcpSession.close();
  assert.equal(unavailableMcpTurn.error, "error_mcp_unavailable", JSON.stringify(unavailableMcpTurn));
  process.stdout.write(`${JSON.stringify({
    status: "passed",
    turns: [first, second],
    modelRequests: modelServer.requests.length,
    httpMcpRequests: httpMcpServer.requests.length,
    idleStartStop: "passed",
    appendInstruction: "passed",
    unavailableMcpError: unavailableMcpTurn.error,
  })}\n`);
} finally {
  await session?.close().catch(() => undefined);
  await modelServer.close();
  await httpMcpServer.close();
  await rm(workspace, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
}
