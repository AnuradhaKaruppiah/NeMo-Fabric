// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Internal execution-environment provider boundary.

use std::collections::BTreeMap;
use std::ffi::OsString;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};

use nemo_fabric_capsule::{CapsuleControlRequest, CapsuleControlResponse};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::config::{ControlLocation, EnvironmentOwnership, EnvironmentPlan, RunPlan};
use crate::error::{FabricError, Result};
use crate::runtime::{EnvironmentHandle, absolute_path, new_id, resolve_path};

const OPEN_SHELL_PROVIDER_ID: &str = "openshell";
const OPEN_SHELL_PROVIDER_COMMAND: &str = "fabric-environment-openshell";
const OPEN_SHELL_PROVIDER_COMMAND_ENV: &str = "NEMO_FABRIC_OPEN_SHELL_PROVIDER";
const PROVIDER_PROTOCOL_VERSION: &str = "fabric.environment-provider.v1alpha1";
const MAX_PROVIDER_REQUEST_BYTES: usize = 256 * 1024;
const MAX_PROVIDER_RESPONSE_BYTES: usize = 4 * 1024 * 1024;

/// Internal environment preparation contract.
///
/// This boundary is intentionally crate-private while the first non-local
/// provider proves the lifecycle. It does not yet define a public provider ABI.
pub(crate) trait EnvironmentProvider: Sync {
    /// Resolve or prepare the environment represented by a run plan.
    fn prepare(&self, plan: &RunPlan) -> Result<EnvironmentHandle>;
}

struct LocalEnvironmentProvider;
struct OpenShellEnvironmentProvider;

static LOCAL_ENVIRONMENT_PROVIDER: LocalEnvironmentProvider = LocalEnvironmentProvider;
static OPEN_SHELL_ENVIRONMENT_PROVIDER: OpenShellEnvironmentProvider = OpenShellEnvironmentProvider;

/// Resolve a built-in environment provider by its stable configuration id.
pub(crate) fn resolve_environment_provider(
    provider_id: &str,
) -> Option<&'static dyn EnvironmentProvider> {
    match provider_id {
        "local" => Some(&LOCAL_ENVIRONMENT_PROVIDER),
        OPEN_SHELL_PROVIDER_ID => Some(&OPEN_SHELL_ENVIRONMENT_PROVIDER),
        _ => None,
    }
}

impl EnvironmentProvider for LocalEnvironmentProvider {
    fn prepare(&self, plan: &RunPlan) -> Result<EnvironmentHandle> {
        let mut metadata = BTreeMap::new();
        let mut connection = BTreeMap::new();
        let (
            control_location,
            ownership,
            workspace,
            artifacts,
            environment_env,
            connection_settings,
            environment_metadata,
            settings,
        ) = if let Some(environment) = &plan.environment_plan {
            (
                environment.control_location,
                environment.ownership,
                environment.workspace.clone(),
                environment.artifacts.clone(),
                environment.env.clone(),
                environment.connection.clone(),
                environment.metadata.clone(),
                environment.settings.clone(),
            )
        } else {
            (
                ControlLocation::ExternalControl,
                EnvironmentOwnership::CallerOwned,
                Some(plan.base_dir.clone()),
                plan.config
                    .runtime
                    .artifacts
                    .as_ref()
                    .map(|artifacts| resolve_path(&plan.base_dir, artifacts)),
                BTreeMap::new(),
                serde_json::Map::new(),
                serde_json::Map::new(),
                serde_json::Map::new(),
            )
        };
        let workspace = match workspace {
            Some(path) => Some(absolute_path(path)?),
            None => None,
        };
        connection.extend(connection_settings);
        metadata.extend(settings);
        metadata.extend(environment_metadata);
        Ok(EnvironmentHandle {
            environment_id: new_id("environment"),
            provider: "local".to_string(),
            control_location,
            workspace,
            artifacts,
            env: environment_env,
            ownership,
            connection,
            metadata,
        })
    }
}

impl EnvironmentProvider for OpenShellEnvironmentProvider {
    fn prepare(&self, plan: &RunPlan) -> Result<EnvironmentHandle> {
        let environment =
            plan.environment_plan
                .as_ref()
                .ok_or_else(|| FabricError::InvalidConfig {
                    field: "environment".to_string(),
                    reason: "the openshell provider requires an explicit environment configuration"
                        .to_string(),
                })?;
        validate_open_shell_profile(environment)?;

        let environment_id = new_id("environment");
        let prepared: PreparedEnvironment = self.request(ProviderOperation::Prepare {
            environment_id: &environment_id,
            environment,
        })?;
        let mut metadata = prepared.metadata.into_iter().collect::<BTreeMap<_, _>>();
        metadata.extend(environment.metadata.clone());

        Ok(EnvironmentHandle {
            environment_id,
            provider: OPEN_SHELL_PROVIDER_ID.to_string(),
            control_location: environment.control_location,
            workspace: prepared.workspace,
            artifacts: prepared.artifacts,
            env: environment.env.clone(),
            ownership: environment.ownership,
            connection: prepared.connection.into_iter().collect(),
            metadata,
        })
    }
}

