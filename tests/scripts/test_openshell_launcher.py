# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise shared launcher wiring without Docker, inference, or Rust builds."""

import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

MOCK_COMMAND = r"""
import json, os, pathlib, socket, sys, time
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ["MOCK_ROOT"])
with (root / "operations.jsonl").open("a") as stream:
    stream.write(json.dumps({"command":name,"args":args,"provider":os.environ.get("NEMO_FABRIC_OPEN_SHELL_PROVIDER")}) + "\n")
if name == "uname":
    print(os.environ.get("MOCK_OS", "Linux") if args == ["-s"] else "x86_64")
elif name == "rustc":
    print("host: x86_64-unknown-linux-gnu")
elif name == "cargo":
    target = root / "target/x86_64-unknown-linux-gnu"
    release = "--release" in args
    for binary in (["fabric-runtime-server", "fabric-runtime-ctl"] if release else ["fabric-environment-openshell"]):
        path = target / ("release" if release else "debug") / binary
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("mock binary")
elif name == "docker":
    if args[0] == "info":
        print(os.environ.get("MOCK_DOCKER_ARCH", "amd64") if "Architecture" in args[-1] else os.environ.get("MOCK_DOCKER_OS", "linux"))
    elif args[:2] == ["image", "inspect"]:
        print("sha256:mock-image")
elif name == "openshell-gateway":
    if "generate-certs" not in args:
        port = int(args[args.index("--port") + 1])
        with socket.socket() as server:
            server.bind(("127.0.0.1", port))
            server.listen()
            while True:
                connection, _ = server.accept()
                connection.close()
elif name == "openshell":
    state = root / "sandbox-state"
    operation = args[args.index("sandbox") + 1]
    if operation == "create":
        sandbox_name = args[args.index("--name") + 1]
        if len(sandbox_name) > 19:
            raise RuntimeError("OpenShell sandbox name exceeds 19 characters")
        state.write_text(sandbox_name)
    elif operation == "delete":
        state.unlink()
    elif operation == "get":
        if not state.exists() or args[args.index("get") + 1] != state.read_text():
            sys.exit(1)
        print('{"id":"mock-resource-id"}')
elif name == "curl":
    raise RuntimeError("tests must use cached release assets")
elif name == "uv":
    if args[0] == "run":
        sys.exit(int(os.environ.get("MOCK_CONSUMER_EXIT", "0")))
"""


@pytest.fixture(name="launcher")
def launcher_fixture(tmp_path):
    shared = tmp_path / "examples/openshell"
    shared.mkdir(parents=True)
    for filename in ["run-demo.sh", "runtime-image.dockerignore"]:
        shutil.copyfile(ROOT / "examples/openshell" / filename, shared / filename)
    for path in ["adapter-contract/python", "adapters/python/common"]:
        tmp_path.joinpath(path).mkdir(parents=True)
    command = f"#!{sys.executable}\n" + MOCK_COMMAND
    mock_bin = tmp_path / "mock-bin"
    mock_bin.mkdir()
    for name in ["cargo", "curl", "docker", "rustc", "uname", "uv"]:
        path = mock_bin / name
        path.write_text(command)
        path.chmod(0o755)
    cache = tmp_path / ".tmp/openshell-releases/v0.0.116"
    cache.mkdir(parents=True)
    for component, binary, target in [
        ("openshell", "openshell", "musl"),
        ("openshell-gateway", "openshell-gateway", "gnu"),
        ("openshell-sandbox", "openshell-sandbox", "musl"),
    ]:
        asset = cache / f"{component}-x86_64-unknown-linux-{target}.tar.gz"
        content = command.encode()
        with tarfile.open(asset, "w:gz") as archive:
            info = tarfile.TarInfo(binary)
            info.size, info.mode = len(content), 0o755
            archive.addfile(info, io.BytesIO(content))
        cache.joinpath(f"{component}-checksums-sha256.txt").write_text(
            hashlib.sha256(asset.read_bytes()).hexdigest() + "  " + asset.name + "\n"
        )
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
    env = {
        **os.environ,
        "MOCK_ROOT": str(tmp_path),
        "PATH": str(mock_bin) + os.pathsep + os.environ["PATH"],
        "OPENSHELL_EXAMPLE_PORT": str(port),
        "OPENSHELL_EXAMPLE_GATEWAY_START_TIMEOUT": "10",
    }
    return shared / "run-demo.sh", tmp_path, env


