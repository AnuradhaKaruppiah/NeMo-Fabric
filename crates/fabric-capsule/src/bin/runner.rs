// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Resident Unix-socket runner for a Fabric capsule.

use std::path::PathBuf;

fn main() {
    let mut args = std::env::args_os().skip(1);
    let first = args.next();
    let socket = match first.as_deref().and_then(|value| value.to_str()) {
        None | Some("serve") => args
            .next()
            .map(PathBuf::from)
            .unwrap_or_else(nemo_fabric_capsule::default_socket_path),
        Some(_) => PathBuf::from(first.expect("first argument")),
    };
    if args.next().is_some() {
        eprintln!("usage: fabric-capsule-runner [serve] [socket]");
        std::process::exit(2);
    }
    if let Err(error) = nemo_fabric_capsule::serve(&socket) {
        eprintln!("fabric capsule runner failed: {error}");
        std::process::exit(1);
    }
}
