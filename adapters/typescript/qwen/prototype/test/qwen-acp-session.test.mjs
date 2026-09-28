// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import test from "node:test";

import { QwenAcpSessionFactory } from "../dist/prototype/src/qwen-acp-session.js";

function startInput(config) {
  return {
    agentName: "qwen-acp-test",
    baseDir: process.cwd(),
    config,
    runtimeContext: {
      runtime_id: "qwen-acp-test",
      invocation_id: "qwen-acp-test",
      request_id: "qwen-acp-test",
      artifacts: {},
      environment: {
        environment_id: "local",
        provider: "local",
        ownership: "caller_owned",
        control_location: "external_control",
        workspace: process.cwd(),
        env: { FABRIC_QWEN_TEST_KEY: "local-test-key" },
      },
    },
  };
}

test("rejects per-server MCP tool filters that ACP cannot express", async () => {
  await assert.rejects(
    new QwenAcpSessionFactory().create(startInput({
      models: {
        default: {
          provider: "openai",
          model: "fabric-test-model",
          api_key_env: "FABRIC_QWEN_TEST_KEY",
          base_url: "http://127.0.0.1:9/v1",
        },
      },
      mcp: {
        servers: {
          probe: {
            transport: "stdio",
            url: process.execPath,
            allowed_tools: ["echo"],
          },
        },
      },
    })),
    (error) => error.code === "qwen_acp_mcp_filter_unsupported",
  );
});
