# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from nemo_fabric.host import cli


def test_serve_remains_the_server_bootstrap():
    assert cli.parse_command(["serve"]) == {
        "interface_version": 3,
        "operation": "serve",
    }


async def test_stop_allows_another_runtime_in_the_sandbox(
    host, host_request, mock_fabric, mock_runtime, second_mock_runtime
):
    mock_fabric.start_runtime.side_effect = [mock_runtime, second_mock_runtime]
    assert (await host.handle(host_request("start")))["status"] == "succeeded"
    assert (await host.handle(host_request("invoke", input={"message": "review"})))[
        "status"
    ] == "succeeded"
    assert (await host.handle(host_request("stop")))["status"] == "succeeded"
    started = await host.handle(host_request("start"))
    assert started["status"] == "succeeded"
    assert started["result"]["runtime_id"] == "runtime-2"
    assert mock_fabric.start_runtime.await_count == 2
    mock_runtime.stop.assert_awaited_once_with()
