// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Typed command-line client for the Fabric capsule runner.

use std::path::PathBuf;

use nemo_fabric_capsule::CapsuleOperation;

fn main() {
    let mut args = std::env::args_os().skip(1);
    let operation = args
        .next()
        .and_then(|value| value.into_string().ok())
        .and_then(|value| value.parse::<CapsuleOperation>().ok())
        .unwrap_or_else(|| {
            eprintln!("usage: fabric-capsule-ctl <start|invoke|stop> [socket]");
            std::process::exit(2);
        });
    let socket = args
        .next()
        .map(PathBuf::from)
        .unwrap_or_else(nemo_fabric_capsule::default_socket_path);
    if args.next().is_some() {
        eprintln!("usage: fabric-capsule-ctl <start|invoke|stop> [socket]");
        std::process::exit(2);
    }
    if let Err(error) = nemo_fabric_capsule::control(
        &socket,
        operation,
        std::io::stdin().lock(),
        std::io::stdout().lock(),
    ) {
        eprintln!("fabric capsule control failed: {error}");
        std::process::exit(1);
    }
}
