// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { realpath, stat } from "node:fs/promises";
import { isAbsolute, join, relative, resolve, sep } from "node:path";

import { InMemoryCredentialStore } from "@earendil-works/pi-ai";
import {
  createAgentSession,
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
  SettingsManager,
  type AgentSession,
  type ExtensionCommandContextActions,
} from "@earendil-works/pi-coding-agent";
import type { AgentConfig, AgentModelConfig } from "nemo-fabric-adapter-contract";
import { LifecycleError, type AdapterStartInput } from "nemo-fabric-adapters-common";

import type { PiPromptOutcome, PiSessionFactory, PiSessionHandle } from "./runtime.js";

interface PiHarnessSettings {
  extensions: string[];
}

function selectModel(config: AgentConfig): AgentModelConfig {
  const entries = Object.entries(config.models ?? {});
  if (entries.length === 0) {
    throw new LifecycleError("pi_model_required", "The Pi adapter requires one configured model");
  }
  const selected = config.models?.default ?? (entries.length === 1 ? entries[0]?.[1] : undefined);
  if (selected === undefined) {
    throw new LifecycleError(
      "pi_model_ambiguous",
      "Configure a default model role when the Pi adapter receives multiple models",
    );
  }
  return selected;
}

function harnessSettings(config: AgentConfig): PiHarnessSettings {
  const raw = config.harness?.settings;
  const extensions = raw?.extensions;
  if (extensions === undefined) {
    return { extensions: [] };
  }
  if (!Array.isArray(extensions)) {
    throw new LifecycleError("pi_invalid_settings", "Pi extension settings do not match the adapter schema");
  }
  const values: string[] = [];
  for (const entry of extensions) {
    if (typeof entry !== "string") {
      throw new LifecycleError("pi_invalid_settings", "Pi extension settings do not match the adapter schema");
    }
    values.push(entry);
  }
  return { extensions: values };
}

function containedBy(root: string, candidate: string): boolean {
  const path = relative(root, candidate);
  return path === "" || (!path.startsWith(`..${sep}`) && path !== ".." && !isAbsolute(path));
}

async function resolveExtensionPaths(workspace: string, configured: string[]): Promise<string[]> {
  const resolved: string[] = [];
  for (const entry of configured) {
    if (isAbsolute(entry)) {
      throw new LifecycleError("pi_extension_outside_workspace", "Pi POC extension paths must be workspace-relative");
    }
    const candidate = await realpath(resolve(workspace, entry));
    if (!containedBy(workspace, candidate)) {
      throw new LifecycleError("pi_extension_outside_workspace", "Pi extension path resolves outside the workspace");
    }
    const info = await stat(candidate);
    if (!info.isFile() || (!candidate.endsWith(".ts") && !candidate.endsWith(".js"))) {
      throw new LifecycleError("pi_unsupported_extension", "Pi POC extensions must be .ts or .js files");
    }
    resolved.push(candidate);
  }
  return resolved;
}

async function resolveSkillPaths(baseDir: string, configured: string[]): Promise<string[]> {
  const resolved: string[] = [];
  for (const entry of configured) {
    let candidate: string;
    try {
      candidate = await realpath(resolve(baseDir, entry));
    } catch {
      throw new LifecycleError("pi_skill_not_found", "A configured NeMo Fabric skill path does not exist");
    }
    let info;
    try {
      info = await stat(candidate);
    } catch {
      throw new LifecycleError("pi_skill_not_found", "A configured NeMo Fabric skill path does not exist");
    }
    if (!info.isDirectory()) {
      throw new LifecycleError("pi_skill_invalid", "NeMo Fabric skill paths must be directories");
    }
    try {
      if (!(await stat(join(candidate, "SKILL.md"))).isFile()) {
        throw new Error("not a file");
      }
    } catch {
      throw new LifecycleError(
        "pi_skill_invalid",
        "NeMo Fabric skill directories must contain a SKILL.md file",
      );
    }
    resolved.push(candidate);
  }
  return resolved;
}

function credentialValue(input: AdapterStartInput, name: string): string | undefined {
  return input.runtimeContext.environment.env?.[name] ?? process.env[name];
}

function promptText(message: { content?: unknown }): string {
  if (!Array.isArray(message.content)) {
    return "";
  }
  return message.content
    .filter((block): block is { type: "text"; text: string } => {
      return (
        typeof block === "object" &&
        block !== null &&
        "type" in block &&
        block.type === "text" &&
        "text" in block &&
        typeof block.text === "string"
      );
    })
    .map((block) => block.text)
    .join("");
}

function unsupportedSessionAction(name: string): never {
  throw new LifecycleError("pi_unsupported_session_operation", `Pi session operation ${name} is not supported`);
}

class PiSdkSessionHandle implements PiSessionHandle {
  private readonly session: AgentSession;
  private readonly state: { shutdownRequested: boolean };
  private stopped = false;

  constructor(session: AgentSession, state: { shutdownRequested: boolean }) {
    this.session = session;
    this.state = state;
  }

  async prompt(text: string): Promise<PiPromptOutcome> {
    let accepted = false;
    let finalAssistant:
      | { role: "assistant"; content: unknown; stopReason: string; errorMessage?: string }
      | undefined;
    const unsubscribe = this.session.subscribe((event) => {
      if (event.type === "message_end" && event.message.role === "assistant") {
        finalAssistant = event.message;
      }
    });
    try {
      await this.session.prompt(text, {
        expandPromptTemplates: false,
        source: "interactive",
        preflightResult: (result) => {
          accepted = result;
        },
      });
    } finally {
      unsubscribe();
    }
    return {
      accepted,
      text: finalAssistant === undefined ? undefined : promptText(finalAssistant),
      stopReason: finalAssistant?.stopReason,
      errorMessage: finalAssistant?.errorMessage,
      shutdownRequested: this.state.shutdownRequested,
    };
  }