impl OpenShellEnvironmentProvider {
    fn request<T>(&self, operation: ProviderOperation<'_>) -> Result<T>
    where
        T: for<'de> Deserialize<'de>,
    {
        request_provider_process(provider_command(), operation)
    }
}

/// Release or detach an environment through its registered provider.
pub(crate) fn release_environment(
    environment: &EnvironmentHandle,
) -> Result<ProviderReleaseOutput> {
    match environment.provider.as_str() {
        "local" => Ok(ProviderReleaseOutput {
            released: false,
            detached: true,
        }),
        OPEN_SHELL_PROVIDER_ID => {
            OPEN_SHELL_ENVIRONMENT_PROVIDER.request(ProviderOperation::Release { environment })
        }
        provider => Err(FabricError::UnsupportedEnvironmentProvider {
            provider: provider.to_string(),
            adapter_kind: crate::config::AdapterKind::Process,
        }),
    }
}

/// Send one typed lifecycle operation to the resident controller in an OpenShell capsule.
pub(crate) fn control_capsule(
    environment: &EnvironmentHandle,
    request: &CapsuleControlRequest,
) -> Result<CapsuleControlResponse> {
    match environment.provider.as_str() {
        OPEN_SHELL_PROVIDER_ID => {
            OPEN_SHELL_ENVIRONMENT_PROVIDER.request(ProviderOperation::CapsuleControl {
                environment,
                request,
            })
        }
        provider => Err(FabricError::UnsupportedEnvironmentProvider {
            provider: provider.to_string(),
            adapter_kind: crate::config::AdapterKind::Process,
        }),
    }
}

fn validate_open_shell_profile(environment: &EnvironmentPlan) -> Result<()> {
    if environment.control_location != ControlLocation::InEnvControl {
        return Err(FabricError::InvalidConfig {
            field: "environment.control_location".to_string(),
            reason: "the experimental openshell provider supports only `in_env_control`"
                .to_string(),
        });
    }
    if environment.ownership != EnvironmentOwnership::FabricOwned {
        return Err(FabricError::InvalidConfig {
            field: "environment.ownership".to_string(),
            reason: "the experimental openshell provider supports only `fabric_owned`".to_string(),
        });
    }
    Ok(())
}

fn provider_command() -> OsString {
    std::env::var_os(OPEN_SHELL_PROVIDER_COMMAND_ENV)
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| OsString::from(OPEN_SHELL_PROVIDER_COMMAND))
}