@pytest.mark.parametrize("mode", ["deployment", "development", "both"])
def test_third_workload_receives_consumer_arguments_and_cleanup(launcher, mode):
    script, root, env = launcher
    workload = root / "third workload"
    workload.mkdir()
    hooks = workload / "demo.sh"
    hooks.write_text("""WORKLOAD_NAME=third-independent-workload
stage_workload_image() { printf ready >"${STAGE_DIR}/agent.txt"; }
smoke_workload_image() { test -s "${STAGE_DIR}/agent.txt"; }
run_workload_consumer() { uv run --no-sync consumer "$@"; }
collect_workload_diagnostics() { touch "${RESULT_DIR}/diagnostics-called"; }
""")
    completed = subprocess.run(
        [
            "bash",
            str(script),
            str(hooks),
            "--input",
            "words with spaces",
            "--show-output",
        ],
        env={**env, "OPENSHELL_EXAMPLE_MODE": mode},
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    operations = [
        json.loads(line)
        for line in root.joinpath("operations.jsonl").read_text().splitlines()
    ]
    invocations = [
        operation
        for operation in operations
        if operation["command"] == "uv" and operation["args"][0] == "run"
    ]
    assert len(invocations) == (2 if mode == "both" else 1)
    for invocation in invocations:
        assert invocation["args"][-3:] == [
            "--input",
            "words with spaces",
            "--show-output",
        ]
        assert invocation["provider"] == str(
            root / "target/x86_64-unknown-linux-gnu/debug/fabric-environment-openshell"
        )
    assert not root.joinpath("sandbox-state").exists()
    assert not list(root.joinpath(".tmp").glob("openshell-image.*"))
    if mode in {"deployment", "both"}:
        assert root.joinpath(
            ".tmp/third-independent-workload/diagnostics-called"
        ).exists()


@pytest.mark.parametrize(
    "settings,message",
    [
        ({"MOCK_OS": "Darwin"}, "Linux host"),
        ({"MOCK_DOCKER_ARCH": "arm64"}, "same architecture"),
        ({"MOCK_DOCKER_OS": "windows"}, "Linux containers"),
    ],
)
def test_platform_mismatch_fails_before_building_or_provisioning(
    launcher, settings, message
):
    script, root, env = launcher
    completed = subprocess.run(
        ["bash", str(script), str(ROOT / "tests/fixtures/openshell_echo/demo.sh")],
        env={**env, **settings},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 2
    assert message in completed.stderr
    operations = [
        json.loads(line)
        for line in root.joinpath("operations.jsonl").read_text().splitlines()
    ]
    assert not any(
        operation["command"] in {"cargo", "uv", "openshell"} for operation in operations
    )


def test_real_echo_hooks_are_selectable_and_failed_consumer_is_cleaned_up(launcher):
    script, root, env = launcher
    completed = subprocess.run(
        [
            "bash",
            str(script),
            str(ROOT / "tests/fixtures/openshell_echo/demo.sh"),
            "--input",
            "third-agent-input",
        ],
        env={**env, "MOCK_CONSUMER_EXIT": "9"},
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert completed.returncode == 9
    operations = [
        json.loads(line)
        for line in root.joinpath("operations.jsonl").read_text().splitlines()
    ]
    invocation = next(
        operation
        for operation in operations
        if operation["command"] == "uv" and operation["args"][0] == "run"
    )
    assert invocation["args"][-2:] == ["--input", "third-agent-input"]
    assert not root.joinpath("sandbox-state").exists()
