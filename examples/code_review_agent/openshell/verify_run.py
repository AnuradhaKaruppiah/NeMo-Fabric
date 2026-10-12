# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate the live two-turn demo using each harness's available evidence."""

import argparse
import json
from pathlib import Path


def hermes_read_calls(messages):
    calls = {}
    completed = set()
    for message in messages:
        for call in message.get("tool_calls", []):
            function = call["function"]
            if function["name"] == "read_file":
                arguments = function["arguments"]
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                calls[call["id"]] = arguments["path"]
        if message.get("role") == "tool":
            content = message.get("content", "")
            try:
                result = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                result = None
            if (
                isinstance(result, dict)
                and isinstance(result.get("content"), str)
                and not result.get("error")
                and not result.get("not_found")
                and result.get("success") is not False
            ):
                completed.add(message["tool_call_id"])
    return [path for identifier, path in calls.items() if identifier in completed]


def verify_results(results, evidence, *, harness, label):
    if len(results) != 2 or any(r["status"] != "succeeded" for r in results):
        raise ValueError("both review turns must succeed")
    if results[0]["runtime_id"] != results[1]["runtime_id"]:
        raise ValueError("both turns must use one Fabric runtime")
    if "calculator.py" not in results[0]["output"]["response"]:
        raise ValueError("the review must reference calculator.py")
    if label not in results[1]["output"]["response"]:
        raise ValueError("conversation continuity failed")
    roles = [p["role"] for p in evidence["processes"].values()]
    if roles.count("adapter") != 1:
        raise ValueError("the adapter process must persist across both turns")
    if harness == "openclaw":
        if roles.count("openclaw_gateway") != 1:
            raise ValueError("the OpenClaw Gateway process must persist")
        if not any(count == 2 for count in evidence["user_turns_by_session"].values()):
            raise ValueError("one OpenClaw session must contain both turns")
        paths = [read["path"] for read in evidence["read_calls"]]
    else:
        paths = hermes_read_calls(results[0]["output"]["messages"])
        if results[0]["output"]["hermes_home"] != results[1]["output"]["hermes_home"]:
            raise ValueError("Hermes state must remain in the same task runtime")
    if not any(path.endswith("calculator.py") for path in paths):
        raise ValueError("the harness must read calculator.py")
    return {
        "harness": harness,
        "runtime_id": results[0]["runtime_id"],
        "label_recalled": True,
        "file_reads": paths,
        "processes": evidence["processes"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=["openclaw", "hermes"], required=True)
    parser.add_argument("--results-file", type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    results = json.loads(args.results_file.read_text())["results"]
    evidence = json.loads(args.results_file.with_name("task-evidence.json").read_text())
    summary = verify_results(results, evidence, harness=args.harness, label=args.label)
    args.results_file.with_name("verification.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(
        f"Verified {args.harness}: two successful turns, label recall, task process persistence, and file reading."
    )


if __name__ == "__main__":
    main()