fn request_provider_process<T>(command: OsString, operation: ProviderOperation<'_>) -> Result<T>
where
    T: for<'de> Deserialize<'de>,
{
    let operation_name = operation.name();
    let request_id = new_id("environment-provider-request");
    let request = ProviderRequest {
        protocol_version: PROVIDER_PROTOCOL_VERSION,
        request_id: &request_id,
        operation,
    };
    let mut encoded = serde_json::to_vec(&request).map_err(FabricError::SerializeJson)?;
    if encoded.len() > MAX_PROVIDER_REQUEST_BYTES {
        return provider_error(
            operation_name,
            "request_too_large",
            format!(
                "request is {} bytes; the limit is {MAX_PROVIDER_REQUEST_BYTES}",
                encoded.len()
            ),
        );
    }
    encoded.push(b'\n');

    let mut child = Command::new(&command)
        .args(["serve", "--stdio"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| FabricError::EnvironmentProviderOperation {
            provider: OPEN_SHELL_PROVIDER_ID.to_string(),
            operation: operation_name.to_string(),
            code: "provider_unavailable".to_string(),
            message: format!("could not start `{}`: {error}", command.to_string_lossy()),
        })?;
    child
        .stdin
        .take()
        .ok_or_else(|| FabricError::EnvironmentProviderOperation {
            provider: OPEN_SHELL_PROVIDER_ID.to_string(),
            operation: operation_name.to_string(),
            code: "provider_protocol_error".to_string(),
            message: "provider stdin was unavailable".to_string(),
        })?
        .write_all(&encoded)
        .map_err(|error| FabricError::EnvironmentProviderOperation {
            provider: OPEN_SHELL_PROVIDER_ID.to_string(),
            operation: operation_name.to_string(),
            code: "provider_protocol_error".to_string(),
            message: format!("could not write provider request: {error}"),
        })?;

    let output =
        child
            .wait_with_output()
            .map_err(|error| FabricError::EnvironmentProviderOperation {
                provider: OPEN_SHELL_PROVIDER_ID.to_string(),
                operation: operation_name.to_string(),
                code: "provider_wait_failed".to_string(),
                message: error.to_string(),
            })?;
    if output.stdout.len() > MAX_PROVIDER_RESPONSE_BYTES {
        return provider_error(
            operation_name,
            "response_too_large",
            format!(
                "response is {} bytes; the limit is {MAX_PROVIDER_RESPONSE_BYTES}",
                output.stdout.len()
            ),
        );
    }
    if !output.status.success() {
        let diagnostics = String::from_utf8_lossy(&output.stderr);
        return provider_error(
            operation_name,
            "provider_exited",
            if diagnostics.trim().is_empty() {
                format!("provider exited with {}", output.status)
            } else {
                format!(
                    "provider exited with {}: {}",
                    output.status,
                    diagnostics.trim()
                )
            },
        );
    }

    let response: ProviderResponse = serde_json::from_slice(&output.stdout).map_err(|error| {
        FabricError::EnvironmentProviderOperation {
            provider: OPEN_SHELL_PROVIDER_ID.to_string(),
            operation: operation_name.to_string(),
            code: "provider_protocol_error".to_string(),
            message: format!("provider returned invalid JSON: {error}"),
        }
    })?;
    if response.protocol_version != PROVIDER_PROTOCOL_VERSION {
        return provider_error(
            operation_name,
            "provider_protocol_mismatch",
            format!(
                "expected `{PROVIDER_PROTOCOL_VERSION}` but received `{}`",
                response.protocol_version
            ),
        );
    }
    if response.request_id != request_id {
        return provider_error(
            operation_name,
            "provider_correlation_mismatch",
            "provider response request id did not match the request".to_string(),
        );
    }
    match response.outcome {
        ProviderOutcome::Succeeded { output } => serde_json::from_value(output).map_err(|error| {
            FabricError::EnvironmentProviderOperation {
                provider: OPEN_SHELL_PROVIDER_ID.to_string(),
                operation: operation_name.to_string(),
                code: "provider_protocol_error".to_string(),
                message: format!("provider output did not match the operation contract: {error}"),
            }
        }),
        ProviderOutcome::Failed { error } => {
            provider_error(operation_name, &error.code, error.message)
        }
    }
}

fn provider_error<T>(operation: &str, code: &str, message: String) -> Result<T> {
    Err(FabricError::EnvironmentProviderOperation {
        provider: OPEN_SHELL_PROVIDER_ID.to_string(),
        operation: operation.to_string(),
        code: code.to_string(),
        message,
    })
}

#[derive(Serialize)]
struct ProviderRequest<'a> {
    protocol_version: &'static str,
    request_id: &'a str,
    #[serde(flatten)]
    operation: ProviderOperation<'a>,
}

#[derive(Serialize)]
#[serde(tag = "operation", rename_all = "snake_case")]
enum ProviderOperation<'a> {
    Prepare {
        environment_id: &'a str,
        environment: &'a EnvironmentPlan,
    },
    CapsuleControl {
        environment: &'a EnvironmentHandle,
        request: &'a CapsuleControlRequest,
    },
    Release {
        environment: &'a EnvironmentHandle,
    },
}

impl ProviderOperation<'_> {
    fn name(&self) -> &'static str {
        match self {
            Self::Prepare { .. } => "prepare",
            Self::CapsuleControl { .. } => "capsule_control",
            Self::Release { .. } => "release",
        }
    }
}

#[derive(Deserialize)]
struct ProviderResponse {
    protocol_version: String,
    request_id: String,
    #[serde(flatten)]
    outcome: ProviderOutcome,
}

#[derive(Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
enum ProviderOutcome {
    Succeeded { output: Value },
    Failed { error: ProviderFailure },
}

#[derive(Deserialize)]
struct ProviderFailure {
    code: String,
    message: String,
}

#[derive(Deserialize)]
struct PreparedEnvironment {
    workspace: Option<PathBuf>,
    artifacts: Option<PathBuf>,
    #[serde(default)]
    connection: serde_json::Map<String, Value>,
    #[serde(default)]
    metadata: serde_json::Map<String, Value>,
}

#[derive(Debug, Deserialize, PartialEq)]
pub(crate) struct ProviderReleaseOutput {
    pub(crate) released: bool,
    pub(crate) detached: bool,
}