  async stop(): Promise<void> {
    if (this.stopped) {
      return;
    }
    this.stopped = true;
    try {
      await this.session.abort();
      await this.session.extensionRunner.emit({ type: "session_shutdown", reason: "quit" });
    } finally {
      this.session.dispose();
    }
  }
}

export class PiSdkSessionFactory implements PiSessionFactory {
  async create(input: AdapterStartInput): Promise<PiSessionHandle> {
    const workspace = await realpath(resolve(input.runtimeContext.environment.workspace ?? input.baseDir));
    if (!(await stat(workspace)).isDirectory()) {
      throw new LifecycleError("pi_workspace_invalid", "The Fabric runtime workspace must be a directory");
    }
    const selected = selectModel(input.config);
    const apiKeyEnv = selected.api_key_env;
    if (apiKeyEnv === undefined || apiKeyEnv === null || apiKeyEnv.length === 0) {
      throw new LifecycleError("pi_api_key_env_required", "The selected Pi model requires api_key_env");
    }
    const apiKey = credentialValue(input, apiKeyEnv);
    if (apiKey === undefined || apiKey.length === 0) {
      throw new LifecycleError("pi_credential_missing", `Credential environment variable ${apiKeyEnv} is not set`);
    }

    const settings = SettingsManager.inMemory({}, { projectTrusted: false });
    const extensionPaths = await resolveExtensionPaths(workspace, harnessSettings(input.config).extensions);
    const skillPaths = await resolveSkillPaths(input.baseDir, input.config.skills?.paths ?? []);
    const agentDir = join(workspace, ".fabric-pi");
    const resourceLoader = new DefaultResourceLoader({
      cwd: workspace,
      agentDir,
      settingsManager: settings,
      additionalExtensionPaths: extensionPaths,
      additionalSkillPaths: skillPaths,
      noExtensions: true,
      noSkills: true,
      noPromptTemplates: true,
      noThemes: true,
      noContextFiles: true,
      systemPrompt: input.config.instructions?.system?.content,
    });
    await resourceLoader.reload();
    const extensionErrors = resourceLoader.getExtensions().errors;
    if (extensionErrors.length > 0) {
      throw new LifecycleError("pi_extension_load_failed", "One or more configured Pi extensions failed to load", {
        metadata: { count: extensionErrors.length },
      });
    }
    const skillDiagnostics = resourceLoader.getSkills().diagnostics;
    const blockingSkillDiagnostics = skillDiagnostics.filter(
      (diagnostic) => diagnostic.type === "error" || diagnostic.type === "collision",
    );
    for (const diagnostic of skillDiagnostics.filter((entry) => entry.type === "warning")) {
      process.stderr.write(`Pi skill warning: ${diagnostic.message}\n`);
    }
    if (blockingSkillDiagnostics.length > 0) {
      throw new LifecycleError("pi_skill_load_failed", "One or more configured NeMo Fabric skills failed to load", {
        metadata: { count: blockingSkillDiagnostics.length },
      });
    }

    const credentials = new InMemoryCredentialStore();
    const modelRuntime = await ModelRuntime.create({
      credentials,
      modelsPath: null,
      allowModelNetwork: false,
      refreshOnCreate: false,
    });
    await modelRuntime.setRuntimeApiKey(selected.provider, apiKey);
    const catalogModel = modelRuntime.getModel(selected.provider, selected.model);
    if (catalogModel === undefined) {
      throw new LifecycleError("pi_model_unknown", "The selected provider and model are not present in Pi's catalog");
    }
    const model = selected.base_url ? { ...catalogModel, baseUrl: selected.base_url } : catalogModel;
    const enabled = input.config.tools?.enabled;
    const blocked = input.config.tools?.blocked ?? [];
    const state = { shutdownRequested: false };
    const { session } = await createAgentSession({
      cwd: workspace,
      agentDir,
      model,
      modelRuntime,
      resourceLoader,
      sessionManager: SessionManager.inMemory(workspace),
      settingsManager: settings,
      tools: enabled === null ? undefined : enabled,
      excludeTools: blocked,
    });
    const handle = new PiSdkSessionHandle(session, state);
    try {
      const blockedNames = new Set(blocked);
      const availableNames = new Set(session.getAllTools().map((tool) => tool.name));
      const missing = (enabled ?? []).filter((name) => !blockedNames.has(name) && !availableNames.has(name));
      if (missing.length > 0) {
        throw new LifecycleError("pi_tool_missing", "One or more enabled tools are not registered", {
          metadata: { tools: missing },
        });
      }

      const commandContextActions: ExtensionCommandContextActions = {
        waitForIdle: () => session.waitForIdle(),
        newSession: async () => unsupportedSessionAction("newSession"),
        fork: async () => unsupportedSessionAction("fork"),
        navigateTree: async () => unsupportedSessionAction("navigateTree"),
        switchSession: async () => unsupportedSessionAction("switchSession"),
        reload: async () => unsupportedSessionAction("reload"),
      };
      await session.bindExtensions({
        mode: "print",
        commandContextActions,
        abortHandler: () => {
          void session.abort();
        },
        shutdownHandler: () => {
          state.shutdownRequested = true;
          void session.abort();
        },
        onError: () => {
          process.stderr.write("Pi extension handler failed\n");
        },
      });
      return handle;
    } catch (error) {
      await handle.stop();
      throw error;
    }
  }
}
